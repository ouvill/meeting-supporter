"""Tests for local HTTP and WebSocket capability-token auth."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.agents.codex_app_server import CodexAppServer
from app.api import websocket as websocket_api
from app.core.event_bus import EventBus
from app.core.types import InputDevice
from app.factory import HttpRouterDependencies, create_app
from app.meetings.lifecycle import MeetingLifecycleCoordinator
from app.meetings.service import MeetingHistoryService
from app.meetings.sqlite_repository import SqliteMeetingHistoryRepository
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


def _no_devices() -> list[InputDevice]:
    return []


def _get(client: TestClient, url: str, headers: dict[str, str] | None = None) -> Response:
    return cast(Response, client.get(url, headers=headers))  # pyright: ignore[reportUnknownMemberType]


def _receive_json_object(ws: WebSocketTestSession) -> dict[str, object]:
    return cast(dict[str, object], ws.receive_json())


def _make_http_app() -> FastAPI:
    state = _make_dummy_state()
    return create_app(
        http_dependencies=HttpRouterDependencies(
            get_input_devices=_no_devices,
            state=state,
            settings_store=state.config.settings_store,
            settings_event_bus=EventBus(),
            codex=CodexAppServer(),
            history_service=MeetingHistoryService(repository=SqliteMeetingHistoryRepository(":memory:")),
            user_data_dir=Path("/tmp"),
            get_minutes_runtime=lambda: None,
        ),
        auth_token="secret-token",
    )


def _make_websocket_app(get_input_devices: object, get_stt_backend: Callable[[], str]) -> FastAPI:
    state = _make_dummy_state()
    router = websocket_api.create_router(
        state=state,
        broadcast_manager=BroadcastManager(),
        stt_controller=cast(SttController, cast(object, _NoopSttController())),
        conversation_orchestrator=cast(ConversationOrchestrator, object()),
        get_stt_backend=get_stt_backend,
        get_input_devices=cast(Callable[[], list[InputDevice]], get_input_devices),
        load_context_files=lambda: "",
        meeting_lifecycle=cast(MeetingLifecycleCoordinator, cast(object, _NoopMeetingLifecycle())),
    )
    app = FastAPI()
    app.include_router(router)
    return app


def test_http_requires_bearer_token_when_auth_is_configured() -> None:
    app = _make_http_app()

    with TestClient(app) as client:
        assert _get(client, "/health").status_code == 401
        assert _get(client, "/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
        resp = _get(client, "/health", headers={"Authorization": "Bearer secret-token"})

    assert resp.status_code == 200


def test_http_rejects_disallowed_origin_even_with_valid_token() -> None:
    app = _make_http_app()

    with TestClient(app) as client:
        resp = _get(
            client,
            "/health",
            headers={"Authorization": "Bearer secret-token", "Origin": "https://evil.example"},
        )

    assert resp.status_code == 403


def test_websocket_requires_auth_subprotocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKEND_AUTH_TOKEN", "secret-token")
    app = _make_websocket_app(_no_devices, lambda: "dummy")

    with TestClient(app) as client:
        try:
            with client.websocket_connect("/ws", headers={"origin": "tauri://localhost"}):
                raise AssertionError("unauthorized websocket unexpectedly connected")
        except WebSocketDisconnect as exc:
            assert exc.code == 1008


def test_websocket_accepts_valid_subprotocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKEND_AUTH_TOKEN", "secret-token")
    app = _make_websocket_app(_no_devices, lambda: "dummy")

    with TestClient(app) as client:
        with client.websocket_connect(
            "/ws",
            headers={"origin": "tauri://localhost"},
            subprotocols=["auth.secret-token"],
        ) as ws:
            assert ws.accepted_subprotocol == "auth.secret-token"
            message = _receive_json_object(ws)
            assert message["type"] == "status"


def test_websocket_reconnect_reports_reloaded_stt_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKEND_AUTH_TOKEN", "secret-token")
    stt_backend = "whisper"

    def get_stt_backend() -> str:
        return stt_backend

    app = _make_websocket_app(_no_devices, get_stt_backend)

    with TestClient(app) as client:
        with client.websocket_connect(
            "/ws",
            headers={"origin": "tauri://localhost"},
            subprotocols=["auth.secret-token"],
        ) as ws:
            initial_stt_state = [_receive_json_object(ws) for _ in range(3)][2]
            assert initial_stt_state["type"] == "stt_state"
            assert initial_stt_state["backend"] == "whisper"

        stt_backend = "dummy"

        with client.websocket_connect(
            "/ws",
            headers={"origin": "tauri://localhost"},
            subprotocols=["auth.secret-token"],
        ) as ws:
            reconnected_stt_state = [_receive_json_object(ws) for _ in range(3)][2]
            assert reconnected_stt_state["type"] == "stt_state"
            assert reconnected_stt_state["backend"] == "dummy"


def test_websocket_device_enumeration_failure_stays_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKEND_AUTH_TOKEN", "secret-token")

    def fail_devices() -> list[InputDevice]:
        raise RuntimeError("device probe failed")

    app = _make_websocket_app(fail_devices, lambda: "dummy")

    with TestClient(app) as client:
        with client.websocket_connect(
            "/ws",
            headers={"origin": "tauri://localhost"},
            subprotocols=["auth.secret-token"],
        ) as ws:
            received = [_receive_json_object(ws) for _ in range(5)]

    assert any(msg.get("type") == "error" for msg in received)
    assert any(msg.get("type") == "devices_list" and msg.get("devices") == [] for msg in received)
