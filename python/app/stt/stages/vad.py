"""VadStage: annotates each frame with is_speech without filtering any frames."""

import hashlib
import logging
import queue
from pathlib import Path
from typing import Protocol, cast, final, override

import numpy as np

from app.audio.base import AudioFrame, PipelineStage, put_latest
from app.stt.sherpa_runtime import import_sherpa_onnx

logger = logging.getLogger(__name__)
_SILERO_MODEL_SHA256 = "c36d490aff5ab924ca6c7aeec4d8f6bd3d22db6fa17611b9c5b17eae58ac3a20"
_SILERO_SAMPLE_RATE = 16_000


class _SileroVadConfig(Protocol):
    model: str
    threshold: float
    min_silence_duration: float
    min_speech_duration: float
    window_size: int


class _VadModelConfig(Protocol):
    silero_vad: _SileroVadConfig
    sample_rate: int

    def validate(self) -> bool: ...


class _VadModelConfigFactory(Protocol):
    def __call__(self) -> _VadModelConfig: ...


class _VoiceActivityDetector(Protocol):
    def accept_waveform(self, samples: np.ndarray[tuple[int], np.dtype[np.float32]]) -> None: ...

    def is_speech_detected(self) -> bool: ...

    def empty(self) -> bool: ...

    def pop(self) -> None: ...


class _VoiceActivityDetectorFactory(Protocol):
    def __call__(
        self,
        config: _VadModelConfig,
        *,
        buffer_size_in_seconds: float,
    ) -> _VoiceActivityDetector: ...


class VadEngine(Protocol):
    def is_speech(self, frame: bytes, sample_rate: int) -> bool: ...


class WebRtcVadEngine:
    """webrtcvad-backed VAD engine (MVP default)."""

    def __init__(self, aggressiveness: int) -> None:
        import webrtcvad

        self._vad: webrtcvad.Vad = webrtcvad.Vad(aggressiveness)

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        try:
            return self._vad.is_speech(frame, sample_rate)
        except Exception:
            return False


@final
class SileroVadEngine:
    """Torch-free Silero VAD using the bundled 16 kHz int8 ONNX model."""

    def __init__(self, threshold: float) -> None:
        model_path = Path(__file__).resolve().parents[2] / "resources" / "silero_vad.int8.onnx"
        if not model_path.is_file():
            raise RuntimeError("Silero VADモデルが見つかりません。")
        with model_path.open("rb") as model_file:
            digest = hashlib.file_digest(model_file, "sha256").hexdigest()
        if digest != _SILERO_MODEL_SHA256:
            raise RuntimeError("Silero VADモデルを検証できませんでした。")

        module = import_sherpa_onnx()
        config_factory = cast(_VadModelConfigFactory, getattr(module, "VadModelConfig"))
        detector_factory = cast(
            _VoiceActivityDetectorFactory,
            getattr(module, "VoiceActivityDetector"),
        )
        config = config_factory()
        config.silero_vad.model = str(model_path)
        config.silero_vad.threshold = min(max(float(threshold), 0.05), 0.95)
        config.silero_vad.min_silence_duration = 0.1
        config.silero_vad.min_speech_duration = 0.1
        config.sample_rate = _SILERO_SAMPLE_RATE
        if not config.validate():
            raise RuntimeError("Silero VADを初期化できませんでした。")

        self._window_size: int = config.silero_vad.window_size
        self._window_bytes: int = self._window_size * 2
        self._pending_pcm = bytearray()
        self._speech_detected = False
        self._vad = detector_factory(config, buffer_size_in_seconds=30.0)

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        if sample_rate != _SILERO_SAMPLE_RATE:
            return False
        self._pending_pcm.extend(frame)
        try:
            while len(self._pending_pcm) >= self._window_bytes:
                window = bytes(self._pending_pcm[: self._window_bytes])
                del self._pending_pcm[: self._window_bytes]
                samples = np.frombuffer(window, dtype=np.int16).astype(np.float32) / 32768.0
                self._vad.accept_waveform(samples)
                self._speech_detected = bool(self._vad.is_speech_detected())
                while not self._vad.empty():
                    self._vad.pop()
        except Exception:
            logger.exception("Silero VAD推論に失敗しました")
            self._speech_detected = False
        return self._speech_detected


class VadStage(PipelineStage):
    """Reads frames from in_q, stamps is_speech, and forwards all frames to out_q.

    The engine is swappable at construction time (WebRtcVadEngine or future alternatives).
    All frames pass through — STT stages decide how to use the is_speech flag.
    """

    def __init__(
        self,
        in_q: queue.Queue[AudioFrame | None],
        out_q: queue.Queue[AudioFrame | None],
        engine: VadEngine,
        sample_rate: int,
    ) -> None:
        super().__init__()
        self._in_q: queue.Queue[AudioFrame | None] = in_q
        self._out_q: queue.Queue[AudioFrame | None] = out_q
        self._engine: VadEngine = engine
        self._sample_rate: int = sample_rate

    @override
    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                frame = self._in_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if frame is None:
                self._out_q.put(None)
                break
            put_latest(
                self._out_q,
                AudioFrame(
                    pcm=frame.pcm,
                    is_speech=self._engine.is_speech(frame.pcm, self._sample_rate),
                    timestamp_ms=frame.timestamp_ms,
                ),
            )


__all__ = ["SileroVadEngine", "VadEngine", "VadStage", "WebRtcVadEngine"]
