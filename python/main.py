#!/usr/bin/env python3
"""会議支援AI — FastAPI + WebSocket サーバー (Tauri サイドカー版)"""

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
from app.agents.managed_runtime import probe_managed_route_status
from app.api import ai_runtimes, websocket
from app.audio import SoundcardSource
from app.core.event_bus import EventBus
from app.core.events import ConfigChanged
from app.core.state import AppState
from app.core.types import InputDevice
from app.lifespan import create_lifespan
from app.meetings.lifecycle import MeetingLifecycleCoordinator
from app.meetings.models import Turn, _new_utterance_id
from app.meetings.recording import RecordingService
from app.meetings.service import MeetingHistoryService
from app.meetings.sqlite_repository import SqliteMeetingHistoryRepository
from app.runtime_composition import RuntimeCompositionCoordinator
from app.services.broadcast import BroadcastManager
from app.services.config_loader import ConfigLoader
from app.services.context_loader import ensure_default_context_directory, load_context_files
from app.services.conversation_orchestrator import (
    ConversationOrchestrator,
)
from app.services.managed_session import ManagedSessionStore
from app.services.reazonspeech_model_manager import ReazonSpeechModelManager
from app.services.secret_store import create_secret_store
from app.services.settings_store import SettingsStore
from app.services.stt_controller import SttController
from app.services.usage_logger import UsageLogger
from app.services.vosk_model_manager import VoskModelManager
from app.services.whisper_model_manager import WhisperModelManager

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
reazonspeech_model_manager = ReazonSpeechModelManager()
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


async def _replace_ai_note(old_str: str, new_str: str) -> str:
    return await conversation_orchestrator.replace_ai_note(old_str, new_str)


async def _handle_speech(role: str, text: str) -> None:
    await conversation_orchestrator.handle_speech(role, text)


runtime_composition = RuntimeCompositionCoordinator(
    config=config,
    state=state,
    secret_store=secret_store,
    broadcast_manager=broadcast_manager,
    managed_session_store=managed_session_store,
    codex=codex,
    usage_logger=usage_logger,
    replace_ai_note=_replace_ai_note,
    handle_speech=_handle_speech,
    managed_status=managed_route_status,
    codex_status=codex_route_status,
)

# ── デバイス一覧 ──────────────────────────────────────────────────────────────


def get_input_devices() -> list[InputDevice]:
    default_ids: set[str] = set()
    for role in ("other", "self"):
        try:
            source = SoundcardSource(None, role, runtime_composition.config.stt_config.sample_rate)
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


# ── STT コントローラー ─────────────────────────────────────────────────────────

stt_controller = SttController(
    state=state,
    backend=runtime_composition.config.stt_backend,
    make_audio=runtime_composition.make_audio,
    make_stt=runtime_composition.make_stt,
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
    reply_agents=runtime_composition.bundle.reply_agent_specs,
    info_runtime=runtime_composition.bundle.info_runtime,
    turn_factory=_turn_factory,
    info_readiness=runtime_composition.info_route_ready,
    info_enabled=runtime_composition.config.agent_settings["info_enabled"],
    agent_settings=runtime_composition.config.agent_settings,
    history_service=history_service,
    usage_logger=usage_logger,
    usage_budget=runtime_composition.config.usage_budget,
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


event_bus.subscribe(
    ConfigChanged,
    partial(
        runtime_composition.on_config_changed,
        stt_controller=stt_controller,
        conversation_orchestrator=conversation_orchestrator,
    ),
)


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
        get_minutes_runtime=lambda: runtime_composition.bundle.minutes_runtime,
        vosk_model_manager=vosk_model_manager,
        whisper_model_manager=whisper_model_manager,
        reazonspeech_model_manager=reazonspeech_model_manager,
        codex_status=codex_route_status,
        managed_session_store=managed_session_store,
    ),
    websocket_router=websocket.create_router(
        state=state,
        broadcast_manager=broadcast_manager,
        stt_controller=stt_controller,
        conversation_orchestrator=conversation_orchestrator,
        get_stt_backend=lambda: runtime_composition.config.stt_backend,
        get_input_devices=get_input_devices,
        load_context_files=lambda: load_context_files(runtime_composition.config.context_dir),
        meeting_lifecycle=meeting_lifecycle,
    ),
    lifespan=create_lifespan(
        get_bundle=lambda: runtime_composition.bundle,
        codex=codex,
        stt_controller=stt_controller,
        config=runtime_composition.config,
        state=state,
        vosk_model_manager=vosk_model_manager,
        history_repository=history_repository,
        history_service=history_service,
        meeting_lifecycle=meeting_lifecycle,
    ),
)
