"""VoskStage: offline Vosk STT consuming VAD-annotated frames from Q2."""

from __future__ import annotations

import importlib
import json
import logging
import math
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast, override

import numpy as np
from numpy.typing import NDArray

from app.audio.base import AudioFrame, PipelineStage
from app.core.config import SttConfig
from app.core.messages import ErrorMsg, StatusMsg, SttInterimMsg
from app.core.publisher import OutgoingPublisher
from app.core.types import HandleSpeechFn

logger = logging.getLogger(__name__)
_FRAME_MS = 30
_PREROLL_MS = 150


class _VoskModel(Protocol):
    pass


class _VoskRecognizer(Protocol):
    def AcceptWaveform(self, data: bytes) -> bool: ...

    def Result(self) -> str: ...

    def FinalResult(self) -> str: ...


class _VoskModelFactory(Protocol):
    def __call__(self, model_path: str) -> _VoskModel: ...


class _VoskRecognizerFactory(Protocol):
    def __call__(self, model: _VoskModel, sample_rate: float) -> _VoskRecognizer: ...


class VoskEngine:
    """Shared Vosk model cache.

    Vosk models are much smaller than the default Whisper model, but loading still
    takes enough time that both audio roles should share one process-local model.
    """

    _lock: threading.Lock = threading.Lock()
    _model: _VoskModel | None = None
    _model_path: str | None = None
    _ref_count: int = 0

    @classmethod
    def acquire(cls, cfg: SttConfig, publisher: OutgoingPublisher | None = None) -> None:
        model_path = resolve_vosk_model_path(cfg.vosk_model_path)
        with cls._lock:
            if cls._model is None or cls._model_path != model_path:
                if cls._model is not None:
                    logger.info("Voskモデルを切り替えます: %s -> %s", cls._model_path, model_path)
                _publish(publisher, f"Voskモデルをロード中... ({model_path})")
                t0 = time.monotonic()
                model_factory, _recognizer_factory, set_log_level = _load_vosk_api()
                set_log_level(-1)
                cls._model = model_factory(model_path)
                cls._model_path = model_path
                logger.info("Voskモデルロード完了 path=%s (%.1fs)", model_path, time.monotonic() - t0)
                _publish(publisher, "Voskモデルの準備が完了しました")
            cls._ref_count += 1

    @classmethod
    def release(cls) -> None:
        with cls._lock:
            if cls._ref_count <= 0:
                return
            cls._ref_count -= 1
            if cls._ref_count > 0:
                return
            cls._model = None
            cls._model_path = None

    @classmethod
    def transcribe(cls, cfg: SttConfig, pcm: bytes) -> str:
        with cls._lock:
            model = cls._model
        if model is None:
            cls.acquire(cfg)
            with cls._lock:
                model = cls._model
        assert model is not None
        _model_factory, recognizer_factory, _set_log_level = _load_vosk_api()
        recognizer = recognizer_factory(model, float(cfg.sample_rate))
        parts: list[str] = []
        if recognizer.AcceptWaveform(pcm):
            text = parse_vosk_text(recognizer.Result())
            if text:
                parts.append(text)
        final_text = parse_vosk_text(recognizer.FinalResult())
        if final_text:
            parts.append(final_text)
        return " ".join(parts).strip()


def _publish(publisher: OutgoingPublisher | None, text: str) -> None:
    if publisher is not None:
        publisher.publish(StatusMsg(text=text))


def _load_vosk_api() -> tuple[_VoskModelFactory, _VoskRecognizerFactory, Callable[[int], None]]:
    try:
        module = importlib.import_module("vosk")
    except ImportError as exc:
        raise RuntimeError(
            "Vosk backend requires the `vosk` Python package. Install dependencies with `uv sync`."
        ) from exc
    model_factory = cast(_VoskModelFactory, getattr(module, "Model"))
    recognizer_factory = cast(_VoskRecognizerFactory, getattr(module, "KaldiRecognizer"))
    set_log_level = cast(Callable[[int], None], getattr(module, "SetLogLevel"))
    return model_factory, recognizer_factory, set_log_level


