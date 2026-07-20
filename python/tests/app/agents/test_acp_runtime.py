# pyright: reportExplicitAny=false, reportAny=false, reportUnannotatedClassAttribute=false, reportImplicitStringConcatenation=false, reportUnusedCallResult=false, reportImplicitOverride=false
"""Tests for ACP reply runtime integration through a local stdio agent."""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.agents.models import ReplyPrompt


def _load_acp_runtime() -> tuple[types.ModuleType, type[Any]]:
    for module_name in ("app.agents.acp_runtime", "app.agents.acp", "app.agents.models"):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                continue
            raise
        runtime_cls = getattr(module, "ACPReplyAgentRuntime", None)
        if runtime_cls is not None:
            return module, runtime_cls
    raise AssertionError(
        "ACPReplyAgentRuntime is missing; expected it in app.agents.acp_runtime "
        "(or a re-export from app.agents.acp/app.agents.models)."
    )


def _write_fake_acp_agent(directory: Path) -> Path:
    script = directory / "fake_acp_agent.py"
    script.write_text(
        textwrap.dedent(
            """
            import asyncio
            import os
            import sys

            from acp import run_agent, update_agent_message_text
            from acp.schema import (
                CloseSessionResponse,
                InitializeResponse,
                NewSessionResponse,
                PromptResponse,
            )


            class FakeReplyAgent:
                def on_connect(self, conn):
                    self._client = conn

                async def initialize(self, protocol_version, client_capabilities=None, client_info=None, **kwargs):
                    _ = (client_capabilities, client_info, kwargs)
                    return InitializeResponse(protocol_version=protocol_version)

                async def new_session(self, cwd, additional_directories=None, mcp_servers=None, **kwargs):
                    _ = (cwd, additional_directories, mcp_servers, kwargs)
                    return NewSessionResponse(session_id="fake-session")

                async def prompt(self, session_id, prompt, **kwargs):
                    _ = kwargs
                    text = "".join(block.text for block in prompt if getattr(block, "type", None) == "text")
                    if session_id != "fake-session":
                        raise RuntimeError(f"unexpected session: {session_id}")
                    if text != "hello acp":
                        raise RuntimeError(f"unexpected prompt: {text}")
                    await self._client.session_update(session_id, update_agent_message_text("hello "))
                    await self._client.session_update(
                        session_id, update_agent_message_text(os.environ["ACP_FAKE_SUFFIX"])
                    )
                    return PromptResponse(stop_reason="end_turn")

                async def close_session(self, session_id, **kwargs):
                    _ = (session_id, kwargs)
                    return CloseSessionResponse()

                async def load_session(self, *args, **kwargs):
                    raise NotImplementedError

                async def list_sessions(self, *args, **kwargs):
                    raise NotImplementedError

                async def set_session_mode(self, *args, **kwargs):
                    raise NotImplementedError

                async def set_config_option(self, *args, **kwargs):
                    raise NotImplementedError

                async def authenticate(self, *args, **kwargs):
                    raise NotImplementedError

                async def fork_session(self, *args, **kwargs):
                    raise NotImplementedError

                async def resume_session(self, *args, **kwargs):
                    raise NotImplementedError

                async def cancel(self, *args, **kwargs):
                    return None

                async def ext_method(self, method, params):
                    _ = (method, params)
                    return {}

                async def ext_notification(self, method, params):
                    _ = (method, params)
                    return None


            asyncio.run(run_agent(FakeReplyAgent()))
            """
        ),
        encoding="utf-8",
    )
    return script


