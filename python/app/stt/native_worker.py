"""Bounded, request/reply transport to the Rust PCM worker (protocol 2)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import ClassVar, Literal, final

from pydantic import BaseModel, ConfigDict, Field


class Punctuation(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)
    status: Literal["applied", "failed"]
    text: str | None = None


class Recognition(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)
    status: Literal["recognized", "rejected", "not_requested"]
    text: str | None = None
    punctuation: Punctuation | None = None


class Segment(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)
    generation: int = Field(ge=0)
    start_sample: int = Field(ge=0)
    end_sample: int = Field(ge=0)
    recognition: Recognition


class Response(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)
    id: int | None
    type: Literal["ready", "prepared", "configured", "reset", "audio", "finished", "stopped", "error"]
    protocol: int | None = None
    transcription_available: bool | None = None
    segment: Segment | None = None
    speech: bool | None = None


class NativeWorkerError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Rust音声認識を継続できません。モデルと実行ファイルを確認して再準備してください。")


@final
class NativeWorker:
    """One caller owns requests; every blocked operation has a deadline."""

    def __init__(self, executable: Path, model: Path, punctuation: Path | None) -> None:
        self._arguments = [str(executable), "--reazon-model", str(model)]
        if punctuation is not None:
            self._arguments += ["--punctuation-model", str(punctuation)]
        self._process: asyncio.subprocess.Process | None = None
        self._sequence = 0

    async def open(self) -> None:
        # The worker only needs OS runtime variables, never provider credentials.
        environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._arguments,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=environment,
                limit=65536,
            )
            async with asyncio.timeout(10):
                response = await self._read()
            if (
                response.type != "ready"
                or response.id is not None
                or response.protocol != 2
                or response.transcription_available is not True
            ):
                raise NativeWorkerError()
        except (OSError, ValueError, TimeoutError):
            raise NativeWorkerError() from None

    async def _read(self) -> Response:
        if self._process is None or self._process.stdout is None:
            raise NativeWorkerError()
        line = await self._process.stdout.readline()
        if not line or not line.endswith(b"\n"):
            raise NativeWorkerError()
        return Response.model_validate_json(line)

    async def request(self, command: dict[str, object], expected: str, timeout: float = 30) -> Response:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise NativeWorkerError()
        self._sequence += 1
        payload = json.dumps({"id": self._sequence, "command": command}, separators=(",", ":")).encode() + b"\n"
        if len(payload) > 16384:
            raise NativeWorkerError()
        try:
            async with asyncio.timeout(timeout):
                process.stdin.write(payload)
                await process.stdin.drain()
                response = await self._read()
            if response.id != self._sequence or response.type != expected:
                raise NativeWorkerError()
            return response
        except (OSError, ValueError, TimeoutError):
            raise NativeWorkerError() from None

    def kill(self) -> None:
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass

    async def close(self) -> None:
        self.kill()
        if self._process is not None:
            _ = await self._process.wait()
            if self._process.stdin is not None:
                self._process.stdin.close()