def resolve_vosk_model_path(model_path: str) -> str:
    value = model_path.strip()
    if not value:
        raise ValueError("Vosk model path is empty. Set [stt].vosk_model_path to a local model directory.")
    path = Path(value).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            "Vosk model directory not found: "
            + value
            + ". Download and unzip a model such as vosk-model-small-ja-0.22, "
            + "then set [stt].vosk_model_path."
        )
    if not path.is_dir():
        raise NotADirectoryError(f"Vosk model path is not a directory: {value}")
    return str(path)


def parse_vosk_text(payload: str) -> str:
    try:
        parsed = cast(object, json.loads(payload))
    except json.JSONDecodeError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    data = cast(dict[str, object], parsed)
    text = data.get("text")
    return text.strip() if isinstance(text, str) else ""


def should_drop_vosk_transcript(cfg: SttConfig, text: str) -> bool:
    normalized = "".join(text.split())
    if not normalized:
        return True
    for phrase in cfg.suspicious_phrases:
        blocked = "".join(str(phrase).split())
        if blocked and blocked in normalized:
            return True
    return False


class VoskStage(PipelineStage):
    """Buffers VAD-confirmed speech frames and transcribes them with Vosk."""

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
        prepended_samples = 0

        while not self._stop_event.is_set():
            try:
                frame = self._in_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if frame is None:
                if in_speech:
                    self._finalize_segment(cfg, speech_buf, voiced_frames, segment_frames, prepended_samples)
                break

            if frame.is_speech:
                if not in_speech:
                    pre_speech = tuple(preroll)
                    speech_buf.extend(pre_speech)
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
                    self._finalize_segment(cfg, speech_buf, voiced_frames, segment_frames, prepended_samples)
                    self._publisher.publish(SttInterimMsg(role=self._role, text=""))
                    speech_buf.clear()
                    in_speech = False
                    silence_frames = 0
                    voiced_frames = 0
                    segment_frames = 0
                    prepended_samples = 0

            preroll.append(frame.pcm)

    def _finalize_segment(
        self,
        cfg: SttConfig,
        speech_buf: list[bytes],
        voiced_frames: int,
        segment_frames: int,
        prepended_samples: int,
    ) -> None:
        if not speech_buf:
            return
        pcm = b"".join(speech_buf)
        audio_np = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32767.0
        segment_audio_np = audio_np[prepended_samples:]
        voiced_ms = voiced_frames * _FRAME_MS
        voiced_ratio = voiced_frames / max(1, segment_frames)
        rms_dbfs = self._rms_dbfs(segment_audio_np)
        if not self._should_transcribe(cfg, voiced_ms, voiced_ratio, rms_dbfs):
            return
        try:
            text = VoskEngine.transcribe(cfg, pcm)
        except Exception as exc:
            self._publisher.publish(ErrorMsg(text=f"Vosk STTエラー({self._role}): {exc}"))
            return
        if text and not should_drop_vosk_transcript(cfg, text) and not self._stop_event.is_set():
            self._publisher.schedule(self._handle_speech(self._role, text))

    @staticmethod
    def _rms_dbfs(audio_np: NDArray[np.float32]) -> float:
        sq = cast(NDArray[np.float32], np.square(audio_np))
        mean_val = cast(np.float64, np.mean(sq))
        rms = math.sqrt(float(mean_val))
        return 20.0 * math.log10(max(rms, 1.0e-6))

    @staticmethod
    def _should_transcribe(cfg: SttConfig, voiced_ms: int, voiced_ratio: float, rms_dbfs: float) -> bool:
        return (
            voiced_ms >= int(cfg.min_voiced_ms)
            and voiced_ratio >= float(cfg.min_voiced_ratio)
            and rms_dbfs >= float(cfg.min_rms_dbfs)
        )


__all__ = [
    "VoskEngine",
    "VoskStage",
    "parse_vosk_text",
    "resolve_vosk_model_path",
    "should_drop_vosk_transcript",
]
