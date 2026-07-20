"""Tests for app.factory — lightweight app factory and OpenAPI generation helper."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import cast, override

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.agents.codex_app_server import CodexAppServer
from app.agents.models import MinutesAgentRuntime, MinutesPrompt
from app.core.config import AgentSettings, AiRouteAssignments, RouteDefinition, SttConfig
from app.core.event_bus import EventBus
from app.core.protocols import StreamLike
from app.core.state import AppState
from app.factory import HttpRouterDependencies, create_app, create_openapi_app
from app.meetings.history_models import (
    CompletedMeetingStorageRecord,
    MeetingListItemRecord,
    MeetingRecord,
    MeetingTurnRecord,
    RecordingAsset,
    ReplySuggestionRecord,
)
from app.meetings.service import MeetingHistoryService
from app.services.settings_store import SettingsStore
from tests.helpers.api_client import TypedTestClient

# ── Helpers ──────────────────────────────────────────────────────────────────


def _as_string_object_map(value: object) -> dict[str, object]:
    """Validate a JSON-like mapping before inspecting its named entries."""
    if not isinstance(value, dict):
        raise TypeError(f"Expected object, got {type(value).__name__}")
    raw = cast("dict[object, object]", value)
    result: dict[str, object] = {}
    for key, item in raw.items():
        if not isinstance(key, str):
            raise TypeError(f"Expected string object key, got {type(key).__name__}")
        result[key] = item
    return result


def _get_openapi_paths(app: FastAPI) -> dict[str, object]:
    """Extract the validated ``paths`` object from FastAPI's untyped OpenAPI schema."""
    schema: object = app.openapi()
    return _as_string_object_map(_as_string_object_map(schema)["paths"])


