"""Compatibility view of Rust-owned capture, WAV recording, and speech routing."""

from __future__ import annotations

import asyncio
import queue
from datetime import UTC, datetime
from pathlib import Path
from typing import final

from app.audio.base import AudioFrame, RecordingResult
from app.audio.media_transport import MediaCommandError, MediaError, MediaEvent, MediaProcess
from app.core.messages import AudioLevelMsg, ErrorMsg, OutgoingBroadcastFn, StreamInfoMsg
from app.core.publisher import ThreadSafePublisher


@final
class MediaAudioPipeline:
    def __init__(self, device: int | str | None, role: str, broadcast: OutgoingBroadcastFn) -> None:
        if role not in {"self", "other"}:
            raise MediaError()
        self._device = device
        self._role = role
        self._broadcast = broadcast
        self._process: MediaProcess | None = None
        self._publisher: ThreadSafePublisher | None = None
        # Compatibility queues are intentionally empty: no PCM crosses into Python.
        self.stt_queue: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=1)
        self.recording_queue: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=1)
        self.speech_events: queue.Queue[MediaEvent] = queue.Queue(maxsize=256)
        self.speech_failed = False
        self._capture_failed = False
        self._recording_failed = False
        self._recording_path: Path | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._process is not None:
            self.ensure_running()
            return
        self._capture_failed = False
        self._publisher = ThreadSafePublisher(self._broadcast, loop)
        worker = MediaProcess(self._role, self._device, self._event)
        try:
            ready = worker.receive(None, 10)
            if ready.protocol != 1 or ready.rate != 16000 or ready.name is None:
                raise MediaError()
            self._process = worker
            self._publisher.publish(StreamInfoMsg(role=self._role, device=ready.name, rate=16000))
        except Exception:
            worker.close()
            raise

    def _event(self, event: MediaEvent) -> None:
        publisher = self._publisher
        if event.type == "level":
            if event.peak is None:
                raise MediaError()
            if publisher is not None:
                publisher.publish(AudioLevelMsg(role=self._role, level=event.peak))
        elif event.type in {"transcript", "speech_error"}:
            if event.type == "speech_error":
                self.speech_failed = True
            try:
                self.speech_events.put_nowait(event)
            except queue.Full:
                self.speech_failed = True
        else:
            self._recording_failed = True
            if event.type == "capture_error":
                self._capture_failed = True
                self.speech_failed = True
            if publisher is not None:
                publisher.publish(ErrorMsg(text=str(MediaError())))

    def ensure_running(self) -> None:
        if self._capture_failed or self._process is None or not self._process.healthy():
            raise MediaError()

    def request(self, command: dict[str, object], timeout: float = 10) -> MediaEvent:
        self.ensure_running()
        worker = self._process
        assert worker is not None
        try:
            return worker.request(command, timeout)
        except MediaCommandError:
            raise
        except Exception:
            worker.close()
            if self._process is worker:
                self._process = None
            self._event(MediaEvent(type="capture_error"))
            raise

    def flush_stt_queue(self) -> None:
        self.ensure_running()

    def reset_speech_events(self) -> None:
        self.speech_failed = False
        while True:
            try:
                _ = self.speech_events.get_nowait()
            except queue.Empty:
                break

    def start_recording(self, path: Path) -> None:
        if self._recording_path is not None:
            raise MediaError()
        self._recording_path = path
        self._recording_failed = False
        _ = self.request({"op": "start_recording", "path": str(path)})

    def stop_recording(self) -> RecordingResult | None:
        path, self._recording_path = self._recording_path, None
        if path is None:
            return None
        reply = self.request({"op": "stop_recording"})
        recording = reply.recording
        if self._recording_failed or recording is None or recording.ended_ms < recording.started_ms:
            raise MediaError()
        return RecordingResult(
            path,
            recording.size_bytes,
            datetime.fromtimestamp(recording.started_ms / 1000, UTC),
            datetime.fromtimestamp(recording.ended_ms / 1000, UTC),
        )

    def stop(self) -> None:
        worker, self._process = self._process, None
        if worker is None:
            return
        # Recording is finalized by the meeting lifecycle before capture closes.
        # Never block the UI behind an in-flight model preparation.
        worker.close()
        self._publisher = None
        self._recording_path = None
