"""SttPipeline: VAD + STT stage chain.

Consumes AudioFrames from AudioPipeline.stt_queue and converts speech to text.
Audio capture and volume monitoring are handled by AudioPipeline (app/audio/).

    stt_queue (from AudioPipeline) → VadStage → Q2 → SttStage
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Callable, Coroutine
from typing import Never

logger = logging.getLogger(__name__)

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import ErrorMsg, OutgoingBroadcastFn
from app.core.pipeline import Pipeline
from app.core.publisher import OutgoingPublisher, ThreadSafePublisher
from app.core.types import HandleSpeechFn
from app.services.managed_session import ManagedSessionStore
from app.stt.stages.stt_deepgram import DeepgramStage
from app.stt.stages.stt_dummy import DummyStage
from app.stt.stages.stt_managed import ManagedSttStage
from app.stt.stages.stt_openai import OpenAIStage
from app.stt.stages.stt_reazonspeech import ReazonSpeechEngine, ReazonSpeechStage
from app.stt.stages.stt_remote import RemoteStage
from app.stt.stages.stt_vosk import VoskEngine, VoskStage
from app.stt.stages.stt_whisper import WhisperEngine, WhisperStage
from app.stt.stages.stt_xai import XaiStage
from app.stt.stages.vad import VadStage, WebRtcVadEngine

_Q2_SIZE = 200


def _make_stt_stage(
    in_q: queue.Queue[AudioFrame | None],
    cfg: SttConfig,
    role: str,
    publisher: OutgoingPublisher,
    handle_speech_fn: HandleSpeechFn,
    managed_session_store: ManagedSessionStore | None,
    get_managed_session_id: Callable[[], str | None] | None,
) -> (
    WhisperStage
    | DeepgramStage
    | ManagedSttStage
    | DummyStage
    | OpenAIStage
    | ReazonSpeechStage
    | RemoteStage
    | VoskStage
    | XaiStage
):
    if cfg.backend == "whisper":
        return WhisperStage(in_q, cfg, role, publisher, handle_speech_fn)
    if cfg.backend == "deepgram":
        return DeepgramStage(in_q, cfg, role, publisher, handle_speech_fn)
    if cfg.backend == "managed":
        if cfg.sample_rate != 16_000:
            raise ValueError("managed STT requires 16 kHz mono PCM")
        if managed_session_store is None or get_managed_session_id is None:
            raise ValueError("managed STT session bridge is not connected")
        return ManagedSttStage(
            in_q,
            role,
            publisher,
            handle_speech_fn,
            managed_session_store,
            get_managed_session_id,
        )
    if cfg.backend == "openai":
        return OpenAIStage(in_q, cfg, role, publisher, handle_speech_fn)
    if cfg.backend == "dummy":
        return DummyStage(in_q, cfg, role, publisher, handle_speech_fn)
    if cfg.backend == "reazonspeech":
        return ReazonSpeechStage(in_q, cfg, role, publisher, handle_speech_fn)
    if cfg.backend == "remote":
        return RemoteStage(in_q, cfg, role, publisher, handle_speech_fn)
    if cfg.backend == "vosk":
        return VoskStage(in_q, cfg, role, publisher, handle_speech_fn)
    if cfg.backend == "xai":
        return XaiStage(in_q, cfg, role, publisher, handle_speech_fn)
    raise ValueError(f"未知の STT バックエンド: {cfg.backend!r}")


def _stt_stage_config_changed(old_cfg: SttConfig, new_cfg: SttConfig) -> bool:
    watched_fields = (
        "backend",
        "whisper_model",
        "openai_model",
        "deepgram_model",
        "vosk_model_path",
        "language",
        "silence_duration",
        "remote_url",
        "remote_token",
        "sample_rate",
        "chunk_size",
        "min_voiced_ms",
        "min_voiced_ratio",
        "min_rms_dbfs",
        "temperature",
        "decode_no_speech_threshold",
        "decode_log_prob_threshold",
        "decode_compression_ratio_threshold",
        "hard_min_voiced_ms",
        "hard_no_speech_threshold",
        "hard_logprob_threshold",
        "hard_compression_ratio_threshold",
        "soft_min_voiced_ms",
        "soft_min_voiced_ratio",
        "soft_min_rms_dbfs",
        "soft_no_speech_threshold",
        "soft_logprob_threshold",
        "soft_compression_ratio_threshold",
        "drop_score_threshold",
        "suspicious_phrases",
    )
    return any(getattr(old_cfg, field_name) != getattr(new_cfg, field_name) for field_name in watched_fields)


class SttPipeline:
    """VAD + STT pipeline that attaches to an AudioPipeline's stt_queue.

    Lifecycle:
        initialize(loop)  — optional local-model prewarm (no-op for other backends)
        start(loop)       — starts VadStage + SttStage
        stop()            — stops VAD + STT; AudioPipeline keeps running
        shutdown()        — stop() + release local model
        apply_config()    — hot-swap VAD or STT stage
    """

    def __init__(
        self,
        stt_queue: queue.Queue[AudioFrame | None],
        cfg: SttConfig,
        role: str,
        broadcast_fn: OutgoingBroadcastFn,
        handle_speech_fn: HandleSpeechFn,
        managed_session_store: ManagedSessionStore | None = None,
        get_managed_session_id: Callable[[], str | None] | None = None,
    ) -> None:
        self._stt_queue: queue.Queue[AudioFrame | None] = stt_queue
        self._cfg: SttConfig = cfg
        self._role: str = role
        self._broadcast: OutgoingBroadcastFn = broadcast_fn
        self._handle_speech: HandleSpeechFn = handle_speech_fn
        self._managed_session_store: ManagedSessionStore | None = managed_session_store
        self._get_managed_session_id: Callable[[], str | None] | None = get_managed_session_id
        self._lock: threading.Lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._publisher: ThreadSafePublisher | None = None
        self._started: bool = False
        self._whisper_initialized: bool = False
        self._vosk_initialized: bool = False
        self._reazonspeech_initialized: bool = False
        self._whisper_initializing: bool = False
        self._whisper_shutdown_requested: bool = False
        self._vosk_initializing: bool = False
        self._vosk_shutdown_requested: bool = False
        self._reazonspeech_initializing: bool = False
        self._reazonspeech_shutdown_requested: bool = False
        self.on_ready: Callable[[], Coroutine[Never, Never, None]] | None = None
        self.on_error: Callable[[Exception], Coroutine[Never, Never, None]] | None = None

        self._q2: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=_Q2_SIZE)
        self._pipeline: Pipeline[AudioFrame | None] | None = None

    # ── SttStreamLike protocol ────────────────────────────────────────────────

    def supports_prewarm(self) -> bool:
        return self._cfg.backend in {"whisper", "vosk", "reazonspeech"}

    def initialize(self, loop: asyncio.AbstractEventLoop) -> None:
        """Pre-warm local STT models. No-op for cloud/remote/dummy backends."""
        if self._cfg.backend == "whisper":
            self._initialize_whisper(loop)
            return
        if self._cfg.backend == "vosk":
            self._initialize_vosk(loop)
            return
        if self._cfg.backend == "reazonspeech":
            self._initialize_reazonspeech(loop)
            return
        self._publish_ready(loop)

    def _initialize_whisper(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._whisper_initialized:
            self._publish_ready(loop)
            return
        if self._whisper_initializing:
            return

        self._loop = loop
        publisher = ThreadSafePublisher(self._broadcast, loop)
        self._whisper_initializing = True
        self._whisper_shutdown_requested = False

        def load_whisper() -> None:
            try:
                WhisperEngine.acquire(self._cfg, publisher=publisher)
            except Exception as e:
                import traceback

                logger.error("Whisper初期化エラー(%s): %s", self._role, e)
                traceback.print_exc()
                self._whisper_initializing = False
                if self._whisper_shutdown_requested:
                    self.on_ready = None
                    self.on_error = None
                    return
                if self.on_error is not None:
                    _ = asyncio.run_coroutine_threadsafe(self.on_error(e), loop)
                    self.on_error = None
                else:
                    _ = asyncio.run_coroutine_threadsafe(
                        self._broadcast(ErrorMsg(text=f"Whisper初期化エラー({self._role}): {e}")),
                        loop,
                    )
                self.on_ready = None
                return

            self._whisper_initializing = False
            if self._whisper_shutdown_requested:
                WhisperEngine.release()
                self.on_ready = None
                self.on_error = None
                return
            self._whisper_initialized = True
            self._publish_ready(loop)

        thread = threading.Thread(target=load_whisper, name=f"whisper-init-{self._role}", daemon=True)
        thread.start()

    def _initialize_vosk(self, loop: asyncio.AbstractEventLoop) -> None:
        publisher: ThreadSafePublisher | None = None
        cfg: SttConfig | None = None
        with self._lock:
            if self._vosk_initialized:
                already_initialized = True
            elif self._vosk_initializing:
                return
            else:
                already_initialized = False
                self._loop = loop
                publisher = ThreadSafePublisher(self._broadcast, loop)
                cfg = self._cfg
                self._vosk_initializing = True
                self._vosk_shutdown_requested = False

        if already_initialized:
            self._publish_ready(loop)
            return

        assert cfg is not None
        assert publisher is not None

        def load_vosk(
            model_cfg: SttConfig,
            progress_publisher: ThreadSafePublisher,
            callback_loop: asyncio.AbstractEventLoop,
        ) -> None:
            try:
                VoskEngine.acquire(model_cfg, publisher=progress_publisher)
            except Exception as e:
                import traceback

                logger.error("Vosk初期化エラー(%s): %s", self._role, e)
                traceback.print_exc()
                with self._lock:
                    self._vosk_initializing = False
                    if self._vosk_shutdown_requested:
                        self.on_ready = None
                        self.on_error = None
                        return
                    on_error = self.on_error
                    self.on_error = None
                    self.on_ready = None
                if on_error is not None:
                    _ = asyncio.run_coroutine_threadsafe(on_error(e), callback_loop)
                else:
                    _ = asyncio.run_coroutine_threadsafe(
                        self._broadcast(ErrorMsg(text=f"Vosk初期化エラー({self._role}): {e}")),
                        callback_loop,
                    )
                return

            with self._lock:
                self._vosk_initializing = False
                if self._vosk_shutdown_requested:
                    self.on_ready = None
                    self.on_error = None
                    release_after_load = True
                    on_ready = None
                else:
                    release_after_load = self._vosk_initialized
                    self._vosk_initialized = True
                    on_ready = self.on_ready
                    self.on_ready = None

            if release_after_load:
                VoskEngine.release()
            if on_ready is not None:
                _ = asyncio.run_coroutine_threadsafe(on_ready(), callback_loop)

        thread = threading.Thread(
            target=load_vosk,
            args=(cfg, publisher, loop),
            name=f"vosk-init-{self._role}",
            daemon=True,
        )
        thread.start()

    def _initialize_reazonspeech(self, loop: asyncio.AbstractEventLoop) -> None:
        publisher: ThreadSafePublisher | None = None
        cfg: SttConfig | None = None
        with self._lock:
            if self._reazonspeech_initialized:
                already_initialized = True
            elif self._reazonspeech_initializing:
                return
            else:
                already_initialized = False
                self._loop = loop
                publisher = ThreadSafePublisher(self._broadcast, loop)
                cfg = self._cfg
                self._reazonspeech_initializing = True
                self._reazonspeech_shutdown_requested = False

        if already_initialized:
            self._publish_ready(loop)
            return

        assert cfg is not None
        assert publisher is not None

        def load_reazonspeech(
            model_cfg: SttConfig,
            progress_publisher: ThreadSafePublisher,
            callback_loop: asyncio.AbstractEventLoop,
        ) -> None:
            try:
                ReazonSpeechEngine.acquire(model_cfg, publisher=progress_publisher)
            except Exception as error:
                logger.error("ReazonSpeech初期化エラー(%s)", self._role, exc_info=True)
                with self._lock:
                    self._reazonspeech_initializing = False
                    if self._reazonspeech_shutdown_requested:
                        self.on_ready = None
                        self.on_error = None
                        return
                    on_error = self.on_error
                    self.on_error = None
                    self.on_ready = None
                if on_error is not None:
                    _ = asyncio.run_coroutine_threadsafe(on_error(error), callback_loop)
                else:
                    _ = asyncio.run_coroutine_threadsafe(
                        self._broadcast(ErrorMsg(text=f"ReazonSpeechを準備できませんでした({self._role})。")),
                        callback_loop,
                    )
                return

            with self._lock:
                self._reazonspeech_initializing = False
                if self._reazonspeech_shutdown_requested:
                    self.on_ready = None
                    self.on_error = None
                    release_after_load = True
                    on_ready = None
                else:
                    release_after_load = self._reazonspeech_initialized
                    self._reazonspeech_initialized = True
                    on_ready = self.on_ready
                    self.on_ready = None

            if release_after_load:
                ReazonSpeechEngine.release()
            if on_ready is not None:
                _ = asyncio.run_coroutine_threadsafe(on_ready(), callback_loop)

        thread = threading.Thread(
            target=load_reazonspeech,
            args=(cfg, publisher, loop),
            name=f"reazonspeech-init-{self._role}",
            daemon=True,
        )
        thread.start()

    def _publish_ready(self, loop: asyncio.AbstractEventLoop) -> None:
        if self.on_ready is not None:
            _ = asyncio.run_coroutine_threadsafe(self.on_ready(), loop)
            self.on_ready = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            if self._started:
                return
            self._loop = loop
            self._publisher = ThreadSafePublisher(self._broadcast, loop)
            self._started = True
            self._build_and_start()

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
            if self._pipeline is not None:
                # Do NOT inject sentinels into self._stt_queue — it is owned by
                # AudioPipeline.  Stages use _stop_event for hot shutdown.
                self._pipeline.stop(timeout=2, inject_sentinels=False)
                self._pipeline = None

    def shutdown(self) -> None:
        """stop() + release local STT models."""
        self._whisper_shutdown_requested = True
        with self._lock:
            self._vosk_shutdown_requested = True
            self._reazonspeech_shutdown_requested = True
            if self._vosk_initializing:
                self.on_ready = None
                self.on_error = None
            if self._reazonspeech_initializing:
                self.on_ready = None
                self.on_error = None
        self.stop()
        if self._whisper_initialized:
            WhisperEngine.release()
            self._whisper_initialized = False
        self._whisper_initializing = False
        with self._lock:
            release_vosk = self._vosk_initialized
            self._vosk_initialized = False
            release_reazonspeech = self._reazonspeech_initialized
            self._reazonspeech_initialized = False
        if release_vosk:
            VoskEngine.release()
        if release_reazonspeech:
            ReazonSpeechEngine.release()

    # ── Config hot-swap ───────────────────────────────────────────────────────

    def apply_config(self, cfg: SttConfig) -> None:
        """Hot-swap VAD and/or STT stage when config changes."""
        with self._lock:
            old_cfg = self._cfg
            self._cfg = cfg

            if not self._started or self._publisher is None:
                return

            if cfg.vad_aggressiveness != old_cfg.vad_aggressiveness:
                self._swap_vad(cfg)

            if _stt_stage_config_changed(old_cfg, cfg):
                self._swap_stt(cfg)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _build_and_start(self) -> None:
        assert self._publisher is not None
        self._q2 = queue.Queue(maxsize=_Q2_SIZE)
        vad = VadStage(self._stt_queue, self._q2, WebRtcVadEngine(self._cfg.vad_aggressiveness), self._cfg.sample_rate)
        stt = _make_stt_stage(
            self._q2,
            self._cfg,
            self._role,
            self._publisher,
            self._handle_speech,
            self._managed_session_store,
            self._get_managed_session_id,
        )

        if isinstance(stt, WhisperStage) and not self._whisper_initialized:
            WhisperEngine.acquire(self._cfg, publisher=self._publisher)
            self._whisper_initialized = True
        if isinstance(stt, VoskStage) and not self._vosk_initialized:
            VoskEngine.acquire(self._cfg, publisher=self._publisher)
            self._vosk_initialized = True
        if isinstance(stt, ReazonSpeechStage) and not self._reazonspeech_initialized:
            ReazonSpeechEngine.acquire(self._cfg, publisher=self._publisher)
            self._reazonspeech_initialized = True

        self._pipeline = Pipeline[AudioFrame | None]([vad, stt], input_queues=[self._q2])
        self._pipeline.start()

    def _swap_vad(self, new_cfg: SttConfig) -> None:
        if self._pipeline is None:
            return
        stages = self._pipeline.stages
        if not stages:
            return
        old_vad = stages[0]
        old_vad.stop(timeout=2)
        new_vad = VadStage(
            self._stt_queue,
            self._q2,
            WebRtcVadEngine(new_cfg.vad_aggressiveness),
            new_cfg.sample_rate,
        )
        new_vad.start()
        self._pipeline.replace_stage(0, new_vad)

    def _swap_stt(self, new_cfg: SttConfig) -> None:
        assert self._publisher is not None
        if self._pipeline is None:
            return
        stages = self._pipeline.stages
        if len(stages) < 2:
            return
        old_stt = stages[1]
        if isinstance(old_stt, WhisperStage):
            WhisperEngine.release()
            self._whisper_initialized = False
        if isinstance(old_stt, VoskStage):
            VoskEngine.release()
            self._vosk_initialized = False
        if isinstance(old_stt, ReazonSpeechStage):
            ReazonSpeechEngine.release()
            self._reazonspeech_initialized = False

        old_stt.stop(timeout=2)

        new_stt = _make_stt_stage(
            self._q2,
            new_cfg,
            self._role,
            self._publisher,
            self._handle_speech,
            self._managed_session_store,
            self._get_managed_session_id,
        )
        if isinstance(new_stt, WhisperStage):
            WhisperEngine.acquire(new_cfg, publisher=self._publisher)
            self._whisper_initialized = True
        if isinstance(new_stt, VoskStage):
            VoskEngine.acquire(new_cfg, publisher=self._publisher)
            self._vosk_initialized = True
        if isinstance(new_stt, ReazonSpeechStage):
            ReazonSpeechEngine.acquire(new_cfg, publisher=self._publisher)
            self._reazonspeech_initialized = True

        new_stt.start()
        self._pipeline.replace_stage(1, new_stt)


__all__ = ["SttPipeline"]
