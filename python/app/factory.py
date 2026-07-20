"""FastAPI application factory and canonical HTTP router composition.

This module separates **app construction** (route registration, middleware)
from **runtime wiring** (service instances, audio, LLM, database).
Both production and OpenAPI applications build their HTTP routes through
``create_http_routers()`` so their route contracts cannot diverge.

Usage (production, in ``main.py``)::

    from app.factory import HttpRouterDependencies, create_app

    app = create_app(
        http_dependencies=HttpRouterDependencies(...),
        websocket_router=websocket.create_router(...),
        lifespan=create_lifespan(...),
    )

Usage (OpenAPI schema generation — no runtime deps needed)::

    from app.factory import create_openapi_app

    app = create_openapi_app()
    schema = app.openapi()
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import override

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.agents.codex_app_server import CodexAppServer
from app.agents.models import MinutesAgentRuntime, MinutesPrompt
from app.agents.route_catalog import CodexStatusProvider, ManagedStatusProvider, OllamaStatusProvider
from app.api import ai_runtimes, managed_session, meeting, meeting_history, settings, stt_models, system
from app.core.config import AgentSettings, AiRouteAssignments, RouteDefinition, SttConfig
from app.core.event_bus import EventBus
from app.core.local_auth import get_backend_auth_token, is_bearer_token_authorized, is_origin_allowed
from app.core.protocols import StreamLike
from app.core.state import AppState
from app.core.types import InputDevice
from app.meetings.history_models import (
    CompletedMeetingStorageRecord,
    MeetingListItemRecord,
    MeetingRecord,
    MeetingTurnRecord,
    RecordingAsset,
    ReplySuggestionRecord,
)
from app.meetings.repository import MeetingHistoryRepository
from app.meetings.service import MeetingHistoryService
from app.services.config_loader import ConfigLoader
from app.services.managed_session import ManagedSessionStore
from app.services.secret_store import FileSecretStore
from app.services.settings_store import SettingsStore
from app.services.vosk_model_manager import VoskModelManager
from app.services.whisper_model_manager import WhisperModelManager

logger = logging.getLogger(__name__)

# ── Nonexistent path safe for CI/headless environments ───────────────────────

# A path under /nonexistent-<uuid> that is guaranteed not to exist but is a
# valid absolute path.  No filesystem access occurs during construction of
# stub services (the path is only used if a handler is actually called, which
# does not happen during OpenAPI schema generation).
_NONEXISTENT = "/nonexistent-4a8b3c2d-1e5f-4a7b-9c0d-1e2f3a4b5c6d"

# ── Lifespan helpers ──────────────────────────────────────────────────────────


@asynccontextmanager
async def _noop_lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Default lifespan: no startup / shutdown logic (safe for OpenAPI gen)."""
    yield


# The type accepted by FastAPI for the ``lifespan`` parameter.
_LifespanType = Callable[[FastAPI], AbstractAsyncContextManager[None]] | None


async def _enforce_local_http_auth(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    auth_token: str | None,
) -> Response:
    """Require the Tauri-generated capability token for HTTP routes when configured."""
    if request.method == "OPTIONS" or auth_token is None:
        return await call_next(request)

    if not is_origin_allowed(request.headers.get("origin")):
        return JSONResponse({"detail": "Forbidden origin"}, status_code=status.HTTP_403_FORBIDDEN)

    if not is_bearer_token_authorized(request.headers.get("authorization"), auth_token):
        return JSONResponse({"detail": "Unauthorized"}, status_code=status.HTTP_401_UNAUTHORIZED)

    return await call_next(request)


