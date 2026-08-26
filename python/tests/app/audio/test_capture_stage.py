"""Regression tests for realtime audio capture cadence."""

import queue
import threading
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from typing import override
from unittest.mock import patch

import numpy as np
from numpy.typing import NDArray

from app.audio.base import AudioFrame
from app.audio.stages.capture import CaptureStage


class FakeClock:
    def __init__(self) -> None:
        self.now: float = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class AdvancingStopEvent(threading.Event):
    def __init__(self, clock: FakeClock, stop_after_waits: int) -> None:
        super().__init__()
        self._clock: FakeClock = clock
        self._stop_after_waits: int = stop_after_waits
        self.waits: list[float] = []

    @override
    def wait(self, timeout: float | None = None) -> bool:
        if timeout is None:
            raise AssertionError("capture cadence waits must have a deadline")
        self.waits.append(timeout)
        self._clock.advance(timeout)
        if len(self.waits) >= self._stop_after_waits:
            self.set()
        return self.is_set()


class ImmediateReader:
    def __init__(self) -> None:
        self.read_count: int = 0
        self.requested_frames: list[int] = []

    def read(self, numframes: int) -> NDArray[np.float32]:
        self.read_count += 1
        self.requested_frames.append(numframes)
        return np.full(numframes, 0.5, dtype=np.float32)


class TimedReader(ImmediateReader):
    def __init__(self, clock: FakeClock, read_durations: list[float]) -> None:
        super().__init__()
        self._clock: FakeClock = clock
        self._read_durations: list[float] = list(read_durations)

    @override
    def read(self, numframes: int) -> NDArray[np.float32]:
        if self._read_durations:
            self._clock.advance(self._read_durations.pop(0))
        return super().read(numframes)


class ImmediateSource:
    name: str = "Immediate Fake Microphone"
    sample_rate: int

    def __init__(self, reader: ImmediateReader, sample_rate: int = 16000) -> None:
        self._reader: ImmediateReader = reader
        self.sample_rate = sample_rate

    @contextmanager
    def open(self) -> Generator[ImmediateReader, None, None]:
        yield self._reader


class CaptureStageTest(unittest.TestCase):
    def test_immediate_reader_is_paced_to_realtime(self) -> None:
        clock = FakeClock()
        stop_event = AdvancingStopEvent(clock, stop_after_waits=4)
        reader = ImmediateReader()
        out_q: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=10)
        stage = CaptureStage(
            ImmediateSource(reader),
            out_q,
        )
        stage._stop_event = stop_event  # pyright: ignore[reportPrivateUsage]

        with patch("app.audio.stages.capture.time.monotonic", side_effect=clock.monotonic):
            stage.start()
            stage.join(timeout=1)

        self.assertFalse(stage.running)
        self.assertEqual(reader.read_count, 5)
        self.assertEqual(len(stop_event.waits), 4)
        for wait_seconds in stop_event.waits:
            self.assertAlmostEqual(wait_seconds, 0.03)

        frames = [out_q.get_nowait() for _ in range(out_q.qsize())]
        self.assertTrue(all(frame is not None for frame in frames))
        timestamps = [frame.timestamp_ms for frame in frames if frame is not None]
        self.assertEqual(len(timestamps), 4)
        for actual, expected in zip(timestamps, [0.0, 30.0, 60.0, 90.0], strict=True):
            self.assertAlmostEqual(actual, expected)

    def test_slow_read_is_not_followed_by_an_immediate_frame(self) -> None:
        clock = FakeClock()
        stop_event = AdvancingStopEvent(clock, stop_after_waits=2)
        reader = TimedReader(clock, read_durations=[0.0, 0.08, 0.0, 0.0])
        out_q: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=10)
        stage = CaptureStage(ImmediateSource(reader), out_q)
        stage._stop_event = stop_event  # pyright: ignore[reportPrivateUsage]

        with patch("app.audio.stages.capture.time.monotonic", side_effect=clock.monotonic):
            stage.start()
            stage.join(timeout=1)

        frames = [out_q.get_nowait() for _ in range(out_q.qsize())]
        timestamps = [frame.timestamp_ms for frame in frames if frame is not None]
        self.assertEqual(reader.read_count, 4)
        self.assertEqual(len(timestamps), 3)
        for actual, expected in zip(timestamps, [0.0, 80.0, 110.0], strict=True):
            self.assertAlmostEqual(actual, expected)

    def test_low_positive_sample_rate_still_has_positive_cadence(self) -> None:
        clock = FakeClock()
        stop_event = AdvancingStopEvent(clock, stop_after_waits=1)
        reader = ImmediateReader()
        out_q: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=10)
        stage = CaptureStage(ImmediateSource(reader, sample_rate=1), out_q)
        stage._stop_event = stop_event  # pyright: ignore[reportPrivateUsage]

        with patch("app.audio.stages.capture.time.monotonic", side_effect=clock.monotonic):
            stage.start()
            stage.join(timeout=1)

        self.assertEqual(reader.requested_frames, [1, 1])
        self.assertEqual(stop_event.waits, [1.0])
        self.assertEqual(out_q.qsize(), 1)

    def test_non_positive_sample_rate_is_rejected(self) -> None:
        out_q: queue.Queue[AudioFrame | None] = queue.Queue()
        with self.assertRaisesRegex(ValueError, "sample_rate must be positive"):
            _ = CaptureStage(ImmediateSource(ImmediateReader(), sample_rate=0), out_q)


if __name__ == "__main__":
    _ = unittest.main()
