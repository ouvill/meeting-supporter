"""Client-side ACP adapter for reply generation.

This module intentionally drives an external ACP agent process.  It is a
narrow prototype for text-in/text-out reply suggestions, not an ACP server and
not a general tool bridge.
"""

from __future__ import annotations

import asyncio
import logging
from asyncio.subprocess import Process
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Self, override

from acp import PROTOCOL_VERSION, spawn_agent_process, text_block
from acp.client.connection import ClientSideConnection
from acp.schema import (
    CreateElicitationResponse,
    CreateTerminalResponse,
    DeniedOutcome,
    ElicitationMode,
    EnvVariable,
    PermissionOption,
    PromptResponse,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    TerminalOutputResponse,
    ToolCallUpdate,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)

from app.agents.models import ReplyAgentRuntime, ReplyPrompt
from app.core.protocols import StreamLike

logger = logging.getLogger(__name__)

_ACP_START_ERROR = "ACP agent を起動できませんでした。provider.command を確認してください。"
_ACP_RUNTIME_ERROR = "ACP agent との通信に失敗しました。agent が ACP stdio protocol を提供しているか確認してください。"


class _AcpClient:
    """Minimal client callback surface for a reply-only ACP adapter."""

    def __init__(self) -> None:
        self.chunks: asyncio.Queue[str] = asyncio.Queue()

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: object,
    ) -> RequestPermissionResponse:
        _ = (session_id, tool_call, options, kwargs)
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def session_update(self, session_id: str, update: object, **kwargs: object) -> None:
        _ = (session_id, kwargs)
        if getattr(update, "session_update", None) != "agent_message_chunk":
            return
        content = getattr(update, "content", None)
        if getattr(content, "type", None) != "text":
            return
        text = getattr(content, "text", None)
        if isinstance(text, str) and text:
            await self.chunks.put(text)

    async def write_text_file(
        self,
        session_id: str,
        path: str,
        content: str,
        **kwargs: object,
    ) -> WriteTextFileResponse | None:
        _ = (session_id, path, content, kwargs)
        raise PermissionError("ACP reply adapter does not allow file writes")

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: object,
    ) -> ReadTextFileResponse:
        _ = (session_id, path, line, limit, kwargs)
        raise PermissionError("ACP reply adapter does not allow file reads")

    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list[EnvVariable] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **kwargs: object,
    ) -> CreateTerminalResponse:
        _ = (session_id, command, args, env, cwd, output_byte_limit, kwargs)
        raise PermissionError("ACP reply adapter does not allow terminal creation")

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: object) -> TerminalOutputResponse:
        _ = (session_id, terminal_id, kwargs)
        raise PermissionError("ACP reply adapter does not allow terminal access")

    async def release_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: object,
    ) -> ReleaseTerminalResponse | None:
        _ = (session_id, terminal_id, kwargs)
        return None

    async def wait_for_terminal_exit(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: object,
    ) -> WaitForTerminalExitResponse:
        _ = (session_id, terminal_id, kwargs)
        raise PermissionError("ACP reply adapter does not allow terminal access")

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: object) -> None:
        _ = (session_id, terminal_id, kwargs)

    async def create_elicitation(
        self,
        message: str,
        mode: ElicitationMode,
        **kwargs: object,
    ) -> CreateElicitationResponse:
        _ = (message, mode, kwargs)
        raise PermissionError("ACP reply adapter does not allow elicitation")

    async def complete_elicitation(self, elicitation_id: str, **kwargs: object) -> None:
        _ = (elicitation_id, kwargs)

    async def ext_method(self, method: str, params: dict[str, object]) -> dict[str, object]:
        _ = (method, params)
        raise PermissionError("ACP reply adapter does not allow extension methods")

    async def ext_notification(self, method: str, params: dict[str, object]) -> None:
        _ = (method, params)

    def on_connect(self, conn: object) -> None:
        _ = conn