# ── Public API ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class HttpRouterDependencies:
    """Dependencies required by the canonical production HTTP route set."""

    get_input_devices: Callable[[], list[InputDevice]]
    state: AppState
    settings_store: SettingsStore
    settings_event_bus: EventBus
    codex: CodexAppServer
    history_service: MeetingHistoryService
    user_data_dir: Path
    get_minutes_runtime: Callable[[], MinutesAgentRuntime | None]
    managed_status: ManagedStatusProvider | None = None
    codex_status: CodexStatusProvider | None = None
    ollama_status: OllamaStatusProvider | None = None
    vosk_model_manager: VoskModelManager | None = None
    whisper_model_manager: WhisperModelManager | None = None
    managed_session_store: ManagedSessionStore | None = None


def create_http_routers(dependencies: HttpRouterDependencies) -> tuple[APIRouter, ...]:
    """Build every HTTP router from one dependency composition.

    This composition is the single source of truth for both the production app
    and the lightweight OpenAPI app. WebSocket routes are intentionally outside
    it because OpenAPI does not describe them.
    """
    vosk_model_manager = dependencies.vosk_model_manager or VoskModelManager(
        user_data_dir=dependencies.user_data_dir,
        settings_store=dependencies.settings_store,
        event_bus=dependencies.settings_event_bus,
    )
    whisper_model_manager = dependencies.whisper_model_manager or WhisperModelManager()
    managed_session_store = dependencies.managed_session_store or ManagedSessionStore(
        "openapi-session-capability-000000"
    )
    return (
        system.create_router(get_input_devices=dependencies.get_input_devices),
        settings.create_router(
            state=dependencies.state,
            managed_status=dependencies.managed_status,
            store=dependencies.settings_store,
            event_bus=dependencies.settings_event_bus,
            codex_status=dependencies.codex_status,
            ollama_status=dependencies.ollama_status,
        ),
        stt_models.create_router(
            vosk_model_manager=vosk_model_manager,
            whisper_model_manager=whisper_model_manager,
        ),
        ai_runtimes.create_router(codex=dependencies.codex),
        managed_session.create_router(managed_session_store),
        meeting.create_router(
            history_service=dependencies.history_service,
            get_minutes_runtime=dependencies.get_minutes_runtime,
        ),
        meeting_history.create_router(
            history_service=dependencies.history_service,
            user_data_dir=dependencies.user_data_dir,
        ),
    )


