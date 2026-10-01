"""Application lifespan (startup / shutdown)."""

import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.state import AppState
from app.meetings.lifecycle import MeetingLifecycleCoordinator
from app.meetings.repository import MeetingHistoryRepository
from app.meetings.service import MeetingHistoryService
from app.services.config_loader import ConfigLoader
from app.services.context_loader import load_context_files
from app.services.stt_controller import SttController

logger = logging.getLogger(__name__)


def create_lifespan(
    *,
    stt_controller: SttController,
    config: ConfigLoader,
    state: AppState,
    history_repository: MeetingHistoryRepository | None = None,
    history_service: MeetingHistoryService | None = None,
    meeting_lifecycle: MeetingLifecycleCoordinator | None = None,
):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ── startup ──────────────────────────────────────────────────────────

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
        logger.info("STT import package: app.stt")
        logger.info(
            "VAD: engine=%s  silero_threshold=%s  webrtc_aggressiveness=%s",
            config.stt_config.vad_engine,
            config.stt_config.vad_sensitivity,
            config.stt_config.vad_aggressiveness,
        )

        if config.stt_backend == "whisper":
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
        elif config.stt_backend == "reazonspeech":
            cfg = config.stt_config
            logger.info(
                "STT: backend=reazonspeech  model=reazonspeech-k2-v2-int8  lang=%s"
                + "  vad_aggressiveness=%s  silence=%ss",
                cfg.language,
                cfg.vad_aggressiveness,
                cfg.silence_duration,
            )
        elif config.stt_backend == "dummy":
            logger.info("STT: backend=dummy  外部サービスなしの軽量 smoke 用バックエンド")
        else:
            logger.info("以前の音声認識設定は利用できません。端末内の方式を選び直してください。")

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

        yield

        # ── shutdown ──────────────────────────────────────────────────────────
        logger.info("シャットダウン開始")

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

        if meeting_lifecycle is not None:
            await meeting_lifecycle.close()

        if history_service is not None:
            await history_service.flush_pending()

        if history_repository is not None:
            await history_repository.close()

        logger.info("シャットダウン完了")

    return lifespan


__all__ = ["create_lifespan"]
