# pyright: reportUninitializedInstanceVariable=false
"""Tests for RecordingService — asset generation and graceful missing pipeline."""

import asyncio
import queue
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import override

from app.audio.base import AudioFrame, RecordingResult
from app.meetings.recording import RecordingService


class FakePipeline:
    """Minimal AudioPipelineLike fake for recording tests."""

    def __init__(self) -> None:
        self._recording_path: Path | None = None
        self._recording_result: RecordingResult | None = None
        self.start_called: bool = False
        self.stop_called: bool = False

    @property
    def stt_queue(self) -> queue.Queue[AudioFrame | None]:
        return queue.Queue()

    @property
    def recording_queue(self) -> queue.Queue[AudioFrame | None]:
        return queue.Queue()

    def flush_stt_queue(self) -> None: ...
    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        _ = loop

    def stop(self) -> None: ...

    def start_recording(self, path: Path) -> None:
        self.start_called = True
        self._recording_path = path
        # Create the file so stat() works
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(b"dummy wav content")

    def stop_recording(self) -> RecordingResult | None:
        self.stop_called = True
        if self._recording_path is None:
            return None
        now = datetime.now(UTC)
        return RecordingResult(
            path=self._recording_path,
            size_bytes=self._recording_path.stat().st_size if self._recording_path.exists() else 0,
            started_at=now,
            ended_at=now,
        )


class RecordingServiceTest(unittest.IsolatedAsyncioTestCase):
    """RecordingService asset generation and graceful missing pipeline behaviour."""

    _tmpdir: tempfile.TemporaryDirectory[str]
    user_data_dir: Path
    service: RecordingService

    @override
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.user_data_dir = Path(self._tmpdir.name)
        self.service = RecordingService(self.user_data_dir)

    @override
    async def asyncTearDown(self) -> None:
        self._tmpdir.cleanup()

    # ── Start / stop with both pipelines ───────────────────────────────────

    async def test_start_recording_other_and_self(self) -> None:
        other = FakePipeline()
        self_pipe = FakePipeline()
        await self.service.start_recording("meeting-1", other, self_pipe)

        recordings_dir = self.user_data_dir / "recordings" / "meeting-1"
        self.assertTrue((recordings_dir / "other.wav").exists())
        self.assertTrue((recordings_dir / "self.wav").exists())
        self.assertTrue(other.start_called)
        self.assertTrue(self_pipe.start_called)

    async def test_stop_recording_returns_assets(self) -> None:
        other = FakePipeline()
        self_pipe = FakePipeline()
        await self.service.start_recording("meeting-2", other, self_pipe)
        assets = await self.service.stop_recording("meeting-2", other, self_pipe)

        self.assertEqual(len(assets), 2)

        asset_other = [a for a in assets if a.role == "other"][0]
        self.assertEqual(asset_other.meeting_id, "meeting-2")
        self.assertEqual(asset_other.relative_path, "recordings/meeting-2/other.wav")
        self.assertEqual(asset_other.format, "wav")
        self.assertGreater(asset_other.size_bytes or 0, 0)
        self.assertIsNotNone(asset_other.started_at)
        self.assertIsNotNone(asset_other.ended_at)

        asset_self = [a for a in assets if a.role == "self"][0]
        self.assertEqual(asset_self.meeting_id, "meeting-2")
        self.assertEqual(asset_self.relative_path, "recordings/meeting-2/self.wav")

    # ── Graceful missing self pipeline ─────────────────────────────────────

    async def test_other_only_when_self_pipeline_none(self) -> None:
        other = FakePipeline()
        await self.service.start_recording("meeting-3", other, audio_self=None)
        assets = await self.service.stop_recording("meeting-3", other, audio_self=None)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].role, "other")
        self.assertTrue(other.stop_called)

    async def test_no_pipelines_returns_empty(self) -> None:
        assets = await self.service.stop_recording("meeting-4", audio_other=None, audio_self=None)
        self.assertEqual(len(assets), 0)

    # ── Pipeline failure does not block ────────────────────────────────────

    async def test_pipeline_that_raises_on_start_is_skipped(self) -> None:
        class BrokenPipeline(FakePipeline):
            @override
            def start_recording(self, path: Path) -> None:
                msg = "Simulated failure"
                raise RuntimeError(msg)

        other = BrokenPipeline()
        self_pipe = FakePipeline()
        # Should not raise despite BrokenPipeline failure
        await self.service.start_recording("meeting-5", other, self_pipe)
        assets = await self.service.stop_recording("meeting-5", other, self_pipe)
        # self pipeline should still produce an asset
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].role, "self")


if __name__ == "__main__":
    _ = unittest.main()
