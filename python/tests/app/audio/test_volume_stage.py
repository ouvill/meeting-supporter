"""Regression tests for wall-clock audio-level throttling."""

import queue
import unittest
from collections.abc import Coroutine, Iterator
from unittest.mock import patch

import numpy as np

from app.audio.base import AudioFrame
from app.audio.stages.volume import VolumeStage
from app.core.messages import AudioLevelMsg, OutgoingMessage


class ScriptedClock:
    def __init__(self, values: list[float]) -> None:
        self._values: Iterator[float] = iter(values)

    def monotonic(self) -> float:
        return next(self._values)


class CollectingPublisher:
    def __init__(self) -> None:
        self.messages: list[OutgoingMessage] = []

    def publish(self, msg: OutgoingMessage) -> None:
        self.messages.append(msg)

    def schedule(self, coro: Coroutine[object, object, object]) -> None:
        coro.close()


def _frame(peak: int, timestamp_ms: float) -> AudioFrame:
    pcm = np.array([peak], dtype=np.int16).tobytes()
    return AudioFrame(pcm=pcm, is_speech=False, timestamp_ms=timestamp_ms)


class VolumeStageTest(unittest.TestCase):
    def test_queued_frames_do_not_bypass_wall_clock_interval(self) -> None:
        in_q: queue.Queue[AudioFrame | None] = queue.Queue()
        for index, peak in enumerate([1000, 2000, 3000, 4000, 5000, 6000]):
            in_q.put(_frame(peak, timestamp_ms=index * 30.0))
        in_q.put(None)

        publisher = CollectingPublisher()
        stage = VolumeStage(in_q, "self", publisher, level_interval_ms=120)
        clock = ScriptedClock([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.120])

        with patch("app.audio.stages.volume.time.monotonic", side_effect=clock.monotonic):
            stage.start()
            stage.join(timeout=1)

        self.assertFalse(stage.running)
        self.assertEqual(len(publisher.messages), 1)
        message = publisher.messages[0]
        self.assertIsInstance(message, AudioLevelMsg)
        if isinstance(message, AudioLevelMsg):
            self.assertEqual(message.role, "self")
            self.assertAlmostEqual(message.level, 6000 / 32767.0)


if __name__ == "__main__":
    _ = unittest.main()
