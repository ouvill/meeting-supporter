"""ReazonSpeech K2-v2 STT stage for VAD-confirmed local audio."""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from dataclasses import dataclass
from typing import cast, final, override

import numpy as np
from numpy.typing import NDArray

from app.audio.base import AudioFrame, PipelineStage
from app.core.config import SttConfig
from app.core.messages import ErrorMsg, StatusMsg, SttInterimMsg
from app.core.publisher import OutgoingPublisher
from app.core.types import HandleSpeechFn
from app.stt.reazonspeech_model import (
    REAZONSPEECH_MAX_AUDIO_SECONDS,
    REAZONSPEECH_PAD_SECONDS,
    REAZONSPEECH_SAMPLE_RATE,
    OfflineRecognizer,
    cached_reazonspeech_snapshot,
    download_reazonspeech_snapshot,
    load_reazonspeech_recognizer,
    transcribe_reazonspeech,
)

logger = logging.getLogger(__name__)
_FRAME_MS = 30
_MAX_SEGMENT_FRAMES = max(
    1,
    int((REAZONSPEECH_MAX_AUDIO_SECONDS - 2 * REAZONSPEECH_PAD_SECONDS) * 1000 / _FRAME_MS) - 1,
)


@dataclass(frozen=True)
class _ReazonSpeechJob:
    stage: ReazonSpeechStage
    run_token: int
    audio: NDArray[np.float32]


@final
class ReazonSpeechEngine:
    """Shared ReazonSpeech recognizer and serialized inference worker."""

    _lock = threading.Lock()
    _enqueue_lock = threading.Lock()
    _model: OfflineRecognizer | None = None
    _queue: queue.Queue[_ReazonSpeechJob] | None = None
    _thread: threading.Thread | None = None
    _running = False
    _ref_count = 0

    @classmethod
    def acquire(cls, cfg: SttConfig, publisher: OutgoingPublisher | None = None) -> None:
        if cfg.sample_rate != REAZONSPEECH_SAMPLE_RATE:
            raise ValueError("ReazonSpeechは16 kHz mono PCMを必要とします。")
        if cfg.language != "ja":
            raise ValueError("ReazonSpeech K2-v2は日本語にのみ対応しています。")

        with cls._lock:
            if cls._model is None:
                _publish(publisher, "ReazonSpeechモデルを確認中...")
                started = time.monotonic()
                try:
                    snapshot = cached_reazonspeech_snapshot()
                    if snapshot is None:
                        _publish(
                            publisher,
                            "ReazonSpeechモデルをダウンロード中です。初回のみ時間がかかる場合があります...",
                        )
                        snapshot = download_reazonspeech_snapshot()
                    _publish(publisher, "ReazonSpeechモデルをロード中...")
                    cls._model = load_reazonspeech_recognizer(snapshot)
                except Exception as error:
                    logger.exception("ReazonSpeechモデルを準備できませんでした")
                    raise RuntimeError("ReazonSpeechモデルを準備できませんでした。") from error
                cls._queue = queue.Queue(maxsize=64)
                cls._running = True
                cls._thread = threading.Thread(
                    target=cls._engine_worker,
                    name="reazonspeech-engine",
                    daemon=True,
                )
                cls._thread.start()
                logger.info("ReazonSpeechモデルロード完了 (%.1fs)", time.monotonic() - started)
                _publish(publisher, "ReazonSpeechモデルの準備が完了しました")
            cls._ref_count += 1

    @classmethod
    def release(cls) -> None:
        with cls._lock:
            if cls._ref_count <= 0:
                return
            cls._ref_count -= 1
            if cls._ref_count > 0:
                return
            cls._running = False
            engine_thread = cls._thread

        if engine_thread is not None:
            engine_thread.join(timeout=2.0)
            if engine_thread.is_alive():
                logger.warning("ReazonSpeechエンジンスレッドが2秒以内に終了しませんでした")

        with cls._lock:
            cls._thread = None
            cls._model = None
            cls._queue = None

    @classmethod
    def enqueue(cls, job: _ReazonSpeechJob) -> None:
        work_queue = cls._queue
        if work_queue is None:
            return
        try:
            work_queue.put_nowait(job)
        except queue.Full:
            with cls._enqueue_lock:
                try:
                    _ = work_queue.get_nowait()
                    work_queue.put_nowait(job)
                except queue.Empty:
                    pass

    @classmethod
    def _engine_worker(cls) -> None:
        assert cls._model is not None
        assert cls._queue is not None

        while cls._running or not cls._queue.empty():
            try:
                job = cls._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            stage = job.stage
            if stage.is_stopped or job.run_token != stage.current_run_token:
                continue
            try:
                text = transcribe_reazonspeech(cls._model, job.audio)
                if (
                    text
                    and not should_drop_reazonspeech_transcript(stage.cfg, text)
                    and not stage.is_stopped
                    and job.run_token == stage.current_run_token
                ):
                    stage.publisher.schedule(stage.handle_speech(stage.role, text))
            except Exception:
                logger.exception("ReazonSpeech推論エラー role=%s", stage.role)
                stage.publisher.publish(ErrorMsg(text=f"ReazonSpeechで音声を認識できませんでした({stage.role})。"))


