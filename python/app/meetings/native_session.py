"""Temporary bounded transport to the Rust meeting lifecycle owner."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Literal, final

from pydantic import BaseModel, ConfigDict, Field

Effect = Literal[
    "prepare",
    "create_draft",
    "start_recording",
    "start_speech",
    "cancel_replies",
    "stop_speech",
    "cancel_final_replies",
    "finalize_recording",
    "remove_recording",
    "flush_history",
    "complete_draft",
    "abort_draft",
    "reload_audio",
]
Outcome = Literal["ok", "failed", "recording_saved", "recording_empty", "recording_disabled"]
Notice = Literal[
    "prepare_failed",
    "draft_failed",
    "recording_start_failed",
    "speech_failed",
    "stop_failed",
    "recording_removed",
    "recording_integrity_failed",
    "history_flush_failed",
    "save_failed",
    "reload_failed",
]


class SessionMetadata(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="forbid")
    id: str = Field(pattern=r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$")
    started_at: datetime
    ended_at: datetime | None


class Snapshot(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="forbid")
    generation: int = Field(ge=0)
    step: int = Field(ge=0)
    phase: Literal["idle", "starting", "active", "stopping", "faulted"]
    session: SessionMetadata | None
    effect: Effect | None
    notices: list[Notice]


class _Response(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="forbid")
    id: int | None
    type: Literal["ready", "result", "error"]
    protocol: int | None = None
    snapshot: Snapshot | None = None
    code: Literal["busy", "stale", "invalid_outcome"] | None = None


class SessionWorkerError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("会議管理との接続が失われました。会議履歴を確認し、アプリを再起動してください。")


@final
class SessionWorker:
    """No restart after connection loss: the last effect may already have run."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._sequence = 0
        self._failed = False

    async def _read(self) -> _Response:
        if self._process is None or self._process.stdout is None:
            raise SessionWorkerError()
        line = await self._process.stdout.readline()
        if not line or not line.endswith(b"\n"):
            raise SessionWorkerError()
        return _Response.model_validate_json(line)

    async def _open(self) -> None:
        path = os.environ.get("MEETING_SESSION_WORKER", "")
        if not path or not Path(path).is_absolute():
            raise SessionWorkerError()
        self._process = await asyncio.create_subprocess_exec(
            path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ},
            limit=16384,
        )
        response = await self._read()
        if response.type != "ready" or response.id is not None or response.protocol != 1:
            raise SessionWorkerError()

    async def request(self, command: dict[str, object]) -> Snapshot:
        if self._failed:
            raise SessionWorkerError()
        try:
            async with asyncio.timeout(5):
                if self._process is None:
                    await self._open()
                process = self._process
                if process is None or process.stdin is None or process.returncode is not None:
                    raise SessionWorkerError()
                self._sequence += 1
                payload = json.dumps({"id": self._sequence, "command": command}).encode() + b"\n"
                if len(payload) > 4096:
                    raise SessionWorkerError()
                process.stdin.write(payload)
                await process.stdin.drain()
                response = await self._read()
                if response.type != "result" or response.id != self._sequence or response.snapshot is None:
                    raise SessionWorkerError()
                return response.snapshot
        except BaseException as error:
            await self.close()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise SessionWorkerError() from None

    async def close(self) -> None:
        self._failed = True
        process, self._process = self._process, None
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            _ = await process.wait()
            if process.stdin is not None:
                process.stdin.close()
