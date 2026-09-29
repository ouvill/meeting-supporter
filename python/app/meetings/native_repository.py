"""Temporary API bridge to Rust/SQLx. Rust exclusively owns the SQLite connection."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Literal, final

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from app.meetings.history_models import (
    CompletedMeetingStorageRecord,
    MeetingListItemRecord,
    MeetingRecord,
    MeetingTurnRecord,
    RecordingAsset,
    ReplySuggestionRecord,
)


class StorageWorkerError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Rust履歴保存に失敗しました。保存状態を確認してから再起動してください。")


class _Response(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="forbid")
    id: int | None
    type: Literal["ready", "result", "error"]
    protocol: int | None = None
    schema_version: int | None = Field(default=None, alias="schema")
    code: Literal["unavailable", "constraint", "invalid_data", "unsupported_schema"] | None = None
    value: JsonValue = None


_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_NULL = TypeAdapter(type(None))
_COUNT = TypeAdapter(int)
_MEETING = TypeAdapter(MeetingRecord)
_TURN = TypeAdapter(MeetingTurnRecord)
_SUGGESTION = TypeAdapter(ReplySuggestionRecord)
_ASSETS = TypeAdapter(list[RecordingAsset])


def _record[T](adapter: TypeAdapter[T], value: T) -> JsonValue:
    return _JSON.validate_json(adapter.dump_json(value))


@final
class NativeMeetingHistoryRepository:
    """Serialized requests, no automatic retry of writes whose outcome is unknown."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._sequence = 0

    async def initialize(self) -> None:
        async with self._lock:
            if self._process is not None:
                raise StorageWorkerError()
            executable = os.environ.get("MEETING_STORAGE_WORKER")
            if not executable or not Path(executable).is_absolute():
                raise StorageWorkerError()
            try:
                self._process = await asyncio.create_subprocess_exec(
                    executable,
                    "--database",
                    self._path,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=32 * 1024 * 1024,
                    env={key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ},
                )
                async with asyncio.timeout(10):
                    response = await self._read()
                if (
                    response.type != "ready"
                    or response.id is not None
                    or response.protocol != 1
                    or response.schema_version != 2
                ):
                    raise StorageWorkerError()
            except BaseException as error:
                await self._terminate()
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise StorageWorkerError() from None

    async def _read(self) -> _Response:
        process = self._process
        if process is None or process.stdout is None:
            raise StorageWorkerError()
        line = await process.stdout.readline()
        if not line or not line.endswith(b"\n"):
            raise StorageWorkerError()
        return _Response.model_validate_json(line)

    async def _terminate(self) -> None:
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

    async def close(self) -> None:
        async with self._lock:
            process = self._process
            if process is None:
                return
            try:
                if process.stdin is not None:
                    process.stdin.close()
                async with asyncio.timeout(5):
                    if await process.wait() != 0:
                        raise StorageWorkerError()
            finally:
                await self._terminate()

    async def _call[T](self, command: dict[str, object], adapter: TypeAdapter[T]) -> T:
        async with self._lock:
            process = self._process
            if process is None or process.stdin is None or process.returncode is not None:
                raise StorageWorkerError()
            self._sequence += 1
            payload = json.dumps({"id": self._sequence, "command": command}, separators=(",", ":")).encode() + b"\n"
            if len(payload) > 8 * 1024 * 1024:
                raise ValueError("履歴保存の要求サイズが上限を超えています。")
            try:
                async with asyncio.timeout(10):
                    process.stdin.write(payload)
                    await process.stdin.drain()
                    response = await self._read()
                if response.id != self._sequence or response.type not in {"result", "error"}:
                    raise StorageWorkerError()
                if response.type == "result":
                    return adapter.validate_json(_JSON.dump_json(response.value), strict=True)
            except BaseException as error:
                await self._terminate()
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise StorageWorkerError() from None
            if response.code == "constraint":
                raise sqlite3.IntegrityError("履歴データの整合性制約に違反しました。")
            if response.code == "invalid_data":
                raise ValueError("履歴データの形式が不正です。")
            raise StorageWorkerError()

    async def create_meeting(self, record: MeetingRecord) -> None:
        await self._call({"op": "create_meeting", "record": _record(_MEETING, record)}, _NULL)

    async def complete_meeting(
        self, meeting_id: str, ended_at: datetime, duration_seconds: int | None = None, ai_note: str = ""
    ) -> None:
        await self._call(
            {
                "op": "complete_meeting",
                "meeting_id": meeting_id,
                "ended_at": ended_at.isoformat(),
                "duration_seconds": duration_seconds,
                "ai_note": ai_note,
            },
            _NULL,
        )

    async def abort_meeting(self, meeting_id: str, ended_at: datetime) -> None:
        await self._call({"op": "abort_meeting", "meeting_id": meeting_id, "ended_at": ended_at.isoformat()}, _NULL)

    async def get_meeting(self, meeting_id: str) -> MeetingRecord | None:
        return await self._call({"op": "get_meeting", "meeting_id": meeting_id}, TypeAdapter(MeetingRecord | None))

    async def list_meetings(self, *, limit: int = 50, offset: int = 0) -> list[MeetingListItemRecord]:
        return await self._call(
            {"op": "list_meetings", "limit": limit, "offset": offset}, TypeAdapter(list[MeetingListItemRecord])
        )

    async def count_meetings(self) -> int:
        return await self._call({"op": "count_meetings"}, _COUNT)

    async def update_meeting_title(self, meeting_id: str, title: str) -> int:
        return await self._call({"op": "update_meeting_title", "meeting_id": meeting_id, "title": title}, _COUNT)

    async def update_meeting_minutes(self, meeting_id: str, minutes: str) -> int:
        return await self._call({"op": "update_meeting_minutes", "meeting_id": meeting_id, "minutes": minutes}, _COUNT)

    async def delete_meeting(self, meeting_id: str) -> None:
        await self._call({"op": "delete_meeting", "meeting_id": meeting_id}, _NULL)

    async def list_completed_meeting_storage_oldest(self) -> list[CompletedMeetingStorageRecord]:
        return await self._call(
            {"op": "list_completed_meeting_storage_oldest"}, TypeAdapter(list[CompletedMeetingStorageRecord])
        )

    async def insert_turn(self, record: MeetingTurnRecord) -> None:
        await self._call({"op": "insert_turn", "record": _record(_TURN, record)}, _NULL)

    async def list_turns(self, meeting_id: str) -> list[MeetingTurnRecord]:
        return await self._call({"op": "list_turns", "meeting_id": meeting_id}, TypeAdapter(list[MeetingTurnRecord]))

    async def insert_reply_suggestion(self, record: ReplySuggestionRecord) -> None:
        await self._call({"op": "insert_reply_suggestion", "record": _record(_SUGGESTION, record)}, _NULL)

    async def list_reply_suggestions(self, meeting_id: str) -> list[ReplySuggestionRecord]:
        return await self._call(
            {"op": "list_reply_suggestions", "meeting_id": meeting_id}, TypeAdapter(list[ReplySuggestionRecord])
        )

    async def insert_recording_assets(self, records: list[RecordingAsset]) -> None:
        await self._call({"op": "insert_recording_assets", "records": _record(_ASSETS, records)}, _NULL)

    async def list_recording_assets(self, meeting_id: str) -> list[RecordingAsset]:
        return await self._call({"op": "list_recording_assets", "meeting_id": meeting_id}, _ASSETS)

    async def get_recording_asset_by_role(self, meeting_id: str, role: str) -> RecordingAsset | None:
        if role not in {"self", "other"}:
            return None
        return await self._call(
            {"op": "get_recording_asset_by_role", "meeting_id": meeting_id, "role": role},
            TypeAdapter(RecordingAsset | None),
        )
