"""Synthetic SQLite interoperability and transaction checks against the real worker."""

import asyncio
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.meetings.history_models import MeetingRecord, MeetingTurnRecord, RecordingAsset, ReplySuggestionRecord
from app.meetings.native_repository import NativeMeetingHistoryRepository, StorageWorkerError
from app.meetings.sqlite_repository import SqliteMeetingHistoryRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("MEETING_STORAGE_WORKER"), reason="Build and select Rust storage worker"
)
NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def check_python_rust_roundtrip_and_cascade(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    python = SqliteMeetingHistoryRepository(path)
    await python.initialize()
    await python.create_meeting(MeetingRecord(id="meeting", started_at=NOW, title="Synthetic"))
    await python.close()
    rust = NativeMeetingHistoryRepository(path)
    await rust.initialize()
    try:
        meeting = await rust.get_meeting("meeting")
        assert meeting is not None and meeting.title == "Synthetic"
        await rust.insert_turn(
            MeetingTurnRecord(id="turn", meeting_id="meeting", sequence=1, speaker="self", text="Test")
        )
        await rust.insert_reply_suggestion(
            ReplySuggestionRecord(
                id="reply",
                meeting_id="meeting",
                target_turn_id="turn",
                sequence=1,
                agent_id="test",
                agent_label="Test",
                text="Synthetic reply",
            )
        )
        await rust.insert_recording_assets(
            [
                RecordingAsset(
                    id="asset",
                    meeting_id="meeting",
                    role="self",
                    relative_path="synthetic.wav",
                    started_at=NOW,
                    size_bytes=100,
                )
            ]
        )
        assert await rust.update_meeting_minutes("meeting", "Synthetic minutes") == 1
        assert await rust.update_meeting_title("missing", "Missing") == 0
        await rust.complete_meeting("meeting", NOW, 0, "Synthetic note")
        assert (await rust.list_meetings())[0].has_recording
        assert await rust.list_meetings(limit=1, offset=1) == []
        assert (await rust.list_completed_meeting_storage_oldest())[0].recording_size_bytes == 100
        assert (await rust.get_recording_asset_by_role("meeting", "self")) is not None
        assert len(await rust.list_reply_suggestions("meeting")) == 1
    finally:
        await rust.close()
    await python.initialize()
    try:
        meeting = await python.get_meeting("meeting")
        assert meeting is not None and meeting.minutes == "Synthetic minutes" and meeting.status == "completed"
        assert (await python.list_turns("meeting"))[0].text == "Test"
        assert len(await python.list_recording_assets("meeting")) == 1
    finally:
        await python.close()
    await rust.initialize()
    try:
        await rust.delete_meeting("meeting")
        assert await rust.count_meetings() == 0
        assert await rust.list_turns("meeting") == []
        assert await rust.list_reply_suggestions("meeting") == []
        assert await rust.list_recording_assets("meeting") == []
    finally:
        await rust.close()


async def check_constraint_rolls_back_batch_and_worker_recovers(tmp_path: Path) -> None:
    rust = NativeMeetingHistoryRepository(tmp_path / "history.sqlite3")
    await rust.initialize()
    try:
        await rust.create_meeting(MeetingRecord(id="meeting", started_at=NOW))
        with pytest.raises(sqlite3.IntegrityError):
            await rust.insert_recording_assets(
                [
                    RecordingAsset(
                        id=str(i), meeting_id="meeting", role="self", relative_path="test.wav", started_at=NOW
                    )
                    for i in range(2)
                ]
            )
        assert await rust.list_recording_assets("meeting") == []
        with pytest.raises(sqlite3.IntegrityError):
            await rust.insert_turn(
                MeetingTurnRecord(id="orphan", meeting_id="absent", sequence=0, speaker="self", text="Test")
            )
        await rust.abort_meeting("meeting", NOW)
        meeting = await rust.get_meeting("meeting")
        assert meeting is not None and meeting.status == "aborted"
        assert await rust.list_completed_meeting_storage_oldest() == []
    finally:
        await rust.close()


async def check_migration_and_corrupt_date(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    python = SqliteMeetingHistoryRepository(path)
    await python.initialize()
    await python.create_meeting(MeetingRecord(id="old", started_at=NOW))
    await python.close()
    with sqlite3.connect(path) as connection:
        _ = connection.execute("ALTER TABLE meetings DROP COLUMN minutes")
        _ = connection.execute("UPDATE schema_version SET version=1")
    rust = NativeMeetingHistoryRepository(path)
    await rust.initialize()
    meeting = await rust.get_meeting("old")
    assert meeting is not None and meeting.minutes == ""
    await rust.close()
    with sqlite3.connect(path) as connection:
        _ = connection.execute("UPDATE meetings SET started_at='invalid'")
    await rust.initialize()
    try:
        with pytest.raises(ValueError):
            _ = await rust.get_meeting("old")
        assert await rust.count_meetings() == 1
    finally:
        await rust.close()
    with sqlite3.connect(path) as connection:
        _ = connection.execute("UPDATE schema_version SET version=99")
    with pytest.raises(StorageWorkerError):
        await rust.initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (99,)


def test_python_rust_roundtrip_and_cascade(tmp_path: Path) -> None:
    asyncio.run(check_python_rust_roundtrip_and_cascade(tmp_path))


def test_constraint_rolls_back_batch_and_worker_recovers(tmp_path: Path) -> None:
    asyncio.run(check_constraint_rolls_back_batch_and_worker_recovers(tmp_path))


def test_migration_and_corrupt_date(tmp_path: Path) -> None:
    asyncio.run(check_migration_and_corrupt_date(tmp_path))


def test_existing_service_flush_and_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.meetings.models import MeetingSession, Turn
    from app.meetings.repository_factory import build_history_repository
    from app.meetings.service import MeetingHistoryService

    monkeypatch.setenv("MEETING_STORAGE_RUNTIME", "rust")

    async def run() -> None:
        repository = build_history_repository(tmp_path / "history.sqlite3")
        await repository.initialize()
        try:
            service = MeetingHistoryService(repository)
            session = MeetingSession(id="session", started_at=NOW)
            await service.create_draft_meeting(session)
            task = service.schedule_insert_turn(
                session.id, 0, Turn(id="final-turn", speaker="self", text="Synthetic final utterance")
            )
            await service.flush_pending()
            await task
            await service.complete_meeting(session.ended())
        finally:
            await repository.close()
        await repository.initialize()
        try:
            meetings, count = await service.list_meetings()
            assert count == 1 and meetings[0].status == "completed"
            assert (await repository.list_turns("session"))[0].text == "Synthetic final utterance"
        finally:
            await repository.close()

    asyncio.run(run())


def test_cancelled_write_stops_worker_without_retry(tmp_path: Path) -> None:
    async def run() -> None:
        path = tmp_path / "history.sqlite3"
        repository = NativeMeetingHistoryRepository(path)
        await repository.initialize()
        await repository.create_meeting(MeetingRecord(id="meeting", started_at=NOW))
        # Block the write using a separate synthetic connection.
        connection = sqlite3.connect(path)
        try:
            _ = connection.execute("BEGIN IMMEDIATE")
            task = asyncio.create_task(repository.update_meeting_title("meeting", "Cancelled"))
            await asyncio.sleep(0.05)
            assert not task.done()
            _ = task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(StorageWorkerError):
                _ = await repository.count_meetings()
        finally:
            connection.rollback()
            connection.close()
            await repository.close()
        await repository.initialize()
        try:
            meeting = await repository.get_meeting("meeting")
            assert meeting is not None and meeting.title is None
        finally:
            await repository.close()

    asyncio.run(run())
