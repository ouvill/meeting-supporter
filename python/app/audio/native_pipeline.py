"""Existing meeting adapter for Rust capture, peak levels and WAV ownership."""

from __future__ import annotations

import asyncio
import queue
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import final

from app.audio.base import AudioFrame, RecordingResult, put_latest
from app.audio.native_transport import AudioProcess, Event, NativeAudioError, NativeRecordingError, list_devices
from app.core.messages import AudioLevelMsg, ErrorMsg, OutgoingBroadcastFn, StreamInfoMsg
from app.core.publisher import ThreadSafePublisher
from app.core.types import InputDevice


def get_native_devices() -> list[InputDevice]:
    return [
        {
            "index": d.index,
            "name": d.name,
            "is_monitor": d.is_monitor,
            "is_default": d.is_default,
            "hostapi": d.hostapi,
            "capture": d.capture,
        }
        for d in list_devices()
    ]


@final
class NativeAudioPipeline:
    def __init__(self, device: int | str | None, role: str, broadcast: OutgoingBroadcastFn) -> None:
        if role not in {"self", "other"}:
            raise NativeAudioError()
        self._device = device
        self._role = role
        self._broadcast = broadcast
        self._process: AudioProcess | None = None
        self._publisher: ThreadSafePublisher | None = None
        self._stt_queue: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=200)
        self._recording_queue: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=1)
        self._recording_path: Path | None = None
        self._recording_failed = False
        self._capture_failed = False
        self._last_level = 0.0
        self._peak = 0.0

    @property
    def stt_queue(self) -> queue.Queue[AudioFrame | None]:
        return self._stt_queue

    @property
    def recording_queue(self) -> queue.Queue[AudioFrame | None]:
        # Compatibility only; Rust owns the recording branch.
        return self._recording_queue

    def ensure_running(self) -> None:
        if self._capture_failed or self._process is None or self._process.process.poll() is not None:
            raise NativeAudioError()

    def flush_stt_queue(self) -> None:
        if self._capture_failed:
            raise NativeAudioError()
        for _ in range(self._stt_queue.qsize()):
            try:
                _ = self._stt_queue.get_nowait()
            except queue.Empty:
                break

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._process is not None:
            self.ensure_running()
            return
        self._capture_failed = False
        self.flush_stt_queue()
        self._publisher = ThreadSafePublisher(self._broadcast, loop)
        arguments = ["--role", self._role]
        if self._device is not None:
            arguments += ["--device", str(self._device)]
        process = AudioProcess(arguments, self._event)
        try:
            response = process.receive("ready")
            if response.protocol != 1 or response.rate != 16000 or response.name is None:
                raise NativeAudioError()
            self._publisher.publish(StreamInfoMsg(role=self._role, device=response.name, rate=16000))
            self._process = process
        except Exception:
            process.close()
            raise NativeAudioError() from None

    def _event(self, event: Event, pcm: bytes) -> None:
        publisher = self._publisher
        if event.type == "audio":
            put_latest(self._stt_queue, AudioFrame(pcm, False, time.monotonic() * 1000, sequence=event.sequence))
            self._peak = max(self._peak, event.peak or 0)
            now = time.monotonic()
            if publisher is not None and now - self._last_level >= 0.12:
                publisher.publish(AudioLevelMsg(role=self._role, level=self._peak))
                self._last_level, self._peak = now, 0.0
        else:
            self._recording_failed = True
            if event.type == "error":
                self._capture_failed = True
                # An invalid input frame retires the speech session, including partial audio.
                put_latest(self._stt_queue, AudioFrame(b"", False, time.monotonic() * 1000))
            if publisher is not None:
                publisher.publish(ErrorMsg(text=str(NativeAudioError())))

    def _request(self, command: dict[str, object], expected: str) -> Event:
        process = self._process
        if process is None:
            raise NativeAudioError()
        try:
            return process.request(command, expected)
        except NativeRecordingError:
            raise
        except Exception:
            process.close()
            self._process = None
            self._event(Event(type="error"), b"")
            raise NativeAudioError() from None

    def start_recording(self, path: Path) -> None:
        if self._process is None or self._capture_failed or self._recording_path is not None:
            raise NativeAudioError()
        self._recording_failed = False
        # Keep failed starts visible to finalization/cleanup; never claim success later.
        self._recording_path = path
        _ = self._request({"op": "start_recording", "path": str(path)}, "recording_started")

    def stop_recording(self) -> RecordingResult | None:
        path, self._recording_path = self._recording_path, None
        if path is None:
            return None
        if self._process is None or self._capture_failed:
            raise NativeAudioError()
        response = self._request({"op": "stop_recording"}, "recording_stopped")
        info = response.recording
        if self._recording_failed or info is None or info.ended_ms < info.started_ms:
            raise NativeAudioError()
        return RecordingResult(
            path,
            info.size_bytes,
            datetime.fromtimestamp(info.started_ms / 1000, UTC),
            datetime.fromtimestamp(info.ended_ms / 1000, UTC),
        )

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            try:
                _ = process.request({"op": "shutdown"}, "stopped")
                if process.process.wait(timeout=5) != 0:
                    raise NativeAudioError()
            finally:
                process.close()
                self._recording_path = None
                self._publisher = None
