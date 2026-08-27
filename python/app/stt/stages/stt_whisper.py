"""WhisperStage: faster-whisper STT consuming VAD-annotated frames from Q2."""

from __future__ import annotations

import logging
import math
import os
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast, override

import numpy as np
from numpy.typing import NDArray

from app.audio.base import AudioFrame, PipelineStage
from app.core.config import SttConfig
from app.core.messages import ErrorMsg, StatusMsg, SttInterimMsg
from app.core.publisher import OutgoingPublisher
from app.core.types import HandleSpeechFn
from app.stt.transcript_judge import (
    AudioEvidence,
    TranscriptEvidence,
    judge_transcript,
    whisper_evidence_from_segments,
)

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)
_FRAME_MS = 30
_PREROLL_MS = 150
_MODEL_BIN_FILENAME = "model.bin"
_CUDA_RUNTIME_ERROR_MARKERS = (
    "cuda failed",
    "cuda driver version is insufficient",
    "cuda driver",
    "cuda runtime",
    "cublas",
    "cudnn",
)


class _SegmentLike(Protocol):
    text: str
    avg_logprob: float
    compression_ratio: float
    no_speech_prob: float


@dataclass(frozen=True)
class _WhisperJob:
    stage: WhisperStage
    run_token: int
    audio_np: NDArray[np.float32]
    audio: AudioEvidence


# ── Shared engine ------------------------------------------------------------------


