"""VadStage: annotates each frame with is_speech without filtering any frames."""

import hashlib
import importlib
import logging
import math
import queue
from pathlib import Path
from typing import Protocol, cast, final, override

import numpy as np

from app.audio.base import AudioFrame, PipelineStage, put_latest

logger = logging.getLogger(__name__)
_SILERO_MODEL_SHA256 = "c36d490aff5ab924ca6c7aeec4d8f6bd3d22db6fa17611b9c5b17eae58ac3a20"
_SILERO_SAMPLE_RATE = 16_000
_SILERO_WINDOW_SAMPLES = 512
_SILERO_STATE_SHAPE = (2, 1, 64)
_SILERO_MIN_TRANSITION_WINDOWS = math.ceil(0.1 * _SILERO_SAMPLE_RATE / _SILERO_WINDOW_SAMPLES)


class _InferenceSession(Protocol):
    def run(
        self,
        output_names: list[str],
        input_feed: dict[str, np.ndarray[tuple[int, ...], np.dtype[np.float32]]],
    ) -> list[object]: ...


class _InferenceSessionFactory(Protocol):
    def __call__(self, model_path: str, *, providers: list[str]) -> _InferenceSession: ...


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
    """Torch-free Silero VAD using ONNX Runtime and the bundled int8 model."""

    def __init__(self, threshold: float) -> None:
        model_path = Path(__file__).resolve().parents[2] / "resources" / "silero_vad.int8.onnx"
        if not model_path.is_file():
            raise RuntimeError("Silero VADモデルが見つかりません。")
        with model_path.open("rb") as model_file:
            digest = hashlib.file_digest(model_file, "sha256").hexdigest()
        if digest != _SILERO_MODEL_SHA256:
            raise RuntimeError("Silero VADモデルを検証できませんでした。")

        try:
            module = importlib.import_module("onnxruntime")
            session_factory = cast(_InferenceSessionFactory, getattr(module, "InferenceSession"))
            self._session = session_factory(str(model_path), providers=["CPUExecutionProvider"])
        except (ImportError, OSError, RuntimeError) as error:
            raise RuntimeError("Silero VADを初期化できませんでした。") from error

        self._threshold: float = min(max(float(threshold), 0.05), 0.95)
        self._window_bytes: int = _SILERO_WINDOW_SAMPLES * 2
        self._pending_pcm = bytearray()
        self._speech_detected = False
        self._silence_windows = 0
        self._state_h = np.zeros(_SILERO_STATE_SHAPE, dtype=np.float32)
        self._state_c = np.zeros(_SILERO_STATE_SHAPE, dtype=np.float32)

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        if sample_rate != _SILERO_SAMPLE_RATE:
            return False
        self._pending_pcm.extend(frame)
        try:
            while len(self._pending_pcm) >= self._window_bytes:
                window = bytes(self._pending_pcm[: self._window_bytes])
                del self._pending_pcm[: self._window_bytes]
                samples = np.frombuffer(window, dtype=np.int16).astype(np.float32) / 32768.0
                probability = self._infer_probability(samples)
                if probability >= self._threshold:
                    self._speech_detected = True
                    self._silence_windows = 0
                else:
                    self._silence_windows += 1
                    if self._silence_windows >= _SILERO_MIN_TRANSITION_WINDOWS:
                        self._speech_detected = False
        except Exception:
            logger.exception("Silero VAD推論に失敗しました")
            self._reset_state()
        return self._speech_detected

    def _infer_probability(self, samples: np.ndarray[tuple[int], np.dtype[np.float32]]) -> float:
        outputs = self._session.run(
            ["prob", "new_h", "new_c"],
            {
                "x": samples.reshape(1, _SILERO_WINDOW_SAMPLES),
                "h": self._state_h,
                "c": self._state_c,
            },
        )
        if len(outputs) != 3 or not all(isinstance(output, np.ndarray) for output in outputs):
            raise RuntimeError("Silero VADの出力が不正です。")

        probability_output, new_h, new_c = outputs
        assert isinstance(probability_output, np.ndarray)
        assert isinstance(new_h, np.ndarray)
        assert isinstance(new_c, np.ndarray)
        if probability_output.size != 1 or new_h.shape != _SILERO_STATE_SHAPE or new_c.shape != _SILERO_STATE_SHAPE:
            raise RuntimeError("Silero VADの出力shapeが不正です。")
        if probability_output.dtype != np.float32 or new_h.dtype != np.float32 or new_c.dtype != np.float32:
            raise RuntimeError("Silero VADの出力型が不正です。")
        if not bool(np.isfinite(new_h).all()) or not bool(np.isfinite(new_c).all()):
            raise RuntimeError("Silero VADの状態が不正です。")

        probability = float(probability_output.item())
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise RuntimeError("Silero VADの確率が不正です。")
        self._state_h = new_h
        self._state_c = new_c
        return probability

    def _reset_state(self) -> None:
        self._speech_detected = False
        self._silence_windows = 0
        self._state_h = np.zeros(_SILERO_STATE_SHAPE, dtype=np.float32)
        self._state_c = np.zeros(_SILERO_STATE_SHAPE, dtype=np.float32)


class VadStage(PipelineStage):
    """Annotates each frame with the selected VAD engine and forwards it once."""

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
                return
            put_latest(
                self._out_q,
                AudioFrame(
                    pcm=frame.pcm,
                    is_speech=self._engine.is_speech(frame.pcm, self._sample_rate),
                    timestamp_ms=frame.timestamp_ms,
                ),
            )


__all__ = ["SileroVadEngine", "VadEngine", "VadStage", "WebRtcVadEngine"]
