"""Deterministic ACP agent used only by the WebDriver desktop suite."""

import asyncio
import sys
from pathlib import Path

from acp import run_agent, update_agent_message_text
from acp.schema import (
    CloseSessionResponse,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
)


class WdioReplyAgent:
    def __init__(self) -> None:
        self._cancelled = asyncio.Event()

    def on_connect(self, conn: object) -> None:
        self._client = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: object | None = None,
        client_info: object | None = None,
        **kwargs: object,
    ) -> InitializeResponse:
        _ = (client_capabilities, client_info, kwargs)
        return InitializeResponse(protocol_version=protocol_version)

    async def new_session(
        self,
        cwd: str,
        additional_directories: object | None = None,
        mcp_servers: object | None = None,
        **kwargs: object,
    ) -> NewSessionResponse:
        _ = (cwd, additional_directories, mcp_servers, kwargs)
        return NewSessionResponse(session_id="wdio-reply-session")

    async def prompt(
        self,
        session_id: str,
        prompt: list[object],
        **kwargs: object,
    ) -> PromptResponse:
        _ = kwargs
        state_path = Path(sys.argv[1])
        invocation = int(state_path.read_text(encoding="utf-8")) if state_path.exists() else 0
        state_path.write_text(str(invocation + 1), encoding="utf-8")

        if invocation == 0:
            await self._cancelled.wait()
            return PromptResponse(stop_reason="cancelled")

        prompt_text = "".join(
            str(getattr(block, "text", ""))
            for block in prompt
            if getattr(block, "type", None) == "text"
        )
        reply = "承知しました。" if "短く、1文で言える形" in prompt_text else "準備できました。進めてください。"
        await self._client.session_update(
            session_id,
            update_agent_message_text(reply),
        )
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs: object) -> None:
        _ = (session_id, kwargs)
        self._cancelled.set()

    async def close_session(self, session_id: str, **kwargs: object) -> CloseSessionResponse:
        _ = (session_id, kwargs)
        return CloseSessionResponse()

    async def load_session(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        raise NotImplementedError

    async def list_sessions(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        raise NotImplementedError

    async def set_session_mode(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        raise NotImplementedError

    async def set_config_option(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        raise NotImplementedError

    async def authenticate(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        raise NotImplementedError

    async def fork_session(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        raise NotImplementedError

    async def resume_session(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        raise NotImplementedError

    async def ext_method(self, method: str, params: object) -> dict[str, object]:
        _ = (method, params)
        return {}

    async def ext_notification(self, method: str, params: object) -> None:
        _ = (method, params)


if __name__ == "__main__":
    asyncio.run(run_agent(WdioReplyAgent()))
