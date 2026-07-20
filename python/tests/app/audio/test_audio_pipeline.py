"""Tests for app.audio.pipeline — AudioPipeline lifecycle."""

import asyncio
import queue
import tempfile
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import override

import numpy as np
from numpy.typing import NDArray

from app.audio.base import AudioFrame, put_latest
from app.audio.pipeline import AudioPipeline
from app.core.messages import OutgoingMessage


class FakeReader:
    def __init__(self, frames: list[bytes]) -> None:
        self._frames: list[bytes] = frames
        self._index: int = 0

    def read(self, _numframes: int) -> NDArray[np.int16]:
        if self._index >= len(self._frames):
            return np.array([], dtype=np.int16)
        frame = self._frames[self._index]
        self._index += 1
        return np.frombuffer(frame, dtype=np.int16)


class FakeSource:
    name: str = "Fake Microphone"
    sample_rate: int = 16000

    def __init__(self, frames: list[bytes] | None = None) -> None:
        self._frames: list[bytes] = frames or []

    @contextmanager
    def open(self) -> Generator[FakeReader, None, None]:
        yield FakeReader(self._frames)


class RefillingQueue(queue.Queue[AudioFrame | None]):
    """Models another producer refilling the queue during put_latest()."""

    def __init__(self) -> None:
        super().__init__(maxsize=1)
        self.put_attempts: int = 0
        self.get_attempts: int = 0

    @override
    def put_nowait(self, item: AudioFrame | None) -> None:
        _ = item
        self.put_attempts += 1
        raise queue.Full

    @override
    def get_nowait(self) -> AudioFrame | None:
        self.get_attempts += 1
        return AudioFrame(pcm=b"old", is_speech=False, timestamp_ms=0.0)


class AudioQueueTest(unittest.TestCase):
    def test_put_latest_drops_incoming_frame_when_queue_is_refilled(self) -> None:
        q = RefillingQueue()

        put_latest(q, AudioFrame(pcm=b"new", is_speech=False, timestamp_ms=1.0))

        self.assertEqual(q.get_attempts, 1)
        self.assertEqual(q.put_attempts, 2)


class FakeBroadcast:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.stream_info_received: asyncio.Event = asyncio.Event()

    async def __call__(self, msg: OutgoingMessage) -> None:
        dumped = msg.model_dump()
        self.messages.append(dumped)
        if dumped.get("type") == "stream_info":
            self.stream_info_received.set()


class AudioPipelineTest(unittest.IsolatedAsyncioTestCase):
    def _make_pipeline(self, frames: list[bytes] | None = None) -> AudioPipeline:
        return AudioPipeline(
            source=FakeSource(frames),  # pyright: ignore[reportArgumentType]
            role="other",
            broadcast_fn=FakeBroadcast(),
        )

    async def test_start_and_stop_are_idempotent_public_lifecycle_operations(self) -> None:
        broadcast = FakeBroadcast()
        p = AudioPipeline(
            source=FakeSource(),  # pyright: ignore[reportArgumentType]
            role="self",
            broadcast_fn=broadcast,
        )
        loop = asyncio.get_running_loop()

        p.start(loop)
        p.start(loop)
        _ = await asyncio.wait_for(broadcast.stream_info_received.wait(), timeout=1)
        _ = p.stop()
        _ = p.stop()

        stream_infos = [message for message in broadcast.messages if message.get("type") == "stream_info"]
        self.assertEqual(
            stream_infos,
            [
                {
                    "type": "stream_info",
                    "role": "self",
                    "device": "Fake Microphone",
                    "rate": 16000,
                }
            ],
        )

    async def test_flush_stt_queue_drains_public_queue(self) -> None:
        p = self._make_pipeline()
        p.stt_queue.put(AudioFrame(pcm=b"123", is_speech=False, timestamp_ms=0.0))
        p.stt_queue.put(AudioFrame(pcm=b"456", is_speech=False, timestamp_ms=1.0))
        p.flush_stt_queue()
        self.assertTrue(p.stt_queue.empty())

    async def test_restart_recreates_public_stt_queue(self) -> None:
        p = self._make_pipeline()
        loop = asyncio.get_running_loop()
        p.start(loop)
        first_queue = p.stt_queue
        p.stop()
        p.start(loop)
        self.assertIsNot(p.stt_queue, first_queue)
        p.stop()

    # ── Recording ──────────────────────────────────────────────────────────

    async def test_stop_recording_returns_result_when_active(self) -> None:
        p = self._make_pipeline()
        loop = asyncio.get_running_loop()
        p.start(loop)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.wav"
            p.start_recording(path)
            result = p.stop_recording()
            self.assertIsNotNone(result)
            if result is not None:
                self.assertEqual(result.path, path)
                self.assertGreater(result.size_bytes, 0)

        p.stop()

    async def test_stop_recording_returns_none_when_inactive(self) -> None:
        p = self._make_pipeline()
        loop = asyncio.get_running_loop()
        p.start(loop)
        result = p.stop_recording()
        self.assertIsNone(result)
        p.stop()


if __name__ == "__main__":
    _ = unittest.main()