def _get_http_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Return a set of ``(path, method)`` tuples for all HTTP (non-WebSocket) routes.

    WebSocket routes (``WebSocketRoute``) are excluded.  FastAPI auto-adds
    ``HEAD`` for every ``GET`` route; we include it so that both sides
    cancel out in the comparison.
    """
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                routes.add((route.path, method))
    return routes


# ── Reusable stub implementations (satisfy MinutesAgentRuntime / StreamLike) ─────────


class _StubStream(StreamLike):
    """Minimal ``StreamLike`` implementation — never actually entered at runtime."""

    async def __aenter__(self) -> _StubStream:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    @override
    async def stream_text(self, *, delta: bool) -> AsyncIterator[str]:
        _ = delta
        for chunk in ():
            yield chunk


class _StubMinutesRuntime:
    """Minimal ``MinutesAgentRuntime`` implementation — no LLM backend."""

    def run_stream(self, prompt: MinutesPrompt) -> _StubStream:
        _ = prompt
        return _StubStream()


class _StubRepo:
    """No-op ``MeetingHistoryRepository`` stub — satisfies Protocol with real types."""

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def create_meeting(self, record: MeetingRecord) -> None:
        _ = record

    async def complete_meeting(
        self,
        meeting_id: str,
        ended_at: datetime,
        duration_seconds: int | None = None,
        ai_note: str = "",
    ) -> None:
        _ = (meeting_id, ended_at, duration_seconds, ai_note)

    async def abort_meeting(
        self,
        meeting_id: str,
        ended_at: datetime,
    ) -> None:
        _ = (meeting_id, ended_at)

    async def get_meeting(self, meeting_id: str) -> MeetingRecord | None:
        _ = meeting_id
        return None

    async def list_meetings(self, *, limit: int = 50, offset: int = 0) -> list[MeetingListItemRecord]:
        _ = (limit, offset)
        return []

    async def count_meetings(self) -> int:
        return 0

    async def update_meeting_title(self, meeting_id: str, title: str) -> int:
        _ = (meeting_id, title)
        return 0

    async def update_meeting_minutes(self, meeting_id: str, minutes: str) -> int:
        _ = (meeting_id, minutes)
        return 0

    async def delete_meeting(self, meeting_id: str) -> None:
        _ = meeting_id

    async def list_completed_meeting_storage_oldest(self) -> list[CompletedMeetingStorageRecord]:
        return []

    async def insert_turn(self, record: MeetingTurnRecord) -> None:
        _ = record

    async def list_turns(self, meeting_id: str) -> list[MeetingTurnRecord]:
        _ = meeting_id
        return []

    async def insert_reply_suggestion(self, record: ReplySuggestionRecord) -> None:
        _ = record

    async def list_reply_suggestions(self, meeting_id: str) -> list[ReplySuggestionRecord]:
        _ = meeting_id
        return []

    async def insert_recording_assets(self, records: list[RecordingAsset]) -> None:
        _ = records

    async def list_recording_assets(self, meeting_id: str) -> list[RecordingAsset]:
        _ = meeting_id
        return []

    async def get_recording_asset_by_role(self, meeting_id: str, role: str) -> RecordingAsset | None:
        _ = (meeting_id, role)
        return None


def _make_dummy_minutes_runtime() -> MinutesAgentRuntime:
    """Return a minimal stub implementing the ``MinutesAgentRuntime`` protocol."""
    return _StubMinutesRuntime()


def _make_dummy_history_service() -> MeetingHistoryService:
    """Return a minimal ``MeetingHistoryService`` with a no-op repository."""
    return MeetingHistoryService(repository=_StubRepo())


def _make_dummy_settings_store() -> SettingsStore:
    return SettingsStore(
        config_path=Path("/nonexistent-test") / "config.toml",
        default_config_path=Path("/nonexistent-test") / "default.toml",
    )


def _make_dummy_state() -> AppState:
    """Create a minimal AppState with stub config for router tests."""
    from app.services.config_loader import ConfigLoader
    from app.services.secret_store import FileSecretStore

    dummy_store = _make_dummy_settings_store()
    dummy_config = ConfigLoader(
        settings_store=dummy_store,
        user_data_dir=Path("/tmp"),
        context_dir=Path("/tmp"),
        ollama_base_url="http://localhost:11434/v1",
        stt_backend="dummy",
        stt_config=SttConfig(
            backend="dummy",
            whisper_model="base",
            deepgram_model="nova-3",
            language="ja",
            vad_sensitivity=0.4,
            silence_duration=0.8,
            vad_aggressiveness=2,
            device="auto",
            remote_url="ws://localhost:8001/ws/stt",
            remote_token="",
            sample_rate=16000,
            chunk_size=1600,
        ),
        audio_sample_rate=16000,
        audio_max_session_seconds=55,
        audio_chunk_size=1600,
        agent_settings=AgentSettings(reply_enabled=False, reply_auto_generate=False, info_enabled=False),
        reply_agent_definitions=[],
        mcp_servers=[],
        providers=[],
        routes=[
            RouteDefinition(id="managed", runtime="managed"),
            RouteDefinition(id="codex", runtime="codex-app-server"),
            RouteDefinition(id="acp", runtime="acp"),
        ],
        ai_assignments=AiRouteAssignments(),
    )
    secret_store = FileSecretStore(path=Path("/nonexistent-test") / "secrets.toml")
    return AppState(config=dummy_config, secret_store=secret_store)


def _make_http_dependencies() -> HttpRouterDependencies:
    """Build inert dependencies for inspecting the production HTTP contract."""
    minutes_runtime = _make_dummy_minutes_runtime()
    return HttpRouterDependencies(
        get_input_devices=lambda: [],
        state=_make_dummy_state(),
        settings_store=_make_dummy_settings_store(),
        settings_event_bus=EventBus(),
        codex=CodexAppServer(),
        history_service=_make_dummy_history_service(),
        user_data_dir=Path("/tmp"),
        get_minutes_runtime=lambda: minutes_runtime,
    )


# ── create_app tests ────────────────────────────────────────────────────────


def test_create_app_cors_middleware() -> None:
    """The production HTTP composition permits the desktop app's CORS preflight."""
    app = create_app(http_dependencies=_make_http_dependencies())
    # Check that CORS headers are present on a response
    with TypedTestClient(app) as client:
        resp = client.options(
            "/dummy",
            headers={
                "Origin": "tauri://localhost",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "tauri://localhost"


# ── create_openapi_app tests ────────────────────────────────────────────────


def test_create_openapi_app_has_exact_canonical_http_paths() -> None:
    """OpenAPI exposes exactly the supported HTTP path contract, including canonical minutes."""
    paths = _get_openapi_paths(create_openapi_app())

    assert set(paths) == {
        "/",
        "/health",
        "/devices",
        "/api/settings",
        "/api/settings/ollama/models",
        "/api/settings/connections/test",
        "/api/stt/model",
        "/api/stt/model/download",
        "/api/stt/model/cancel",
        "/api/ai/routes",
        "/api/ai/routes/assignments",
        "/api/ai-runtimes/codex/status",
        "/api/ai-runtimes/codex/login",
        "/api/ai-runtimes/codex/login/device-code",
        "/api/ai-runtimes/codex/login/cancel",
        "/api/ai-runtimes/codex/logout",
        "/api/ai-runtimes/codex/rate-limits",
        "/api/ai-runtimes/codex/cancel",
        "/meetings/recordings/cleanup",
        "/meetings/recordings/cleanup/preview",
        "/meetings",
        "/meetings/{meeting_id}",
        "/meetings/{meeting_id}/minutes",
        "/meetings/{meeting_id}/recordings",
        "/meetings/{meeting_id}/recordings/{role}",
    }
    assert "/minutes" not in paths


def test_minutes_openapi_response_is_plain_text_string() -> None:
    """Minutes clients receive a documented plaintext stream rather than a JSON payload."""
    paths = _get_openapi_paths(create_openapi_app())
    minutes_path = _as_string_object_map(paths["/meetings/{meeting_id}/minutes"])
    post_operation = _as_string_object_map(minutes_path["post"])
    responses = _as_string_object_map(post_operation["responses"])
    success_response = _as_string_object_map(responses["200"])
    content = _as_string_object_map(success_response["content"])
    assert content == {"text/plain": {"schema": {"type": "string"}}}


def test_http_routes_match_between_create_app_and_create_openapi_app() -> None:
    """The production and OpenAPI factories must expose the same route contract."""
    prod_app = create_app(http_dependencies=_make_http_dependencies())

    assert _get_http_routes(prod_app) == _get_http_routes(create_openapi_app())
