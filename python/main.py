#!/usr/bin/env python3
"""会議支援AI — FastAPI + WebSocket サーバー (Tauri サイドカー版)"""

import asyncio
import logging
import os
import secrets
from functools import partial
from pathlib import Path

import soundcard as sc
from dotenv import load_dotenv
from platformdirs import user_data_path

_ = load_dotenv()

# ── ロギング設定 (他モジュールのインポートより前に行う) ────────────────────────────
# APP_DATA_DIR が確定する前なので platformdirs で仮パスを取得する
_log_dir = (
    Path(os.environ["APP_DATA_DIR"])
    if os.getenv("APP_DATA_DIR")
    else user_data_path("net.ouvill.meeting-supporter", appauthor=False, roaming=True)
)

from app.core.logging_setup import setup_logging

setup_logging(_log_dir, debug=bool(os.getenv("DEBUG")))

logger = logging.getLogger(__name__)

# ── アプリ依存のインポート ─────────────────────────────────────────────────────
from app.agents.codex_app_server import CodexAppServer
from app.agents.codex_runtime import CodexInfoAgentRuntime, CodexMinutesAgentRuntime, CodexReplyAgentRuntime
from app.agents.factory import AgentBundle, AgentRouteError, build_agents
from app.agents.managed_runtime import ManagedReplyAgentRuntime, probe_managed_route_status
from app.agents.models import ReplyAgentDefinition
from app.agents.route_catalog import RouteCatalog
from app.api import ai_runtimes, websocket
from app.audio import AudioPipeline, SoundcardSource
from app.core.config import RouteDefinition
from app.core.event_bus import EventBus
from app.core.events import ConfigChanged
from app.core.protocols import AudioPipelineLike
from app.core.state import AppState
from app.core.types import InputDevice
from app.lifespan import create_lifespan
from app.meetings.lifecycle import MeetingLifecycleCoordinator
from app.meetings.models import Turn, _new_utterance_id
from app.meetings.recording import RecordingService
from app.meetings.service import MeetingHistoryService
from app.meetings.sqlite_repository import SqliteMeetingHistoryRepository
from app.services.broadcast import BroadcastManager
from app.services.config_loader import ConfigLoader
from app.services.context_loader import ensure_default_context_directory, load_context_files
from app.services.conversation_orchestrator import (
    ConversationOrchestrator,
)
from app.services.managed_session import ManagedSessionStore
from app.services.secret_store import create_secret_store
from app.services.settings_store import SettingsStore
from app.services.stt_controller import SttController
from app.services.usage_logger import UsageLogger
from app.services.vosk_model_manager import VoskModelManager
from app.services.whisper_model_manager import WhisperModelManager
from app.stt import SttPipeline, build_pipeline

# ── 設定 ─────────────────────────────────────────────────────────────────────

_user_data_dir = _log_dir
managed_session_store = ManagedSessionStore(os.getenv("MANAGED_SESSION_CAPABILITY") or secrets.token_urlsafe(32))
_user_data_dir.mkdir(parents=True, exist_ok=True)

store = SettingsStore(
    config_path=_user_data_dir / "config.toml",
    default_config_path=Path(__file__).parent / "config.default.toml",
)
secret_store = create_secret_store(_user_data_dir / "secrets.toml")
secret_store.apply_secrets_to_env()

config = ConfigLoader.from_settings_store(store)
ensure_default_context_directory(
    context_dir=config.context_dir,
    user_data_dir=config.user_data_dir,
)

# ── アプリ状態・共有サービス ────────────────────────────────────────────────

state = AppState(config=config, secret_store=secret_store)
broadcast_manager = BroadcastManager()
event_bus = EventBus()
vosk_model_manager = VoskModelManager(
    user_data_dir=_user_data_dir,
    settings_store=store,
    event_bus=event_bus,
)
whisper_model_manager = WhisperModelManager()
usage_logger = UsageLogger(
    config.user_data_dir / "usage.jsonl",
    get_meeting_id=lambda: state.current_session.id if state.current_session is not None else None,
)