def _write_cancellable_acp_agent(directory: Path, marker: Path) -> Path:
    script = directory / "cancellable_acp_agent.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import asyncio
            from pathlib import Path

            from acp import run_agent
            from acp.schema import (
                CloseSessionResponse,
                InitializeResponse,
                NewSessionResponse,
                PromptResponse,
            )


            class CancellableReplyAgent:
                def __init__(self):
                    self._cancelled = asyncio.Event()

                def on_connect(self, conn):
                    self._client = conn

                async def initialize(self, protocol_version, client_capabilities=None, client_info=None, **kwargs):
                    _ = (client_capabilities, client_info, kwargs)
                    return InitializeResponse(protocol_version=protocol_version)

                async def new_session(self, cwd, additional_directories=None, mcp_servers=None, **kwargs):
                    _ = (cwd, additional_directories, mcp_servers, kwargs)
                    return NewSessionResponse(session_id="cancel-session")

                async def prompt(self, session_id, prompt, **kwargs):
                    _ = (session_id, prompt, kwargs)
                    await self._cancelled.wait()
                    return PromptResponse(stop_reason="cancelled")

                async def cancel(self, session_id, **kwargs):
                    _ = (session_id, kwargs)
                    Path({str(marker)!r}).write_text("cancelled", encoding="utf-8")
                    self._cancelled.set()
                    return None

                async def close_session(self, session_id, **kwargs):
                    _ = (session_id, kwargs)
                    return CloseSessionResponse()

                async def load_session(self, *args, **kwargs):
                    raise NotImplementedError

                async def list_sessions(self, *args, **kwargs):
                    raise NotImplementedError

                async def set_session_mode(self, *args, **kwargs):
                    raise NotImplementedError

                async def set_config_option(self, *args, **kwargs):
                    raise NotImplementedError

                async def authenticate(self, *args, **kwargs):
                    raise NotImplementedError

                async def fork_session(self, *args, **kwargs):
                    raise NotImplementedError

                async def resume_session(self, *args, **kwargs):
                    raise NotImplementedError

                async def ext_method(self, method, params):
                    _ = (method, params)
                    return {{}}

                async def ext_notification(self, method, params):
                    _ = (method, params)
                    return None


            asyncio.run(run_agent(CancellableReplyAgent()))
            """
        ),
        encoding="utf-8",
    )
    return script


class ACPReplyAgentRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_streams_text_from_fake_external_acp_agent_process(self) -> None:
        module, runtime_cls = _load_acp_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            fake_agent = _write_fake_acp_agent(Path(tmp))
            spawn_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
            original_spawn = getattr(module, "spawn_agent_process", None)
            if original_spawn is None:
                self.fail(
                    "ACP runtime module must expose/import spawn_agent_process so the stdio process path is testable"
                )

            def recording_spawn(*args: Any, **kwargs: Any) -> Any:
                spawn_calls.append((args, kwargs))
                return original_spawn(*args, **kwargs)

            with patch.object(module, "spawn_agent_process", recording_spawn):
                runtime = runtime_cls(
                    command=[sys.executable, str(fake_agent)],
                    env={"ACP_FAKE_SUFFIX": "world"},
                    cwd=Path(tmp),
                )

                async with runtime.run_stream(ReplyPrompt(text="hello acp")) as stream:
                    chunks = [chunk async for chunk in stream.stream_text(delta=True)]

        self.assertEqual(["hello ", "world"], chunks)
        self.assertEqual(1, len(spawn_calls))
        call_args, call_kwargs = spawn_calls[0]
        self.assertIn(sys.executable, call_args)
        self.assertEqual("world", call_kwargs.get("env", {}).get("ACP_FAKE_SUFFIX"))

    async def test_exit_requests_cancel_and_waits_for_cancelled_stop_reason(self) -> None:
        _, runtime_cls = _load_acp_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            marker = tmp_path / "cancel-marker.txt"
            fake_agent = _write_cancellable_acp_agent(tmp_path, marker)
            runtime = runtime_cls(command=[sys.executable, str(fake_agent)], cwd=tmp_path)

            async with runtime.run_stream(ReplyPrompt(text="please stop")):
                pass

            self.assertEqual("cancelled", marker.read_text(encoding="utf-8"))

    async def test_wdio_fixture_supports_stream_cancellation(self) -> None:
        _, runtime_cls = _load_acp_runtime()
        fixture = Path(__file__).resolve().parents[4] / "test" / "tauri" / "fixtures" / "wdio_acp_agent.py"
        with tempfile.TemporaryDirectory() as tmp:
            invocation_state = Path(tmp) / "invocations"
            runtime = runtime_cls(
                command=[sys.executable, str(fixture), str(invocation_state)],
                cwd=Path(tmp),
            )

            async with runtime.run_stream(ReplyPrompt(text="please stop")) as stream:
                consumer = asyncio.create_task(anext(stream.stream_text(delta=True), None))
                async with asyncio.timeout(2):
                    while not invocation_state.exists():
                        await asyncio.sleep(0.01)

            self.assertIsNone(await asyncio.wait_for(consumer, timeout=2))
            self.assertEqual("1", invocation_state.read_text(encoding="utf-8"))

    async def test_invalid_command_error_does_not_expose_prompt_or_env_secret(self) -> None:
        _, runtime_cls = _load_acp_runtime()
        prompt_text = "prompt contains private customer detail"
        secret_value = "super-secret-acp-token"
        with tempfile.TemporaryDirectory() as tmp:
            runtime = runtime_cls(
                command=["/definitely/not/a/real/acp-agent"],
                env={"ACP_TOKEN": secret_value},
                cwd=Path(tmp),
            )
            logger = logging.getLogger("app.agents")
            records: list[logging.LogRecord] = []

            class _CollectingHandler(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    records.append(record)

            handler = _CollectingHandler()
            logger.addHandler(handler)
            try:
                with self.assertRaises(Exception) as raised:
                    async with runtime.run_stream(ReplyPrompt(text=prompt_text)) as stream:
                        _ = [chunk async for chunk in stream.stream_text(delta=True)]
            finally:
                logger.removeHandler(handler)

        combined_output = f"{raised.exception}\n" + "\n".join(record.getMessage() for record in records)
        self.assertIn("ACP", combined_output)
        self.assertRegex(combined_output, r"(?i)(start|connect|spawn|command)")
        self.assertNotIn(prompt_text, combined_output)
        self.assertNotIn(secret_value, combined_output)


if __name__ == "__main__":
    _ = unittest.main()
