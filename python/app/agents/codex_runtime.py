"""Use-case specific runtimes backed by one shared Codex app-server peer."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from types import TracebackType
from typing import Literal, Self, override

from app.agents.codex_app_server import CodexAppServer, CodexSafeError, CodexTurn
from app.agents.models import (
    InfoAgentRuntime,
    InfoPrompt,
    MinutesAgentRuntime,
    MinutesPrompt,
    ReplyAgentRuntime,
    ReplyPrompt,
)
from app.agents.prompts import CODEX_INFO_INSTRUCTION, MINUTES_INSTRUCTION, REPLY_BASE_INSTRUCTION
from app.core.protocols import StreamLike

logger = logging.getLogger(__name__)


@dataclass
class _CodexStream(StreamLike, AbstractAsyncContextManager["_CodexStream"]):
    peer: CodexAppServer
    prompt: str
    model: str
    instructions: str
    _turn: CodexTurn | None = field(default=None, init=False)
    _protocol_retry_used: bool = field(default=False, init=False)

    async def _start_turn(self) -> CodexTurn:
        try:
            return await self.peer.begin_turn(self.prompt, self.model, instructions=self.instructions)
        except CodexSafeError as error:
            if error.code != "protocol_incompatible" or self._protocol_retry_used:
                raise
            self._protocol_retry_used = True
            logger.warning("Codex protocol mismatch before output; retrying with a fresh app-server process")
            await self.peer.restart()
            return await self.peer.begin_turn(self.prompt, self.model, instructions=self.instructions)

    @override
    async def __aenter__(self) -> Self:
        self._turn = await self._start_turn()
        return self

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc_type, exc, traceback)
        if self._turn is not None and not self._turn.finished:
            await self._turn.interrupt()
        return None

    @override
    async def stream_text(self, *, delta: bool) -> AsyncIterator[str]:
        accumulated = ""
        emitted = False
        while True:
            turn = self._turn
            if turn is None:
                raise RuntimeError("Codex stream has not been entered")
            try:
                async for chunk in turn.deltas():
                    emitted = True
                    if delta:
                        yield chunk
                    else:
                        accumulated += chunk
                        yield accumulated
                return
            except CodexSafeError as error:
                if error.code != "protocol_incompatible" or emitted or self._protocol_retry_used:
                    raise
                self._protocol_retry_used = True
                logger.warning("Codex protocol mismatch before output; retrying with a fresh app-server process")
                await self.peer.restart()
                self._turn = await self.peer.begin_turn(self.prompt, self.model, instructions=self.instructions)


@dataclass(frozen=True)
class CodexReplyAgentRuntime(ReplyAgentRuntime):
    """Reply runtime using ChatGPT subscription auth managed by official Codex."""

    peer: CodexAppServer
    model: str

    @override
    def run_stream(self, prompt: ReplyPrompt) -> AbstractAsyncContextManager[StreamLike]:
        return _CodexStream(
            peer=self.peer,
            prompt=prompt.text,
            model=self.model,
            instructions=REPLY_BASE_INSTRUCTION,
        )


@dataclass(frozen=True)
class CodexInfoAgentRuntime(InfoAgentRuntime):
    """Complete-note info runtime using the isolated Codex turn boundary."""

    peer: CodexAppServer
    model: str

    @property
    @override
    def output_mode(self) -> Literal["complete_note"]:
        return "complete_note"

    @override
    def run_stream(self, prompt: InfoPrompt) -> AbstractAsyncContextManager[StreamLike]:
        return _CodexStream(
            peer=self.peer,
            prompt=prompt.text,
            model=self.model,
            instructions=CODEX_INFO_INSTRUCTION,
        )

    @override
    async def __aenter__(self) -> Self:
        return self

    @override
    async def __aexit__(self, *exc_info: object) -> bool | None:
        _ = exc_info
        return None


@dataclass(frozen=True)
class CodexMinutesAgentRuntime(MinutesAgentRuntime):
    """Post-meeting minutes runtime using the isolated Codex turn boundary."""

    peer: CodexAppServer
    model: str

    @override
    def run_stream(self, prompt: MinutesPrompt) -> AbstractAsyncContextManager[StreamLike]:
        return _CodexStream(
            peer=self.peer,
            prompt=prompt.text,
            model=self.model,
            instructions=MINUTES_INSTRUCTION,
        )


__all__ = ["CodexInfoAgentRuntime", "CodexMinutesAgentRuntime", "CodexReplyAgentRuntime"]
