"""Behavior tests for explicit recording retention and deletion safety."""

import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import override

from app.meetings.history_models import (
    CompletedMeetingStorageRecord,
    MeetingListItemRecord,
    MeetingRecord,
    MeetingStatus,
    MeetingTurnRecord,
    RecordingAsset,
    ReplySuggestionRecord,
)
from app.meetings.recording_retention import RecordingCleanupError, RecordingRetentionService
from app.meetings.service import MeetingHistoryService
from app.meetings.sqlite_repository import SqliteMeetingHistoryRepository


class RecordingRetentionRepositoryTest(unittest.IsolatedAsyncioTestCase):
    """Exercise retention against the real persistence projection and filesystem boundary."""

    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self._temporary_directory: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.user_data_dir: Path = Path(self._temporary_directory.name)
        self.repo: SqliteMeetingHistoryRepository = SqliteMeetingHistoryRepository(":memory:")

    @override
    async def asyncSetUp(self) -> None:
        await self.repo.initialize()

    @override
    async def asyncTearDown(self) -> None:
        await self.repo.close()
        self._temporary_directory.cleanup()

    async def _save_meeting_with_asset(
        self,
        *,
        meeting_id: str,
        status: MeetingStatus,
        ended_at: datetime | None,
        recording_size: int,
    ) -> None:
        await self.repo.create_meeting(
            MeetingRecord(
                id=meeting_id,
                started_at=datetime(2025, 1, 1, tzinfo=UTC),
                status=status,
                ended_at=ended_at,
            )
        )
        await self.repo.insert_recording_assets(
            [
                RecordingAsset(
                    id=f"asset-{meeting_id}",
                    meeting_id=meeting_id,
                    role="other",
                    relative_path=f"recordings/{meeting_id}/other.wav",
                    started_at=datetime(2025, 1, 1, tzinfo=UTC),
                    size_bytes=recording_size,
                )
            ]
        )

    async def test_storage_projection_sums_assets_and_excludes_non_completed_meetings(self) -> None:
        """Retention's repository projection returns completed recordings in deletion order with summed bytes."""
        await self._save_meeting_with_asset(
            meeting_id="oldest",
            status="completed",
            ended_at=datetime(2025, 1, 1, tzinfo=UTC),
            recording_size=15,
        )
        await self.repo.insert_recording_assets(
            [
                RecordingAsset(
                    id="asset-oldest-self",
                    meeting_id="oldest",
                    role="self",
                    relative_path="recordings/oldest/self.wav",
                    started_at=datetime(2025, 1, 1, tzinfo=UTC),
                    size_bytes=20,
                )
            ]
        )
        await self._save_meeting_with_asset(
            meeting_id="newest",
            status="completed",
            ended_at=datetime(2025, 1, 2, tzinfo=UTC),
            recording_size=10,
        )
        await self._save_meeting_with_asset(
            meeting_id="active",
            status="active",
            ended_at=None,
            recording_size=90,
        )
        await self._save_meeting_with_asset(
            meeting_id="aborted",
            status="aborted",
            ended_at=datetime(2025, 1, 1, tzinfo=UTC),
            recording_size=80,
        )

        storage = await self.repo.list_completed_meeting_storage_oldest()

        self.assertEqual(["oldest", "newest"], [record.meeting.id for record in storage])
        self.assertEqual([35, 10], [record.recording_size_bytes for record in storage])

    async def test_manual_delete_rejects_a_meeting_directory_outside_the_recordings_root(self) -> None:
        """Canonical containment prevents a malformed meeting id from deleting a sibling directory."""
        sibling_directory = self.user_data_dir.parent / f"{self.user_data_dir.name}_outside"
        sibling_directory.mkdir()
        sentinel = sibling_directory / "must-survive.wav"
        _ = sentinel.write_bytes(b"recording")
        meeting_id = f"../{sibling_directory.name}"
        await self.repo.create_meeting(
            MeetingRecord(
                id=meeting_id,
                started_at=datetime(2025, 1, 1, tzinfo=UTC),
                status="completed",
                ended_at=datetime(2025, 1, 1, tzinfo=UTC),
            )
        )
        retention = RecordingRetentionService(
            history=MeetingHistoryService(repository=self.repo),
            user_data_dir=self.user_data_dir,
        )
        try:
            with self.assertRaises(RecordingCleanupError):
                _ = await retention.delete_meeting_with_recordings(meeting_id)

            self.assertTrue(sentinel.is_file())
            self.assertIsNotNone(await self.repo.get_meeting(meeting_id))
        finally:
            shutil.rmtree(sibling_directory)