# The Codex peer is process-scoped for the lifetime of this application. It is
# deliberately lazy: status and reply calls start the official app-server only
# when the user selects or inspects the experimental route.
codex = CodexAppServer()
managed_route_status = (
    partial(probe_managed_route_status, managed_session_store) if os.getenv("MANAGED_API_BASE_URL") else None
)
codex_route_status = partial(ai_runtimes.probe_codex_route_status, codex)


async def _info_route_ready() -> bool:
    route = await RouteCatalog(
        providers=state.config.providers,
        routes=state.config.routes,
        assignments=state.config.ai_assignments,
        secret_store=secret_store,
        managed_status=managed_route_status,
        codex_status=codex_route_status,
    ).read_assigned_route("info")
    return route is not None and route.readiness == "ready" and route.selectable and "info" in route.capabilities


def _build_managed_reply_runtime(
    _route: RouteDefinition,
    definition: ReplyAgentDefinition,
) -> ManagedReplyAgentRuntime:
    return ManagedReplyAgentRuntime(
        session_store=managed_session_store,
        instruction=definition.instruction,
    )


def _build_codex_reply_runtime(
    route: RouteDefinition,
    _definition: ReplyAgentDefinition,
) -> CodexReplyAgentRuntime:
    model = route.model
    if not model:
        raise AgentRouteError(
            code="CODEX_MODEL_NOT_CONFIGURED",
            message="Codex経路のモデル設定が空です。設定を確認してください。",
        )
    return CodexReplyAgentRuntime(peer=codex, model=model)


def _build_codex_info_runtime(route: RouteDefinition) -> CodexInfoAgentRuntime:
    model = route.model
    if not model:
        raise AgentRouteError(
            code="CODEX_MODEL_NOT_CONFIGURED",
            message="Codex経路のモデル設定が空です。設定を確認してください。",
        )
    return CodexInfoAgentRuntime(peer=codex, model=model)


def _build_codex_minutes_runtime(route: RouteDefinition) -> CodexMinutesAgentRuntime:
    model = route.model
    if not model:
        raise AgentRouteError(
            code="CODEX_MODEL_NOT_CONFIGURED",
            message="Codex経路のモデル設定が空です。設定を確認してください。",
        )
    return CodexMinutesAgentRuntime(peer=codex, model=model)


# ── エージェント ──────────────────────────────────────────────────────────────


async def _replace_ai_note(old_str: str, new_str: str) -> str:
    return await conversation_orchestrator.replace_ai_note(old_str, new_str)


bundle: AgentBundle = build_agents(
    state=state,
    providers=config.providers,
    routes=config.routes,
    assignments=config.ai_assignments,
    secret_store=secret_store,
    context_dir=config.context_dir,
    usage_logger=usage_logger,
    mcp_servers=config.mcp_servers,
    reply_agent_definitions=config.reply_agent_definitions,
    replace_ai_note=_replace_ai_note,
    external_reply_factories={
        "managed": _build_managed_reply_runtime,
        "codex": _build_codex_reply_runtime,
    },
    external_info_factories={"codex": _build_codex_info_runtime},
    external_minutes_factories={"codex": _build_codex_minutes_runtime},
)

# ── デバイス一覧 ──────────────────────────────────────────────────────────────


def get_input_devices() -> list[InputDevice]:
    default_ids: set[str] = set()
    for role in ("other", "self"):
        try:
            source = SoundcardSource(None, role, config.stt_config.sample_rate)
            default_ids.add(str(source.device_id))
        except Exception as error:
            logger.warning("既定音声デバイスの解決に失敗しました (%s): %s", role, error)
    result: list[InputDevice] = []
    for mic in sc.all_microphones(include_loopback=True):
        result.append(
            {
                "index": mic.id,
                "name": mic.name,
                "is_monitor": getattr(mic, "isloopback", False),
                "is_default": str(mic.id) in default_ids,
                "hostapi": "",
                "capture": "soundcard",
            }
        )
    return result


