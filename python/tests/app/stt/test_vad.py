"""Contracts for frame annotation with WebRTC and Torch-free Silero VAD."""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast, final
from unittest.mock import patch

import numpy as np
from numpy.typing import NDArray

from app.stt.stages.vad import SileroVadEngine

_SILERO_SHA256 = "c36d490aff5ab924ca6c7aeec4d8f6bd3d22db6fa17611b9c5b17eae58ac3a20"
_LOUD_PCM = (12000).to_bytes(2, "little", signed=True) * 480


@final
class _SileroConfig:
    def __init__(self) -> None:
        self.model = ""
        self.threshold = 0.5
        self.min_silence_duration = 0.0
        self.min_speech_duration = 0.0
        self.window_size = 512


@final
class _VadConfig:
    def __init__(self) -> None:
        self.silero_vad = _SileroConfig()
        self.sample_rate = 0

    def validate(self) -> bool:
        return Path(self.silero_vad.model).is_file() and self.sample_rate == 16_000


@final
class _Detector:
    def __init__(self, config: _VadConfig, *, buffer_size_in_seconds: float) -> None:
        self.config = config
        self.buffer_size_in_seconds = buffer_size_in_seconds
        self.accepted: list[NDArray[np.float32]] = []
        self.detected = False

    def accept_waveform(self, samples: NDArray[np.float32]) -> None:
        self.accepted.append(samples)
        peak = cast(np.float32, np.max(np.abs(samples)))
        self.detected = float(peak) > 0.1

    def is_speech_detected(self) -> bool:
        return self.detected

    def empty(self) -> bool:
        return True

    def pop(self) -> None:
        raise AssertionError("No completed segments are queued by this fake")


class SileroVadEngineTest(unittest.TestCase):
    def test_bundled_model_matches_the_pinned_artifact(self) -> None:
        model_path = Path(__file__).resolve().parents[3] / "app" / "resources" / "silero_vad.int8.onnx"
        assert model_path.stat().st_size == 212_860
        assert hashlib.sha256(model_path.read_bytes()).hexdigest() == _SILERO_SHA256

    def test_buffers_30ms_frames_into_silero_windows_without_torch(self) -> None:
        detectors: list[_Detector] = []

        def create_detector(config: _VadConfig, *, buffer_size_in_seconds: float) -> _Detector:
            detector = _Detector(config, buffer_size_in_seconds=buffer_size_in_seconds)
            detectors.append(detector)
            return detector

        module = SimpleNamespace(
            VadModelConfig=_VadConfig,
            VoiceActivityDetector=create_detector,
        )
        with patch("app.stt.stages.vad.import_sherpa_onnx", return_value=module):
            engine = SileroVadEngine(0.4)

        assert engine.is_speech(_LOUD_PCM, 16_000) is False
        assert engine.is_speech(_LOUD_PCM, 16_000) is True
        assert len(detectors) == 1
        detector = detectors[0]
        assert detector.config.silero_vad.threshold == 0.4
        assert detector.config.silero_vad.min_silence_duration == 0.1
        assert detector.config.silero_vad.min_speech_duration == 0.1
        assert detector.buffer_size_in_seconds == 30.0
        assert len(detector.accepted) == 1
        assert detector.accepted[0].shape == (512,)

    def test_rejects_unsupported_sample_rates_without_inference(self) -> None:
        module = SimpleNamespace(
            VadModelConfig=_VadConfig,
            VoiceActivityDetector=_Detector,
        )
        with patch("app.stt.stages.vad.import_sherpa_onnx", return_value=module):
            engine = SileroVadEngine(0.4)

        assert engine.is_speech(_LOUD_PCM, 48_000) is False


if __name__ == "__main__":
    _ = unittest.main()