@dataclass
class _AcpReplyStream(StreamLike, AbstractAsyncContextManager["_AcpReplyStream"]):
    command: Sequence[str]
    prompt: str
    cwd: Path
    env: Mapping[str, str] | None = None
    _client: _AcpClient = field(default_factory=_AcpClient, init=False)
    _context: AbstractAsyncContextManager[tuple[ClientSideConnection, Process]] | None = field(default=None, init=False)
    _conn: ClientSideConnection | None = field(default=None, init=False)
    _session_id: str | None = field(default=None, init=False)
    _prompt_task: asyncio.Task[PromptResponse] | None = field(default=None, init=False)

    @override
    async def __aenter__(self) -> Self:
        if not self.command:
            raise RuntimeError(f"{_ACP_START_ERROR} command が空です。")
        self._context = spawn_agent_process(
            self._client,
            self.command[0],
            *self.command[1:],
            env=dict(self.env) if self.env is not None else None,
            cwd=self.cwd,
        )
        try:
            conn, _process = await self._context.__aenter__()
            self._conn = conn
            _ = await conn.initialize(protocol_version=PROTOCOL_VERSION)
            session = await conn.new_session(cwd=str(self.cwd), mcp_servers=[])
            self._session_id = session.session_id
            self._prompt_task = asyncio.create_task(self._prompt(conn, session.session_id), name="acp-reply-prompt")
        except FileNotFoundError as exc:
            await self._close_context()
            raise RuntimeError(_ACP_START_ERROR) from exc
        except Exception as exc:
            await self._close_context()
            raise RuntimeError(_ACP_RUNTIME_ERROR) from exc
        return self

    async def _prompt(self, conn: ClientSideConnection, session_id: str) -> PromptResponse:
        return await conn.prompt(session_id=session_id, prompt=[text_block(self.prompt)])

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc_type, exc, traceback)
        if self._prompt_task is not None and not self._prompt_task.done():
            await self._request_cancel_and_wait()
        if self._conn is not None and self._session_id is not None:
            try:
                _ = await self._conn.close_session(self._session_id)
            except Exception:
                logger.debug("Failed to close ACP session", exc_info=True)
        await self._close_context()
        return None

    async def _request_cancel_and_wait(self) -> None:
        if self._prompt_task is None:
            return
        if self._conn is not None and self._session_id is not None:
            try:
                await self._conn.cancel(self._session_id)
            except Exception:
                logger.debug("Failed to cancel ACP session", exc_info=True)
        try:
            response = await asyncio.wait_for(asyncio.shield(self._prompt_task), timeout=1.0)
            _ = response.stop_reason
        except TimeoutError:
            _ = self._prompt_task.cancel()
        except Exception:
            logger.debug("ACP prompt task failed after cancel", exc_info=True)

    async def _close_context(self) -> None:
        if self._context is None:
            return
        try:
            _ = await self._context.__aexit__(None, None, None)
        finally:
            self._context = None

    @override
    async def stream_text(self, *, delta: bool) -> AsyncIterator[str]:
        _ = delta
        if self._prompt_task is None:
            raise RuntimeError("ACP reply stream has not been entered")
        while True:
            try:
                yield await asyncio.wait_for(self._client.chunks.get(), timeout=0.1)
            except TimeoutError:
                if self._prompt_task.done():
                    await self._finish_prompt()
                    return

    async def _finish_prompt(self) -> None:
        if self._prompt_task is None:
            return
        try:
            await self._prompt_task
        except Exception as exc:
            raise RuntimeError(_ACP_RUNTIME_ERROR) from exc


@dataclass(frozen=True)
class ACPReplyAgentRuntime(ReplyAgentRuntime):
    """Reply runtime that delegates one prompt to an external ACP agent process."""

    command: Sequence[str]
    cwd: Path
    env: Mapping[str, str] | None = None

    @override
    def run_stream(self, prompt: ReplyPrompt) -> AbstractAsyncContextManager[StreamLike]:
        return _AcpReplyStream(command=self.command, prompt=prompt.text, cwd=self.cwd, env=self.env)


__all__ = ["ACPReplyAgentRuntime"]