# ── STT ファクトリ ────────────────────────────────────────────────────────────


def _make_audio(device: int | str | None, role: str) -> AudioPipeline:
    cfg = config.stt_config
    source = SoundcardSource(device, role, cfg.sample_rate)
    return AudioPipeline(source, role, broadcast_manager.broadcast)


def _make_stt(audio: AudioPipelineLike, role: str) -> SttPipeline:
    cfg = config.stt_config
    return build_pipeline(
        audio.stt_queue,
        role,
        cfg,
        broadcast_manager.broadcast,
        _handle_speech,
        managed_session_store,
        lambda: state.current_session.id if state.current_session is not None else None,
    )


# ── STT コントローラー ─────────────────────────────────────────────────────────

stt_controller = SttController(
    state=state,
    backend=config.stt_backend,
    make_audio=_make_audio,
    make_stt=_make_stt,
    get_input_devices=get_input_devices,
    broadcast=broadcast_manager.broadcast,
)

# ── 会議履歴 (ADR-003 Phase 1) ───────────────────────────────────────────────

history_repository = SqliteMeetingHistoryRepository(_user_data_dir / "meeting_history.sqlite3")
history_service = MeetingHistoryService(repository=history_repository)

# ── 会話オーケストレーション ──────────────────────────────────────────────────


def _turn_factory(speaker: str, text: str, speaker_id: str | None = None) -> Turn:
    return Turn(id=_new_utterance_id(), speaker=speaker, text=text, speaker_id=speaker_id)


conversation_orchestrator = ConversationOrchestrator(
    state=state,
    broadcast=broadcast_manager.broadcast,
    reply_agents=bundle.reply_agent_specs,
    info_runtime=bundle.info_runtime,
    turn_factory=_turn_factory,
    info_readiness=_info_route_ready,
    info_enabled=config.agent_settings["info_enabled"],
    agent_settings=config.agent_settings,
    history_service=history_service,
    usage_logger=usage_logger,
    usage_budget=config.usage_budget,
)


# ── 録音 (ADR-003 Phase 4) ─────────────────────────────────────────────────────

recording_service = RecordingService(user_data_dir=_user_data_dir)


meeting_lifecycle = MeetingLifecycleCoordinator(
    state=state,
    stt_controller=stt_controller,
    broadcast=broadcast_manager.broadcast,
    history=history_service,
    cancel_replies=conversation_orchestrator.cancel_replies,
    reset_reply_cancel_results=conversation_orchestrator.clear_reply_cancel_results,
    reset_info_note_updater=conversation_orchestrator.reset_info_note_updater,
    recording=recording_service,
    user_data_dir=_user_data_dir,
)


async def _handle_speech(role: str, text: str) -> None:
    await conversation_orchestrator.handle_speech(role, text)


_config_change_lock = asyncio.Lock()


async def _enter_info_runtime(bundle_to_start: AgentBundle) -> None:
    if bundle_to_start.info_runtime is not None:
        _ = await bundle_to_start.info_runtime.__aenter__()


async def _exit_info_runtime(bundle_to_stop: AgentBundle) -> None:
    if bundle_to_stop.info_runtime is not None:
        _ = await bundle_to_stop.info_runtime.__aexit__(None, None, None)


