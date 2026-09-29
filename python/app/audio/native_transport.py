"""Length-bounded binary PCM transport; no audio devices or inference in Python."""

from __future__ import annotations

import json
import os
import queue
import select
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, ClassVar, Literal, final

from pydantic import BaseModel, ConfigDict, Field


class Device(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)
    index: str
    name: str
    is_monitor: bool
    is_default: bool
    hostapi: str
    capture: Literal["rust"]


class RecordingInfo(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)
    size_bytes: int = Field(ge=44)
    started_ms: int = Field(ge=0)
    ended_ms: int = Field(ge=0)
    samples: int = Field(ge=0)


class Event(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)
    type: Literal[
        "devices", "ready", "audio", "recording_started", "recording_stopped", "recording_error", "stopped", "error"
    ]
    id: int | None = None
    protocol: int | None = None
    rate: int | None = None
    name: str | None = None
    sequence: int | None = Field(default=None, ge=0)
    peak: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    recording: RecordingInfo | None = None
    devices: list[Device] | None = None


class NativeAudioError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Rust音声取得・録音に失敗しました。デバイスと音声workerを確認してください。")


class NativeRecordingError(NativeAudioError):
    """A rejected recording command leaves capture alive."""


def worker_path() -> Path:
    value = os.environ.get("MEETING_AUDIO_WORKER")
    if sys.platform != "linux" or not value or not Path(value).is_absolute():
        raise NativeAudioError()
    return Path(value)


def enabled() -> bool:
    mode = os.environ.get("MEETING_AUDIO_RUNTIME", "python")
    if mode not in {"python", "rust"}:
        raise ValueError("MEETING_AUDIO_RUNTIME は python または rust を指定してください。")
    return mode == "rust"


def read_exact(stream: IO[bytes], length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        part = stream.read(length - len(result))
        if not part:
            raise NativeAudioError()
        result.extend(part)
    return bytes(result)


@final
class AudioProcess:
    """One reader always drains PCM; requests use a separate bounded reply queue."""

    def __init__(self, arguments: list[str], on_event: Callable[[Event, bytes], None]) -> None:
        environment = {
            key: os.environ[key]
            for key in ("HOME", "XDG_RUNTIME_DIR", "PULSE_SERVER", "PULSE_COOKIE", "TMPDIR")
            if key in os.environ
        }
        try:
            self.process = subprocess.Popen(
                [str(worker_path()), *arguments],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                env=environment,
            )
        except OSError:
            raise NativeAudioError() from None
        self._on_event = on_event
        self._replies: queue.Queue[Event] = queue.Queue(maxsize=16)
        self._sequence = 0
        self._closed = threading.Event()
        self._failed = threading.Event()
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, name="native-audio-reader", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        stream = self.process.stdout
        assert stream is not None
        try:
            while not self._closed.is_set():
                prefix = read_exact(stream, 8)
                header_size = int.from_bytes(prefix[:4], "little")
                pcm_size = int.from_bytes(prefix[4:], "little")
                if not 0 < header_size <= 16384 or pcm_size not in {0, 960}:
                    raise NativeAudioError()
                event = Event.model_validate_json(read_exact(stream, header_size))
                pcm = read_exact(stream, pcm_size)
                if (event.type == "audio") != (pcm_size == 960):
                    raise NativeAudioError()
                if event.type == "audio":
                    if event.sequence is None or event.peak is None:
                        raise NativeAudioError()
                    self._on_event(event, pcm)
                elif event.type == "recording_error":
                    self._on_event(event, pcm)
                else:
                    self._replies.put_nowait(event)
                    if event.type in {"stopped", "devices"}:
                        return
                    if event.type == "error" and event.id is None:
                        raise NativeAudioError()
        except Exception:
            self._failed.set()
            if not self._closed.is_set():
                self._on_event(Event(type="error"), b"")

    def receive(self, expected: str, request_id: int | None = None, timeout: float = 8) -> Event:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = self._replies.get(timeout=min(0.05, max(0, deadline - time.monotonic())))
                if event.type == "error" and event.id == request_id and request_id is not None:
                    raise NativeRecordingError()
                if event.type != expected or event.id != request_id:
                    raise NativeAudioError()
                return event
            except queue.Empty:
                if self._failed.is_set():
                    raise NativeAudioError() from None
        raise NativeAudioError()

    def request(self, command: dict[str, object], expected: str) -> Event:
        with self._lock:
            stream = self.process.stdin
            if stream is None or self.process.poll() is not None or self._failed.is_set():
                raise NativeAudioError()
            self._sequence += 1
            payload = json.dumps({"id": self._sequence, "command": command}, separators=(",", ":")).encode() + b"\n"
            if len(payload) > 16384:
                raise NativeAudioError()
            deadline = time.monotonic() + 5
            try:
                os.set_blocking(stream.fileno(), False)
                while payload:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([], [stream], [], remaining)[1]:
                        raise NativeAudioError()
                    try:
                        written = os.write(stream.fileno(), payload)
                    except BlockingIOError:
                        continue
                    payload = payload[written:]
                return self.receive(expected, self._sequence, timeout=max(0, deadline - time.monotonic()))
            except OSError:
                raise NativeAudioError() from None

    def close(self) -> None:
        self._closed.set()
        if self.process.poll() is None:
            self.process.kill()
        _ = self.process.wait(timeout=5)
        self._reader.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()


def list_devices() -> list[Device]:
    process = AudioProcess(["--list-devices"], lambda _event, _pcm: None)
    try:
        response = process.receive("devices")
        if response.devices is None or process.process.wait(timeout=5) != 0:
            raise NativeAudioError()
        return response.devices
    finally:
        process.close()