def create_app(
    *,
    http_dependencies: HttpRouterDependencies,
    websocket_router: APIRouter | None = None,
    lifespan: _LifespanType = None,
    auth_token: str | None = None,
) -> FastAPI:
    """Build an application with the canonical HTTP routes and middleware.

    ``http_dependencies`` is used exclusively by :func:`create_http_routers`,
    which production and OpenAPI construction both invoke. This prevents either
    application from independently adding, omitting, or differently wiring an
    HTTP route.
    """
    app = FastAPI(lifespan=lifespan if lifespan is not None else _noop_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
        ],
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    resolved_auth_token = auth_token if auth_token is not None else get_backend_auth_token()
    if resolved_auth_token is None:
        logger.warning("BACKEND_AUTH_TOKEN is not set; local backend auth is disabled.")

    @app.middleware("http")
    async def _auth_middleware(  # pyright: ignore[reportUnusedFunction]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        return await _enforce_local_http_auth(request, call_next, resolved_auth_token)

    for router in create_http_routers(http_dependencies):
        app.include_router(router)
    if websocket_router is not None:
        app.include_router(websocket_router)

    return app


# ── OpenAPI generation helper ────────────────────────────────────────────────


def create_openapi_app() -> FastAPI:
    """Build a FastAPI application suitable for OpenAPI schema generation only.

    All dependencies are lightweight stubs — no audio devices, no LLM agents,
    no database connections, no file I/O beyond what the Python standard
    library provides.  Safe to call in CI / headless environments.

    WebSocket routes are **not** included: they are invisible in the OpenAPI
    spec and have heavy dependency requirements.
    """
    # ── Stub dependencies ──────────────────────────────────────────────────
    dummy_store = SettingsStore(
        config_path=Path(_NONEXISTENT) / "config.toml",
        default_config_path=Path(_NONEXISTENT) / "default.toml",
    )
    dummy_stt_config = SttConfig(
        backend="dummy",
        whisper_model="large-v3-turbo",
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
    )
    dummy_config = ConfigLoader(
        settings_store=dummy_store,
        user_data_dir=Path("/tmp"),
        context_dir=Path("/tmp"),
        ollama_base_url="http://localhost:11434/v1",
        stt_backend="dummy",
        stt_config=dummy_stt_config,
        audio_sample_rate=16000,
        audio_max_session_seconds=55,
        audio_chunk_size=1600,
        agent_settings=AgentSettings(
            reply_enabled=False,
            reply_auto_generate=False,
            info_enabled=False,
        ),
        reply_agent_definitions=[],
        mcp_servers=[],
        providers=[],
        routes=[
            RouteDefinition(id="managed", runtime="managed"),
            RouteDefinition(id="codex", runtime="codex-app-server", model="gpt-5.6-luna"),
            RouteDefinition(id="acp", runtime="acp"),
            RouteDefinition(id="ollama", runtime="pydantic-ai", provider_id="ollama", model="qwen3"),
            RouteDefinition(id="gemini", runtime="pydantic-ai", provider_id="gemini", model="gemini-3.1-flash-lite"),
            RouteDefinition(id="openai", runtime="pydantic-ai", provider_id="openai", model="gpt-5.4-mini"),
            RouteDefinition(
                id="anthropic",
                runtime="pydantic-ai",
                provider_id="anthropic",
                model="claude-haiku-4-5-20251001",
            ),
        ],
        ai_assignments=AiRouteAssignments(),
    )
    dummy_event_bus = EventBus()
    dummy_vosk_model_manager = VoskModelManager(
        user_data_dir=Path(_NONEXISTENT),
        settings_store=dummy_store,
        event_bus=dummy_event_bus,
    )
    dummy_whisper_model_manager = WhisperModelManager()
    dummy_secret_store = FileSecretStore(path=Path(_NONEXISTENT) / "secrets.toml")
    dummy_state = AppState(config=dummy_config, secret_store=dummy_secret_store)
    dummy_codex = CodexAppServer()
    dummy_minutes_runtime: MinutesAgentRuntime = _DummyMinutesRuntime()
    dummy_history_repo: MeetingHistoryRepository = _DummyMeetingHistoryRepository()
    dummy_history_service = MeetingHistoryService(repository=dummy_history_repo)

    return create_app(
        http_dependencies=HttpRouterDependencies(
            get_input_devices=_stub_get_input_devices,
            state=dummy_state,
            settings_store=dummy_store,
            settings_event_bus=dummy_event_bus,
            codex=dummy_codex,
            history_service=dummy_history_service,
            user_data_dir=Path("/tmp"),
            get_minutes_runtime=lambda: dummy_minutes_runtime,
            vosk_model_manager=dummy_vosk_model_manager,
            whisper_model_manager=dummy_whisper_model_manager,
        )
    )


# ── Private stubs for OpenAPI generation ────────────────────────────────────


def _stub_get_input_devices() -> list[InputDevice]:
    """Return an empty device list — never called during schema generation."""
    return []


class _DummyStream(StreamLike):
    """Stub ``StreamLike`` — never actually entered during schema generation."""

    async def __aenter__(self) -> _DummyStream:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    @override
    async def stream_text(self, *, delta: bool) -> AsyncIterator[str]:
        """Return an empty async iterator (stub, never actually called at runtime)."""
        _ = delta
        for chunk in ():
            yield chunk


class _DummyMinutesRuntime:
    """Minimal ``MinutesAgentRuntime`` stub — no LLM backend."""

    def run_stream(self, prompt: MinutesPrompt) -> _DummyStream:
        _ = prompt
        return _DummyStream()


class _DummyMeetingHistoryRepository:
    """Minimal ``MeetingHistoryRepository`` stub — no database backend."""

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