class WhisperEngine:
    """Shared faster-whisper model + inference thread.

    This is a singleton-like class variable manager.  Callers must pair every
    ``acquire()`` with exactly one ``release()``.
    """

    _lock: threading.Lock = threading.Lock()
    _enqueue_lock: threading.Lock = threading.Lock()
    _model: WhisperModel | None = None
    _queue: queue.Queue[_WhisperJob] | None = None
    _thread: threading.Thread | None = None
    _running: bool = False
    _ref_count: int = 0
    _active_device: str | None = None
    _model_path: str | None = None

    @classmethod
    def acquire(cls, cfg: SttConfig, publisher: OutgoingPublisher | None = None) -> None:
        logger.debug("acquire_engine 開始 model=%s ref=%d", cfg.whisper_model, cls._ref_count)
        with cls._lock:
            if cls._model is None:
                logger.info(
                    "Whisperモデルをロード中... (%s) ※数十秒かかる場合があります",
                    cfg.whisper_model,
                )
                t0 = time.monotonic()
                cls._model = cls._load_model(cfg, publisher=publisher)
                logger.info("Whisperモデルロード完了 (%.1fs)", time.monotonic() - t0)
                cls._queue = queue.Queue(maxsize=64)
                cls._running = True
                cls._thread = threading.Thread(target=cls._engine_worker, daemon=True)
                cls._thread.start()
            cls._ref_count += 1
        logger.debug("acquire_engine 完了 ref=%d", cls._ref_count)

    @classmethod
    def release(cls) -> None:
        logger.debug("release_engine 開始 ref=%d", cls._ref_count)
        with cls._lock:
            if cls._ref_count <= 0:
                return
            cls._ref_count -= 1
            if cls._ref_count > 0:
                logger.debug("release_engine: 参照残あり ref=%d", cls._ref_count)
                return
            cls._running = False
            engine_thread = cls._thread

        if engine_thread:
            logger.debug("Whisperエンジンスレッド終了待機...")
            engine_thread.join(timeout=2.0)
            if engine_thread.is_alive():
                logger.warning("Whisperエンジンスレッドが2秒以内に終了しませんでした")

        with cls._lock:
            cls._thread = None
            cls._model = None
            cls._queue = None
            cls._active_device = None
            cls._model_path = None
        logger.debug("release_engine 完了")

    @classmethod
    def enqueue(cls, payload: _WhisperJob) -> None:
        q = cls._queue
        if q is None:
            return
        try:
            q.put_nowait(payload)
        except queue.Full:
            with cls._enqueue_lock:
                try:
                    _ = q.get_nowait()
                    q.put_nowait(payload)
                except queue.Empty:
                    pass

    # ── Internal -----------------------------------------------------------------

    @staticmethod
    def _download_model_path(model_spec: str, *, local_files_only: bool) -> str:
        from typing import cast as _cast

        from faster_whisper.utils import download_model as _download_model  # pyright: ignore[reportMissingTypeStubs]

        path = _cast(
            str,
            _download_model(model_spec, local_files_only=True) if local_files_only else _download_model(model_spec),
        )
        assert isinstance(path, str), f"Expected str from download_model, got {type(path)}"
        return path

    @staticmethod
    def _has_model_bin(model_path: str) -> bool:
        return os.path.isfile(os.path.join(model_path, _MODEL_BIN_FILENAME))

    @staticmethod
    def _is_missing_model_bin_error(error: RuntimeError) -> bool:
        message = str(error).lower()
        return _MODEL_BIN_FILENAME in message and "unable to open file" in message

    @staticmethod
    def _resolve_model_path(
        model_spec: str,
        *,
        on_download: Callable[[], None] | None = None,
    ) -> str:
        """Download or resolve model path, returning a verified ``str``.

        ``faster_whisper.utils.download_model`` lacks type stubs, so we wrap
        the call in a runtime assertion to keep basedpyright happy.
        """
        from huggingface_hub.errors import LocalEntryNotFoundError

        if os.path.isdir(model_spec):
            return model_spec
        try:
            path = WhisperEngine._download_model_path(model_spec, local_files_only=True)
        except LocalEntryNotFoundError:
            if on_download is not None:
                on_download()
            path = WhisperEngine._download_model_path(model_spec, local_files_only=False)
        return path

    @staticmethod
    def _select_device(cfg: SttConfig) -> str:
        stt_device = str(cfg.device)
        if stt_device != "auto":
            return stt_device

        try:
            import ctranslate2

            return "cuda" if ctranslate2.get_supported_compute_types("cuda") else "cpu"
        except Exception:
            return "cpu"

    @staticmethod
    def _compute_type_for_device(device: str) -> str:
        return "float16" if device == "cuda" else "int8"

    @staticmethod
    def _is_cuda_runtime_error(error: RuntimeError) -> bool:
        message = str(error).lower()
        return "cuda" in message and any(marker in message for marker in _CUDA_RUNTIME_ERROR_MARKERS)

    @staticmethod
    def _publish_status(publisher: OutgoingPublisher | None, text: str) -> None:
        if publisher is not None:
            publisher.publish(StatusMsg(text=text))

    @staticmethod
    def _load_model_on_device(model_path: str, device: str) -> WhisperModel:
        from faster_whisper import WhisperModel

        return WhisperModel(model_path, device=device, compute_type=WhisperEngine._compute_type_for_device(device))

    @staticmethod
    def _load_model_with_cuda_fallback(
        model_path: str,
        stt_device: str,
        publisher: OutgoingPublisher | None,
    ) -> tuple[WhisperModel, str]:
        try:
            return WhisperEngine._load_model_on_device(model_path, stt_device), stt_device
        except RuntimeError as e:
            if stt_device != "cuda" or not WhisperEngine._is_cuda_runtime_error(e):
                raise
            logger.warning("CUDAでWhisperモデルをロードできないためCPUへフォールバックします", exc_info=True)
            WhisperEngine._publish_status(
                publisher,
                "CUDAでWhisperモデルのロードに失敗したため、CPUで再試行します...",
            )
            return WhisperEngine._load_model_on_device(model_path, "cpu"), "cpu"

    @staticmethod
    def _load_model(cfg: SttConfig, publisher: OutgoingPublisher | None = None) -> WhisperModel:
        stt_device = WhisperEngine._select_device(cfg)
        model_spec = cfg.whisper_model
        is_local_model_dir = os.path.isdir(model_spec)
        cache_repaired = False
        WhisperEngine._publish_status(publisher, "Whisperモデルを確認中...")

        model_path = WhisperEngine._resolve_model_path(
            model_spec,
            on_download=lambda: WhisperEngine._publish_status(
                publisher,
                "Whisperモデルをダウンロード中です。初回のみ時間がかかる場合があります...",
            ),
        )

        if not is_local_model_dir and not WhisperEngine._has_model_bin(model_path):
            cache_repaired = True
            logger.warning("Whisperモデルのローカルキャッシュにmodel.binが無いため再取得します: %s", model_path)
            WhisperEngine._publish_status(
                publisher,
                "Whisperモデルのキャッシュが不完全なため、再ダウンロードします...",
            )
            model_path = WhisperEngine._download_model_path(model_spec, local_files_only=False)

        WhisperEngine._publish_status(publisher, "Whisperモデルをロード中...")
        try:
            model, active_device = WhisperEngine._load_model_with_cuda_fallback(model_path, stt_device, publisher)
        except RuntimeError as e:
            if is_local_model_dir or cache_repaired or not WhisperEngine._is_missing_model_bin_error(e):
                raise
            cache_repaired = True
            logger.warning("Whisperモデルのロードでmodel.binを開けなかったため再取得します", exc_info=True)
            WhisperEngine._publish_status(
                publisher,
                "Whisperモデルのキャッシュが不完全なため、再ダウンロードします...",
            )
            model_path = WhisperEngine._download_model_path(model_spec, local_files_only=False)
            model, active_device = WhisperEngine._load_model_with_cuda_fallback(model_path, stt_device, publisher)

        WhisperEngine._active_device = active_device
        WhisperEngine._model_path = model_path
        WhisperEngine._publish_status(publisher, "Whisperモデルの準備が完了しました")
        return model

    @classmethod
    def _transcribe_segments(cls, stage: WhisperStage, audio_np: NDArray[np.float32]) -> list[_SegmentLike]:
        assert cls._model is not None
        segments, _ = cls._model.transcribe(
            audio_np,
            language=stage.cfg.language,
            beam_size=1,
            temperature=max(0.0, float(stage.cfg.temperature)),
            condition_on_previous_text=False,
            no_speech_threshold=float(stage.cfg.decode_no_speech_threshold),
            log_prob_threshold=float(stage.cfg.decode_log_prob_threshold),
            compression_ratio_threshold=float(stage.cfg.decode_compression_ratio_threshold),
            vad_filter=False,
        )
        return cast(list[_SegmentLike], list(segments))

    @classmethod
    def _fallback_to_cpu_after_cuda_failure(cls, error: RuntimeError, publisher: OutgoingPublisher) -> bool:
        if not cls._is_cuda_runtime_error(error):
            return False

        with cls._lock:
            if cls._active_device != "cuda" or cls._model_path is None:
                return False

            logger.warning("CUDAでWhisper推論を実行できないためCPUへフォールバックします", exc_info=True)
            cls._publish_status(
                publisher,
                "CUDAでWhisper推論に失敗したため、CPUで再試行します...",
            )
            cls._model = cls._load_model_on_device(cls._model_path, "cpu")
            cls._active_device = "cpu"
        return True

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
            run_token = job.run_token
            if stage.is_stopped or run_token != stage.current_run_token:
                continue
            try:
                try:
                    seg_list = cls._transcribe_segments(stage, job.audio_np)
                except RuntimeError as e:
                    if not cls._fallback_to_cpu_after_cuda_failure(e, stage.publisher):
                        raise
                    seg_list = cls._transcribe_segments(stage, job.audio_np)
                text = "".join(s.text for s in seg_list).strip()
                decision = judge_transcript(
                    stage.cfg,
                    TranscriptEvidence(
                        text=text,
                        audio=job.audio,
                        whisper=whisper_evidence_from_segments(seg_list),
                    ),
                )
                if not decision.keep:
                    logger.debug(
                        "Whisper transcript dropped: role=%s score=%.2f reasons=%s text=%r",
                        stage.role,
                        decision.score,
                        decision.reasons,
                        text,
                    )
                    continue
                if text and not stage.is_stopped and run_token == stage.current_run_token:
                    stage.publisher.schedule(stage.handle_speech(stage.role, text))
            except Exception as e:
                stage.publisher.publish(ErrorMsg(text=f"WhisperSTTエラー({stage.role}): {e}"))


