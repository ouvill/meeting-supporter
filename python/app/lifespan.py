"""Application lifespan (startup / shutdown)."""

import logging
import os
import traceback
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.agents.codex_app_server import CodexAppServer
from app.agents.factory import AgentBundle
from app.core.state import AppState
from app.meetings.lifecycle import MeetingLifecycleCoordinator
from app.meetings.repository import MeetingHistoryRepository
from app.meetings.service import MeetingHistoryService
from app.openapi_utils import write_openapi_json
from app.services.config_loader import ConfigLoader
from app.services.context_loader import load_context_files
from app.services.stt_controller import SttController
from app.services.vosk_model_manager import VoskModelManager

logger = logging.getLogger(__name__)


def create_lifespan(
    *,
    get_bundle: Callable[[], AgentBundle],
    codex: CodexAppServer,
    stt_controller: SttController,
    config: ConfigLoader,
    state: AppState,
    vosk_model_manager: VoskModelManager,
    history_repository: MeetingHistoryRepository | None = None,
    history_service: MeetingHistoryService | None = None,
    meeting_lifecycle: MeetingLifecycleCoordinator | None = None,
):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ── startup ──────────────────────────────────────────────────────────
        startup_bundle = get_bundle()
        if startup_bundle.info_runtime is not None:
            _ = await startup_bundle.info_runtime.__aenter__()

        if history_repository is not None:
            await history_repository.initialize()

        if state.device_other is None:
            logger.info("DEVICE_OTHER 未設定 — デフォルトスピーカーのループバックを使用します")

        cfg_source = (
            "ユーザー設定"
            if config.settings_store.config_path.exists()
            else ("デフォルト設定" if config.settings_store.default_config_path.exists() else "内部デフォルト値")
        )
        logger.info("設定: %s  (%s)", cfg_source, config.user_data_dir)
        logger.info("AI経路 (返答AI): %s", config.ai_assignments.reply or "未割当")
        logger.info("AI経路 (情報AI): %s", config.ai_assignments.info or "未割当")
        logger.info("AI経路 (議事録AI): %s", config.ai_assignments.minutes or "未割当")
        logger.info("STT import package: app.stt")
        logger.info(
            "VAD: engine=%s  silero_threshold=%s  webrtc_aggressiveness=%s",
            config.stt_config.vad_engine,
            config.stt_config.vad_sensitivity,
            config.stt_config.vad_aggressiveness,
        )

        if config.stt_backend == "local":
            logger.warning("backend=local は未対応です。backend=whisper / vosk / remote を使用してください")
        elif config.stt_backend == "dummy":
            logger.info("STT: backend=dummy  外部サービスなしの軽量 smoke 用バックエンド")
        elif config.stt_backend == "remote":
            logger.info(
                "STT: backend=remote  url=%s  auth=%s",
                config.stt_config.remote_url,
                "有効" if config.stt_config.remote_token else "無効",
            )
        elif config.stt_backend == "whisper":
            cfg = config.stt_config
            logger.info(
                "STT: backend=whisper  model=%s  lang=%s  device=%s"
                + "  vad_aggressiveness=%s  silence=%ss"
                + "  hard_min_voiced_ms=%s  soft_min_voiced_ms=%s"
                + "  soft_min_voiced_ratio=%s  soft_no_speech_threshold=%s"
                + "  soft_logprob_threshold=%s",
                cfg.whisper_model,
                cfg.language,
                cfg.device,
                cfg.vad_aggressiveness,
                cfg.silence_duration,
                cfg.hard_min_voiced_ms,
                cfg.soft_min_voiced_ms,
                cfg.soft_min_voiced_ratio,
                cfg.soft_no_speech_threshold,
                cfg.soft_logprob_threshold,
            )
        elif config.stt_backend == "vosk":
            cfg = config.stt_config
            logger.info(
                "STT: backend=vosk  model_path=%s  lang=%s  vad_aggressiveness=%s  silence=%ss",
                cfg.vosk_model_path,
                cfg.language,
                cfg.vad_aggressiveness,
                cfg.silence_duration,
            )
        elif config.stt_backend == "deepgram":
            has_key = bool(os.getenv("DEEPGRAM_API_KEY"))
            cfg = config.stt_config
            logger.info(
                "STT: backend=deepgram  model=%s  lang=%s  vad_aggressiveness=%s  api_key=%s",
                cfg.deepgram_model,
                cfg.language,
                cfg.vad_aggressiveness,
                "有効" if has_key else "未設定 (DEEPGRAM_API_KEY を設定してください)",
            )
        elif config.stt_backend == "openai":
            cfg = config.stt_config
            logger.info(
                "STT: backend=openai  model=%s  lang=%s  api_key=%s",
                cfg.openai_model,
                cfg.language,
                "有効" if os.getenv("OPENAI_API_KEY") else "未設定 (OPENAI_API_KEY を設定してください)",
            )
        elif config.stt_backend == "xai":
            cfg = config.stt_config
            logger.info(
                "STT: backend=xai  lang=%s  api_key=%s",
                cfg.language,
                "有効" if os.getenv("XAI_API_KEY") else "未設定 (XAI_API_KEY を設定してください)",
            )

        ctx_text = load_context_files(config.context_dir)
        state.context_text = ctx_text
        if ctx_text:
            lines = ctx_text.count("\n") + 1
            logger.info("コンテキスト読み込み完了 (%d行) — %s", lines, config.context_dir)
        else:
            logger.info(
                "コンテキストなし — %s に .md ファイルを置くと前提情報として使用されます",
                config.context_dir,
            )

        logger.info("会議支援AI 起動完了")

        # Developer fallback: write OpenAPI schema on DEBUG startup.
        # The canonical generation command is ``npm run generate:openapi``
        # (or equivalently ``cd python && uv run python scripts/generate_openapi.py``).
        if os.getenv("DEBUG"):
            _openapi_path = Path(__file__).parent.parent.parent / "openapi.json"
            _ = write_openapi_json(app, path=_openapi_path)
            logger.debug("OpenAPI schema written to %s", _openapi_path.resolve())

        yield

        # ── shutdown ──────────────────────────────────────────────────────────
        logger.info("シャットダウン開始")
        # Let a final managed-model configuration event finish before STT
        # resources are torn down; its handler may reconfigure the controller.
        await vosk_model_manager.shutdown()

        try:
            if meeting_lifecycle is not None:
                await meeting_lifecycle.stop_meeting()
            else:
                await stt_controller.stop_meeting()
            await stt_controller.shutdown_stt()
            stt_controller.stop_level_monitors()
        except Exception as e:
            logger.error("shutdown cleanup 失敗: %s", e, exc_info=True)
            traceback.print_exc()

        if history_service is not None:
            await history_service.flush_pending()

        if history_repository is not None:
            await history_repository.close()

        active_bundle = get_bundle()
        if active_bundle.info_runtime is not None:
            _ = await active_bundle.info_runtime.__aexit__(None, None, None)
        await codex.close()
        logger.info("シャットダウン完了")

    return lifespan


__all__ = ["create_lifespan"]
