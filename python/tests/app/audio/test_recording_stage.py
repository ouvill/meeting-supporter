# pyright: reportUninitializedInstanceVariable=false
"""Tests for RecordingStage — WAV writing and discard/drain behaviour."""

import queue
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest import mock

from app.audio.base import AudioFrame
from app.audio.stages.recording import RecordingStage


class RecordingStageTest(unittest.TestCase):
    """RecordingStage WAV writing and discard/drain behaviour."""

    q: queue.Queue[AudioFrame | None]
    stage: RecordingStage

    @override
    def setUp(self) -> None:
        self.q = queue.Queue()
        self.stage = RecordingStage(self.q)

    # ── Start / stop recording ──────────────────────────────────────────────

    def test_start_recording_creates_wav_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.wav"
            self.stage.start_recording(path)
            # The WAV file handle is opened by wave.open(); the header is only
            # flushed to disk on close().  Verify the file exists as a handle.
            self.assertTrue(path.exists())
            _ = self.stage.stop_recording()

    def test_start_recording_creates_parent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subdir" / "test.wav"
            self.stage.start_recording(path)
            self.assertTrue(path.exists())
            _ = self.stage.stop_recording()

    def test_stop_recording_returns_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.wav"
            self.stage.start_recording(path)
            result = self.stage.stop_recording()
            self.assertIsNotNone(result)
            if result is not None:
                self.assertEqual(result.path, path)
                # Empty WAV is exactly 44 bytes (header only)
                self.assertEqual(result.size_bytes, 44)
                self.assertIsNotNone(result.started_at)
                self.assertIsNotNone(result.ended_at)

    def test_stop_recording_when_not_recording_returns_none(self) -> None:
        result = self.stage.stop_recording()
        self.assertIsNone(result)

    # ── Frames are written to WAV when recording ───────────────────────────

    def test_recording_writes_pcm_to_wav(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.wav"
            self.stage.start_recording(path)
            # Write 2 frames of silence (320 samples @ 16-bit = 640 bytes each)
            self.q.put(AudioFrame(pcm=b"\x00\x00" * 320, is_speech=False, timestamp_ms=0.0))
            self.q.put(AudioFrame(pcm=b"\x00\x00" * 320, is_speech=False, timestamp_ms=20.0))
            # Put sentinel to stop the thread
            self.q.put(None)
            self.stage.start()  # starts the thread that drains
            self.stage.join(timeout=2)
            result = self.stage.stop_recording()
            self.assertIsNotNone(result)
            if result is not None:
                # WAV header (44 bytes) + 2 * 640 bytes data = 1324 bytes
                expected_data_size = 2 * 320 * 2  # 2 frames * 320 samples * 2 bytes
                self.assertEqual(result.size_bytes, 44 + expected_data_size)

    # ── Frames are discarded when not recording ────────────────────────────

    def test_discard_frames_after_stop_recording(self) -> None:
        """Frames enqueued after stop_recording are discarded."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.wav"
            self.stage.start_recording(path)
            # Enqueue one frame while recording is active
            self.q.put(AudioFrame(pcm=b"\x00\x00" * 16, is_speech=False, timestamp_ms=0.0))
            # Stop recording — returns result for the first recording period.
            # The WAV is closed; frames already in the queue that were not yet
            # processed (thread not started) will be gone.
            first_result = self.stage.stop_recording()
            self.assertIsNotNone(first_result)
            # Enqueue frames after recording stopped — these should be discarded.
            self.q.put(AudioFrame(pcm=b"\x01\x02" * 16, is_speech=False, timestamp_ms=10.0))
            self.q.put(AudioFrame(pcm=b"\x03\x04" * 16, is_speech=False, timestamp_ms=20.0))
            self.q.put(None)
            self.stage.start()
            self.stage.join(timeout=2)
            # No recording active — stop_recording returns None
            second_result = self.stage.stop_recording()
            self.assertIsNone(second_result)

    # ── Drain without recording ────────────────────────────────────────────

    def test_drain_frames_without_recording(self) -> None:
        """Qc must be drained and not accumulate when recording is inactive."""
        self.q.put(AudioFrame(pcm=b"\x00\x00" * 16, is_speech=False, timestamp_ms=0.0))
        self.q.put(AudioFrame(pcm=b"\x01\x02" * 16, is_speech=False, timestamp_ms=10.0))
        self.q.put(None)
        self.stage.start()
        self.stage.join(timeout=2)
        # After the thread exits, the queue should be empty
        self.assertTrue(self.q.empty())

    # ── Idempotent stop ────────────────────────────────────────────────────

    def test_stop_twice_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.wav"
            self.stage.start_recording(path)
            _ = self.stage.stop_recording()
            result = self.stage.stop_recording()
            self.assertIsNone(result)

    # ── Write error resilience ────────────────────────────────────────────

    def test_write_error_disables_recording_and_drains(self) -> None:
        """A write error disables recording; the stage continues draining."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.wav"
            self.stage.start_recording(path)

            with mock.patch.object(
                self.stage._wav_file,  # pyright: ignore[reportPrivateUsage]; tests internal error resilience
                "writeframes",
                side_effect=OSError("Mock write failure"),
            ):
                # Enqueue frames — first triggers the write error, rest drain
                self.q.put(AudioFrame(pcm=b"\x00\x00" * 16, is_speech=False, timestamp_ms=0.0))
                self.q.put(AudioFrame(pcm=b"\x01\x02" * 16, is_speech=False, timestamp_ms=10.0))
                self.q.put(AudioFrame(pcm=b"\x03\x04" * 16, is_speech=False, timestamp_ms=20.0))
                self.q.put(None)

                self.stage.start()
                self.stage.join(timeout=2)

            # Queue must be fully drained
            self.assertTrue(self.q.empty())

            # Recording must be disabled after write error: stop returns None
            result = self.stage.stop_recording()
            self.assertIsNone(result)


if __name__ == "__main__":
    _ = unittest.main()