# ── Stage --------------------------------------------------------------------------


class WhisperStage(PipelineStage):
    """Buffers speech frames and transcribes them via a shared faster-whisper model.

    VAD is not performed here — ``is_speech`` is read directly from each
    AudioFrame.  The shared :class:`WhisperEngine` avoids loading the model
    multiple times.
    """

    def __init__(
        self,
        in_q: queue.Queue[AudioFrame | None],
        cfg: SttConfig,
        role: str,
        publisher: OutgoingPublisher,
        handle_speech_fn: HandleSpeechFn,
    ) -> None:
        super().__init__()
        self._in_q: queue.Queue[AudioFrame | None] = in_q
        self._cfg: SttConfig = cfg
        self._role: str = role
        self._publisher: OutgoingPublisher = publisher
        self._handle_speech: HandleSpeechFn = handle_speech_fn
        self._run_token: int = 0

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

    # ── Stage run loop ---------------------------------------------------------

    @override
    def _run(self) -> None:
        cfg = self._cfg
        silence_threshold = max(1, int(float(cfg.silence_duration) * 1000 / _FRAME_MS))
        preroll: deque[bytes] = deque(maxlen=max(1, _PREROLL_MS // _FRAME_MS))

        speech_buf: list[bytes] = []
        silence_frames = 0
        in_speech = False
        voiced_frames = 0
        segment_frames = 0
        prepended_frames = 0
        prepended_samples = 0

        self._run_token += 1
        run_token = self._run_token

        while not self._stop_event.is_set():
            try:
                frame = self._in_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if frame is None:
                break

            if frame.is_speech:
                if not in_speech:
                    pre_speech = tuple(preroll)
                    speech_buf.extend(pre_speech)
                    prepended_frames = len(pre_speech)
                    prepended_samples = sum(len(pcm) // 2 for pcm in pre_speech)
                    in_speech = True
                    self._publisher.publish(SttInterimMsg(role=self._role, text="…"))
                speech_buf.append(frame.pcm)
                voiced_frames += 1
                segment_frames += 1
                silence_frames = 0
            elif in_speech:
                silence_frames += 1
                speech_buf.append(frame.pcm)
                segment_frames += 1
                if silence_frames >= silence_threshold:
                    audio_np = np.frombuffer(b"".join(speech_buf), dtype=np.int16).astype(np.float32) / 32767.0
                    segment_audio_np = audio_np[prepended_samples:]
                    voiced_ms = voiced_frames * _FRAME_MS
                    voiced_ratio = voiced_frames / max(1, segment_frames)
                    rms_dbfs = self._rms_dbfs(segment_audio_np)
                    audio = AudioEvidence(
                        voiced_ms=voiced_ms,
                        voiced_ratio=voiced_ratio,
                        rms_dbfs=rms_dbfs,
                        duration_ms=(prepended_frames + segment_frames) * _FRAME_MS,
                    )
                    speech_buf.clear()
                    in_speech = False
                    silence_frames = 0
                    voiced_frames = 0
                    segment_frames = 0
                    prepended_frames = 0
                    prepended_samples = 0
                    if not self._stop_event.is_set() and self._should_enqueue(cfg, audio):
                        self._enqueue_audio(audio_np, audio, run_token)
                    self._publisher.publish(SttInterimMsg(role=self._role, text=""))

            preroll.append(frame.pcm)

    # ── Helpers ------------------------------------------------------------------

    @staticmethod
    def _rms_dbfs(audio_np: NDArray[np.float32]) -> float:
        sq = cast(NDArray[np.float32], np.square(audio_np))
        mean_val = cast(np.float64, np.mean(sq))
        rms = math.sqrt(float(mean_val))
        return 20.0 * math.log10(max(rms, 1.0e-6))

    @staticmethod
    def _should_enqueue(cfg: SttConfig, audio: AudioEvidence) -> bool:
        return audio.voiced_ms >= int(cfg.hard_min_voiced_ms)

    def _enqueue_audio(self, audio_np: NDArray[np.float32], audio: AudioEvidence, run_token: int) -> None:
        WhisperEngine.enqueue(_WhisperJob(self, run_token, audio_np, audio))


__all__ = ["WhisperEngine", "WhisperStage"]