class _ActiveOnRecheckRepository:
    """A deterministic persistence boundary whose selected meeting becomes active before deletion."""

    def __init__(self) -> None:
        self._selected: MeetingRecord = MeetingRecord(
            id="changes-state",
            started_at=datetime(2025, 1, 1, tzinfo=UTC),
            status="completed",
            ended_at=datetime(2025, 1, 1, tzinfo=UTC),
        )

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_meeting(self, record: MeetingRecord) -> None:
        _ = record

    async def complete_meeting(
        self,
        meeting_id: str,
        ended_at: datetime,
        duration_seconds: int | None = None,
        ai_note: str = "",
    ) -> None:
        _ = (meeting_id, ended_at, duration_seconds, ai_note)

    async def abort_meeting(self, meeting_id: str, ended_at: datetime) -> None:
        _ = (meeting_id, ended_at)

    async def get_meeting(self, meeting_id: str) -> MeetingRecord | None:
        if meeting_id != self._selected.id:
            return None
        return MeetingRecord(
            id=meeting_id,
            started_at=self._selected.started_at,
            status="active",
        )

    async def list_meetings(self, *, limit: int = 50, offset: int = 0) -> list[MeetingListItemRecord]:
        _ = (limit, offset)
        return []

    async def count_meetings(self) -> int:
        return 0

    async def update_meeting_title(self, meeting_id: str, title: str) -> int:
        _ = (meeting_id, title)
        return 0

    async def update_meeting_minutes(self, meeting_id: str, minutes: str) -> int:
        _ = (meeting_id, minutes)
        return 0

    async def delete_meeting(self, meeting_id: str) -> None:
        raise AssertionError(f"active meeting {meeting_id} must not be deleted")

    async def list_completed_meeting_storage_oldest(self) -> list[CompletedMeetingStorageRecord]:
        return [CompletedMeetingStorageRecord(meeting=self._selected, recording_size_bytes=64)]

    async def insert_turn(self, record: MeetingTurnRecord) -> None:
        _ = record

    async def list_turns(self, meeting_id: str) -> list[MeetingTurnRecord]:
        _ = meeting_id
        return []

    async def insert_reply_suggestion(self, record: ReplySuggestionRecord) -> None:
        _ = record

    async def list_reply_suggestions(self, meeting_id: str) -> list[ReplySuggestionRecord]:
        _ = meeting_id
        return []

    async def insert_recording_assets(self, records: list[RecordingAsset]) -> None:
        _ = records

    async def list_recording_assets(self, meeting_id: str) -> list[RecordingAsset]:
        _ = meeting_id
        return []

    async def get_recording_asset_by_role(self, meeting_id: str, role: str) -> RecordingAsset | None:
        _ = (meeting_id, role)
        return None


class RecordingRetentionRecheckTest(unittest.IsolatedAsyncioTestCase):
    async def test_execute_skips_candidate_that_became_active_after_preview(self) -> None:
        """Execution rechecks status so a stale preview cannot delete a newly active meeting."""
        repository = _ActiveOnRecheckRepository()
        retention = RecordingRetentionService(
            history=MeetingHistoryService(repository=repository),
            user_data_dir=Path(tempfile.gettempdir()),
        )

        result = await retention.execute(cutoff_date=None, max_total_bytes=32)

        self.assertEqual(("changes-state",), result.skipped_meeting_ids)
        self.assertEqual((), result.deleted_meeting_ids)
        self.assertEqual((), result.failed_meeting_ids)
