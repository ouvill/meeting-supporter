"""Contracts for frame annotation with WebRTC and Torch-free Silero VAD."""

from __future__ import annotations

import hashlib
import queue
import unittest
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast, final
from unittest.mock import patch

import numpy as np

from app.audio.base import AudioFrame
from app.stt.stages.vad import SileroVadEngine, VadStage

_SILERO_SHA256 = "c36d490aff5ab924ca6c7aeec4d8f6bd3d22db6fa17611b9c5b17eae58ac3a20"
_LOUD_PCM = (12000).to_bytes(2, "little", signed=True) * 480
_LOUD_WINDOW = (12000).to_bytes(2, "little", signed=True) * 512
_SILENCE_WINDOW = b"\x00\x00" * 512


@final
class _Session:
    def __init__(self, *, invalid_state: bool = False) -> None:
        self.inputs: list[dict[str, np.ndarray[tuple[int, ...], np.dtype[np.float32]]]] = []
        self.invalid_state = invalid_state

    def run(
        self,
        output_names: list[str],
        input_feed: dict[str, np.ndarray[tuple[int, ...], np.dtype[np.float32]]],
    ) -> list[object]:
        assert output_names == ["prob", "new_h", "new_c"]
        self.inputs.append(input_feed)
        peak_value = cast(np.float32, np.max(np.abs(input_feed["x"])))
        peak = float(peak_value)
        probability = np.array([[0.9 if peak > 0.1 else 0.0]], dtype=np.float32)
        new_h = input_feed["h"] + np.float32(1.0)
        new_c = input_feed["c"] + np.float32(1.0)
        if self.invalid_state:
            new_h[0, 0, 0] = np.float32(np.nan)
        return [probability, new_h, new_c]


def _module_for_session(session: _Session) -> SimpleNamespace:
    def create_session(_model_path: str, *, providers: list[str]) -> _Session:
        assert providers == ["CPUExecutionProvider"]
        return session

    return SimpleNamespace(InferenceSession=create_session)


@final
class _SequenceVadEngine:
    def __init__(self, results: list[bool]) -> None:
        self._results: Iterator[bool] = iter(results)

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        del frame, sample_rate
        return next(self._results)


class VadStageTest(unittest.TestCase):
    def test_forwards_each_frame_once_with_engine_annotation(self) -> None:
        in_q: queue.Queue[AudioFrame | None] = queue.Queue()
        out_q: queue.Queue[AudioFrame | None] = queue.Queue()
        frames = [
            AudioFrame(
                pcm=index.to_bytes(2, "little", signed=True) * 480,
                is_speech=False,
                timestamp_ms=index * 30.0,
            )
            for index in range(6)
        ]
        for frame in frames:
            in_q.put(frame)
        in_q.put(None)

        stage = VadStage(
            in_q,
            out_q,
            _SequenceVadEngine([False, False, True, True, False, False]),
            16_000,
        )
        stage.start()
        stage.join(timeout=1)

        output: list[AudioFrame] = []
        while True:
            item = out_q.get_nowait()
            if item is None:
                break
            output.append(item)

        self.assertFalse(stage.running)
        self.assertEqual([frame.pcm for frame in output], [frame.pcm for frame in frames])
        self.assertEqual([frame.is_speech for frame in output], [False, False, True, True, False, False])


class SileroVadEngineTest(unittest.TestCase):
    def test_bundled_model_matches_the_pinned_artifact(self) -> None:
        model_path = Path(__file__).resolve().parents[3] / "app" / "resources" / "silero_vad.int8.onnx"
        assert model_path.stat().st_size == 212_860
        assert hashlib.sha256(model_path.read_bytes()).hexdigest() == _SILERO_SHA256

    def test_buffers_30ms_frames_and_detects_speech_on_first_complete_window(self) -> None:
        sessions: list[_Session] = []

        def create_session(model_path: str, *, providers: list[str]) -> _Session:
            assert Path(model_path).is_file()
            assert providers == ["CPUExecutionProvider"]
            session = _Session()
            sessions.append(session)
            return session

        module = SimpleNamespace(InferenceSession=create_session)
        with patch("app.stt.stages.vad.importlib.import_module", return_value=module):
            engine = SileroVadEngine(0.4)

        results = [engine.is_speech(_LOUD_PCM, 16_000) for _ in range(5)]
        assert results == [False, True, True, True, True]
        assert len(sessions) == 1
        session = sessions[0]
        assert len(session.inputs) == 4
        assert session.inputs[0]["x"].shape == (1, 512)
        assert np.count_nonzero(session.inputs[0]["h"]) == 0
        last_state_min = np.min(session.inputs[-1]["h"])
        last_state_max = np.max(session.inputs[-1]["h"])
        assert float(last_state_min) == 3.0
        assert float(last_state_max) == 3.0

    def test_keeps_speech_active_until_four_silero_silence_windows(self) -> None:
        session = _Session()
        module = _module_for_session(session)
        with patch("app.stt.stages.vad.importlib.import_module", return_value=module):
            engine = SileroVadEngine(0.4)

        assert engine.is_speech(_LOUD_WINDOW, 16_000) is True
        assert [engine.is_speech(_SILENCE_WINDOW, 16_000) for _ in range(4)] == [True, True, True, False]

    def test_rejects_non_finite_recurrent_state_and_resets(self) -> None:
        session = _Session(invalid_state=True)
        module = _module_for_session(session)
        with (
            patch("app.stt.stages.vad.logger.exception") as log_exception,
            patch("app.stt.stages.vad.importlib.import_module", return_value=module),
        ):
            engine = SileroVadEngine(0.4)
            assert engine.is_speech(_LOUD_WINDOW, 16_000) is False

        session.invalid_state = False
        assert engine.is_speech(_LOUD_WINDOW, 16_000) is True
        assert np.count_nonzero(session.inputs[-1]["h"]) == 0
        log_exception.assert_called_once()

    def test_rejects_unsupported_sample_rates_without_inference(self) -> None:
        session = _Session()
        module = _module_for_session(session)
        with patch("app.stt.stages.vad.importlib.import_module", return_value=module):
            engine = SileroVadEngine(0.4)

        assert engine.is_speech(_LOUD_PCM, 48_000) is False
        assert session.inputs == []


if __name__ == "__main__":
    _ = unittest.main()
