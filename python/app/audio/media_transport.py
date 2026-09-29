"""Control/results only: PCM stays entirely between Rust processes."""

from __future__ import annotations

import json
import os
import queue
import select
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Literal, final

from pydantic import BaseModel, ConfigDict, Field

from app.audio.native_transport import RecordingInfo


class MediaError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Rust音声制御に失敗しました。音声設定を確認し、再準備してください。")


class MediaCommandError(MediaError):
    """A rejected command does not imply capture/recording has stopped."""


class MediaEvent(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="forbid")
    type: Literal["ready", "level", "transcript", "speech_error", "capture_error", "recording_error", "result", "error"]
    id: int | None = None
    protocol: int | None = None
    rate: int | None = None
    name: str | None = None
    peak: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    generation: int | None = Field(default=None, ge=0)
    start_sample: int | None = Field(default=None, ge=0)
    end_sample: int | None = Field(default=None, ge=0)
    text: str | None = None
    raw_text: str | None = None
    punctuation_failed: bool | None = None
    recording: RecordingInfo | None = None
    code: Literal["protocol", "worker", "timeout", "capture", "discontinuity", "speech", "recording", "busy"] | None = (
        None
    )


def enabled() -> bool:
    runtime = os.environ.get("MEETING_MEDIA_RUNTIME", "python")
    if runtime not in {"python", "rust"}:
        raise ValueError("MEETING_MEDIA_RUNTIME must be python or rust")
    return runtime == "rust"


def _executable(key: str) -> str:
    value = os.environ.get(key, "")
    if not value or not Path(value).is_absolute():
        raise MediaError()
    return value


@final
class MediaProcess:
    def __init__(self, role: str, device: int | str | None, on_event: Callable[[MediaEvent], None]) -> None:
        arguments = [
            _executable("MEETING_MEDIA_WORKER"),
            "--audio-worker",
            _executable("MEETING_AUDIO_WORKER"),
            "--speech-worker",
            _executable("MEETING_REAZON_WORKER"),
            "--role",
            role,
        ]
        if device is not None:
            arguments += ["--device", str(device)]
        environment = {
            key: os.environ[key]
            for key in (
                "HOME",
                "XDG_RUNTIME_DIR",
                "PULSE_SERVER",
                "PULSE_COOKIE",
                "TMPDIR",
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
            )
            if key in os.environ
        }
        try:
            self.process = subprocess.Popen(
                arguments,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=environment,
            )
        except OSError:
            raise MediaError() from None
        self._callback = on_event
        self._replies: queue.Queue[MediaEvent] = queue.Queue(maxsize=1)
        self._pending: dict[int, queue.Queue[MediaEvent]] = {}
        self._pending_lock = threading.Lock()
        self._failed = threading.Event()
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._sequence = 0
        self._reader = threading.Thread(target=self._read, name="rust-media-events", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        output = self.process.stdout
        assert output is not None
        try:
            while not self._closed.is_set():
                line = output.readline(65537)
                if len(line) > 65536 or not line.endswith(b"\n"):
                    raise MediaError()
                event = MediaEvent.model_validate_json(line)
                if event.type == "ready":
                    self._replies.put_nowait(event)
                elif event.type in {"result", "error"}:
                    with self._pending_lock:
                        reply = self._pending.get(event.id) if event.id is not None else None
                    if reply is None:
                        raise MediaError()
                    reply.put_nowait(event)
                else:
                    self._callback(event)
        except Exception:
            self._failed.set()
            if not self._closed.is_set():
                self._callback(MediaEvent(type="capture_error"))

    def receive(self, request_id: int | None, timeout: float) -> MediaEvent:
        with self._pending_lock:
            replies = self._replies if request_id is None else self._pending.get(request_id)
        if replies is None:
            raise MediaError()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = replies.get(timeout=min(0.05, max(0, deadline - time.monotonic())))
            except queue.Empty:
                if self._failed.is_set():
                    raise MediaError()
                continue
            if event.id != request_id:
                raise MediaError()
            if event.type == "error":
                raise MediaCommandError()
            if event.type != ("ready" if request_id is None else "result"):
                raise MediaError()
            return event
        raise MediaError()

    def request(self, command: dict[str, object], timeout: float = 10) -> MediaEvent:
        # Lock only the write, so cancellation can interrupt model preparation.
        with self._lock:
            stream = self.process.stdin
            if stream is None or self.process.poll() is not None or self._failed.is_set():
                raise MediaError()
            self._sequence += 1
            request_id = self._sequence
            payload = json.dumps({"id": request_id, "command": command}).encode() + b"\n"
            if len(payload) > 16384:
                raise MediaError()
            with self._pending_lock:
                if len(self._pending) >= 16:
                    raise MediaError()
                self._pending[request_id] = queue.Queue(maxsize=1)
            deadline = time.monotonic() + min(timeout, 5)
            try:
                os.set_blocking(stream.fileno(), False)
                while payload:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([], [stream], [], remaining)[1]:
                        raise MediaError()
                    try:
                        count = os.write(stream.fileno(), payload)
                    except BlockingIOError:
                        continue
                    payload = payload[count:]
            except (OSError, MediaError):
                self._failed.set()
                with self._pending_lock:
                    _ = self._pending.pop(request_id, None)
                raise MediaError() from None
        try:
            return self.receive(request_id, timeout)
        finally:
            with self._pending_lock:
                _ = self._pending.pop(request_id, None)

    def healthy(self) -> bool:
        return self.process.poll() is None and not self._failed.is_set()

    def close(self) -> None:
        with self._close_lock:
            self._closed.set()
            self._failed.set()
            if self.process.poll() is None:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
            _ = self.process.wait(timeout=5)
            self._reader.join(timeout=5)
            for stream in (self.process.stdin, self.process.stdout):
                if stream is not None:
                    stream.close()