def _publish(publisher: OutgoingPublisher | None, text: str) -> None:
    if publisher is not None:
        publisher.publish(StatusMsg(text=text))


def should_drop_reazonspeech_transcript(cfg: SttConfig, text: str) -> bool:
    normalized = "".join(text.split())
    if not normalized:
        return True
    return any(
        blocked and blocked in normalized
        for phrase in cfg.suspicious_phrases
        if (blocked := "".join(str(phrase).split()))
    )


@final
class ReazonSpeechStage(PipelineStage):
    """Buffer speech segments and enqueue Japanese K2-v2 inference."""

    def __init__(
        self,
        in_q: queue.Queue[AudioFrame | None],
        cfg: SttConfig,
        role: str,
        publisher: OutgoingPublisher,
        handle_speech_fn: HandleSpeechFn,
    ) -> None:
        super().__init__()
        self._in_q = in_q
        self._cfg = cfg
        self._role = role
        self._publisher = publisher
        self._handle_speech = handle_speech_fn
        self._run_token = 0

    @property
    def current_run_token(self) -> int:
        return self._run_token

    @property
    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    @property
    def cfg(self) -> SttConfig:
        return self._cfg

    @property
    def publisher(self) -> OutgoingPublisher:
        return self._publisher

    @property
    def handle_speech(self) -> HandleSpeechFn:
        return self._handle_speech

    @property
    def role(self) -> str:
        return self._role

    @override
    def _run(self) -> None:
        cfg = self._cfg
        silence_threshold = max(1, int(float(cfg.silence_duration) * 1000 / _FRAME_MS))
        speech_buf: list[bytes] = []
        silence_frames = 0
        in_speech = False
        voiced_frames = 0
        segment_frames = 0
        self._run_token += 1
        run_token = self._run_token

        while not self._stop_event.is_set():
            try:
                frame = self._in_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if frame is None:
                if in_speech:
                    self._finalize_segment(cfg, speech_buf, voiced_frames, segment_frames, run_token)
                    self._publisher.publish(SttInterimMsg(role=self._role, text=""))
                break

            if in_speech or frame.is_speech:
                speech_buf.append(frame.pcm)
            if frame.is_speech:
                voiced_frames += 1
                segment_frames += 1
                silence_frames = 0
                if not in_speech:
                    in_speech = True
                    self._publisher.publish(SttInterimMsg(role=self._role, text="…"))
            elif in_speech:
                silence_frames += 1
                segment_frames += 1

            reached_silence = in_speech and silence_frames >= silence_threshold
            reached_model_limit = in_speech and segment_frames >= _MAX_SEGMENT_FRAMES
            if reached_silence or reached_model_limit:
                self._finalize_segment(cfg, speech_buf, voiced_frames, segment_frames, run_token)
                self._publisher.publish(SttInterimMsg(role=self._role, text=""))
                speech_buf.clear()
                silence_frames = 0
                in_speech = False
                voiced_frames = 0
                segment_frames = 0

    def _finalize_segment(
        self,
        cfg: SttConfig,
        speech_buf: list[bytes],
        voiced_frames: int,
        segment_frames: int,
        run_token: int,
    ) -> None:
        if not speech_buf:
            return
        audio = np.frombuffer(b"".join(speech_buf), dtype=np.int16).astype(np.float32) / 32767.0
        if not self._should_transcribe(cfg, audio, voiced_frames, segment_frames):
            return
        ReazonSpeechEngine.enqueue(_ReazonSpeechJob(self, run_token, audio))

    @staticmethod
    def _should_transcribe(
        cfg: SttConfig,
        audio: NDArray[np.float32],
        voiced_frames: int,
        segment_frames: int,
    ) -> bool:
        voiced_ms = voiced_frames * _FRAME_MS
        voiced_ratio = voiced_frames / max(1, segment_frames)
        squared = cast(NDArray[np.float32], np.square(audio))
        mean_value = cast(np.float64, np.mean(squared))
        rms_dbfs = 20.0 * math.log10(max(math.sqrt(float(mean_value)), 1.0e-6))
        return (
            voiced_ms >= int(cfg.min_voiced_ms)
            and voiced_ratio >= float(cfg.min_voiced_ratio)
            and rms_dbfs >= float(cfg.min_rms_dbfs)
        )


__all__ = [
    "ReazonSpeechEngine",
    "ReazonSpeechStage",
    "should_drop_reazonspeech_transcript",
]
