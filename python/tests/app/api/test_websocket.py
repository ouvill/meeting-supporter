"""Tests for WebSocket message dispatch background-task behavior."""

from __future__ import annotations

import asyncio
import os
import unittest
from collections.abc import Callable, Coroutine
from typing import cast
from unittest.mock import patch

from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from app.api import websocket as websocket_api
from app.meetings.lifecycle import MeetingLifecycleCoordinator
from app.services.broadcast import BroadcastManager
from app.services.conversation_orchestrator import ConversationOrchestrator
from app.services.stt_controller import SttController
from tests.app.test_factory import _make_dummy_state  # pyright: ignore[reportPrivateUsage]


class _NoopSttController:
    async def start_level_monitors(self) -> None:
        pass


class _NoopMeetingLifecycle:
    async def start_meeting(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def stop_meeting(self) -> None:
        pass


class _FailingConversationOrchestrator:
    def __init__(self) -> None:
        self.started: asyncio.Event = asyncio.Event()
        self.call: tuple[str, str, str | None] | None = None

    async def handle_speech(self, role: str, text: str, speaker_id: str | None = None) -> None:
        self.call = (role, text, speaker_id)
        self.started.set()
        msg = "manual speech failed"
        raise RuntimeError(msg)


class _FakeWebSocket:
    def __init__(self, incoming: list[dict[str, object]]) -> None:
        self.headers: dict[str, str] = {}
        self._incoming: list[dict[str, object]] = incoming
        self.accepted: bool = False
        self.closed_code: int | None = None
        self.sent: list[dict[str, object]] = []

    async def accept(self, subprotocol: str | None = None) -> None:
        _ = subprotocol
        self.accepted = True

    async def close(self, code: int) -> None:
        self.closed_code = code

    async def send_json(self, data: object) -> None:
        self.sent.append(cast(dict[str, object], data))

    async def receive_json(self) -> object:
        if self._incoming:
            return self._incoming.pop(0)
        raise WebSocketDisconnect(code=1000)


class WebSocketManualSpeechTaskTest(unittest.IsolatedAsyncioTestCase):
    async def test_failing_manual_speech_background_task_logs_exception(self) -> None:
        """Manual speech failures surface through the task done-callback logger."""
        orchestrator = _FailingConversationOrchestrator()
        with patch.dict(os.environ, {"BACKEND_AUTH_TOKEN": ""}):
            router = websocket_api.create_router(
                state=_make_dummy_state(),
                broadcast_manager=BroadcastManager(),
                stt_controller=cast(SttController, cast(object, _NoopSttController())),
                conversation_orchestrator=cast(ConversationOrchestrator, cast(object, orchestrator)),
                get_stt_backend=lambda: "dummy",
                get_input_devices=lambda: [],
                load_context_files=lambda: "",
                meeting_lifecycle=cast(MeetingLifecycleCoordinator, cast(object, _NoopMeetingLifecycle())),
            )

        route = next(route for route in router.routes if getattr(route, "path", None) == "/ws")
        if not isinstance(route, WebSocketRoute):
            self.fail("Expected /ws route to be a WebSocketRoute")
        endpoint = cast(Callable[[_FakeWebSocket], Coroutine[object, object, None]], route.endpoint)
        ws = _FakeWebSocket([{"type": "manual_speech", "text": "  delayed utterance  "}])

        with self.assertLogs("app.api.websocket", level="ERROR") as logs:
            endpoint_task = asyncio.create_task(endpoint(ws))
            await endpoint_task
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        self.assertEqual(("other", "delayed utterance", None), orchestrator.call)
        self.assertTrue(ws.accepted)
        self.assertTrue(
            any("WebSocket background task failed: manual_speech" in line for line in logs.output),
            logs.output,
        )


if __name__ == "__main__":
    _ = unittest.main()