async def _on_config_changed(_event: ConfigChanged) -> None:
    global config, bundle
    async with _config_change_lock:
        old_config = config
        new_config = old_config.reload()
        ensure_default_context_directory(
            context_dir=new_config.context_dir,
            user_data_dir=new_config.user_data_dir,
        )
        context_dir_changed = new_config.context_dir != old_config.context_dir
        composition_changed = (
            new_config.routes != old_config.routes
            or new_config.ai_assignments != old_config.ai_assignments
            or new_config.providers != old_config.providers
            or new_config.ollama_base_url != old_config.ollama_base_url
            or new_config.context_dir != old_config.context_dir
            or new_config.mcp_servers != old_config.mcp_servers
            or new_config.reply_agent_definitions != old_config.reply_agent_definitions
        )

        prepared_bundle: AgentBundle | None = None
        if composition_changed:
            try:
                prepared_bundle = build_agents(
                    state=state,
                    providers=new_config.providers,
                    routes=new_config.routes,
                    assignments=new_config.ai_assignments,
                    secret_store=secret_store,
                    context_dir=new_config.context_dir,
                    usage_logger=usage_logger,
                    mcp_servers=new_config.mcp_servers,
                    reply_agent_definitions=new_config.reply_agent_definitions,
                    replace_ai_note=_replace_ai_note,
                    external_reply_factories={
                        "managed": _build_managed_reply_runtime,
                        "codex": _build_codex_reply_runtime,
                    },
                    external_minutes_factories={"codex": _build_codex_minutes_runtime},
                    external_info_factories={"codex": _build_codex_info_runtime},
                )
                await _enter_info_runtime(prepared_bundle)
            except (ValueError, RuntimeError) as error:
                if prepared_bundle is not None:
                    await _exit_info_runtime(prepared_bundle)
                logger.warning("AI設定の適用に失敗したため、現在の実行構成を維持します: %s", error)
                return

        # Configuration and runtime bundles change together only after the next
        # bundle is fully ready. Codex stays shared; this never creates another
        # app-server process when assignments change.
        config = new_config
        state.config = new_config
        if context_dir_changed:
            state.context_text = load_context_files(new_config.context_dir)
        await stt_controller.on_config_changed(
            old_config,
            new_config,
            audio_lifecycle_lock_held=_event.audio_lifecycle_lock_held,
        )

        if prepared_bundle is not None:
            old_bundle = bundle
            bundle = prepared_bundle
            await conversation_orchestrator.update_agents(
                info_runtime=bundle.info_runtime,
                reply_agent_specs=bundle.reply_agent_specs,
            )
            await _exit_info_runtime(old_bundle)

        await conversation_orchestrator.on_config_changed(new_config)


event_bus.subscribe(ConfigChanged, _on_config_changed)


# ── FastAPI アプリ ─────────────────────────────────────────────────────────────

from app.factory import HttpRouterDependencies, create_app  # noqa: PLC0415 — deferred import for clarity

app = create_app(
    http_dependencies=HttpRouterDependencies(
        get_input_devices=get_input_devices,
        state=state,
        managed_status=managed_route_status,
        settings_store=store,
        settings_event_bus=event_bus,
        codex=codex,
        history_service=history_service,
        user_data_dir=_user_data_dir,
        get_minutes_runtime=lambda: bundle.minutes_runtime,
        vosk_model_manager=vosk_model_manager,
        whisper_model_manager=whisper_model_manager,
        codex_status=codex_route_status,
        managed_session_store=managed_session_store,
    ),
    websocket_router=websocket.create_router(
        state=state,
        broadcast_manager=broadcast_manager,
        stt_controller=stt_controller,
        conversation_orchestrator=conversation_orchestrator,
        get_stt_backend=lambda: config.stt_backend,
        get_input_devices=get_input_devices,
        load_context_files=lambda: load_context_files(config.context_dir),
        meeting_lifecycle=meeting_lifecycle,
    ),
    lifespan=create_lifespan(
        get_bundle=lambda: bundle,
        codex=codex,
        stt_controller=stt_controller,
        config=config,
        state=state,
        vosk_model_manager=vosk_model_manager,
        history_repository=history_repository,
        history_service=history_service,
        meeting_lifecycle=meeting_lifecycle,
    ),
)
