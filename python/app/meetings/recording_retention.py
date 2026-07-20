"""Explicit, file-first cleanup for completed meeting recordings.

Retention policy is deliberately inert until a caller requests a preview or
execution.  The service only selects ``completed`` rows and removes a meeting's
recording directory before its database row, so an I/O failure leaves metadata
and files together for a later retry.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

from app.meetings.history_models import CompletedMeetingStorageRecord
from app.meetings.service import MeetingHistoryService


class RecordingCleanupError(RuntimeError):
    """A recording directory could not be safely removed."""


@dataclass(frozen=True)
class RecordingCleanupPlan:
    """The completed meetings an explicit cleanup request would remove."""

    candidates: tuple[CompletedMeetingStorageRecord, ...]
    total_recording_bytes_before: int
    total_recording_bytes_after: int

    @property
    def delete_count(self) -> int:
        return len(self.candidates)

    @property
    def delete_recording_bytes(self) -> int:
        return sum(candidate.recording_size_bytes for candidate in self.candidates)


@dataclass(frozen=True)
class RecordingCleanupResult:
    """Outcome of an explicit cleanup execution."""

    plan: RecordingCleanupPlan
    deleted_meeting_ids: tuple[str, ...]
    failed_meeting_ids: tuple[str, ...]
    skipped_meeting_ids: tuple[str, ...]


def _recording_directory(user_data_dir: Path, meeting_id: str) -> Path:
    """Return a verified per-meeting recording directory without traversal."""
    recordings_root = user_data_dir / "recordings"
    directory = recordings_root / meeting_id
    try:
        user_root = user_data_dir.resolve()
        root_resolved = recordings_root.resolve()
        resolved = directory.resolve()
        _ = root_resolved.relative_to(user_root)
        _ = resolved.relative_to(root_resolved)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RecordingCleanupError("Invalid recording directory") from exc
    if directory.is_symlink():
        raise RecordingCleanupError("Refusing to delete a symlinked recording directory")
    return directory


def _remove_recording_directory(user_data_dir: Path, meeting_id: str) -> None:
    """Synchronously remove a verified recording directory, if it exists."""
    directory = _recording_directory(user_data_dir, meeting_id)
    if not directory.exists():
        return
    if not directory.is_dir():
        raise RecordingCleanupError("Recording path is not a directory")
    try:
        shutil.rmtree(directory)
    except OSError as exc:
        raise RecordingCleanupError("Failed to delete recording directory") from exc


def _utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class RecordingRetentionService:
    """Plans and executes user-requested recording cleanup.

    ``cutoff_date`` and ``max_total_bytes`` are independent.  A cutoff selects
    completed meetings ended before its UTC midnight.  A capacity budget then
    removes additional completed meetings with recordings in oldest-first
    order until the completed-recording total fits the budget.
    """

    def __init__(self, *, history: MeetingHistoryService, user_data_dir: Path) -> None:
        self._history: MeetingHistoryService = history
        self._user_data_dir: Path = user_data_dir

    async def preview(
        self,
        *,
        cutoff_date: date | None,
        max_total_bytes: int | None,
    ) -> RecordingCleanupPlan:
        if cutoff_date is None and max_total_bytes is None:
            raise ValueError("Specify a cutoff date or a maximum total size")
        if max_total_bytes is not None and max_total_bytes <= 0:
            raise ValueError("Maximum total size must be greater than zero")

        storage = await self._history.list_completed_meeting_storage_oldest()
        total_before = sum(record.recording_size_bytes for record in storage)
        selected: list[CompletedMeetingStorageRecord] = []
        selected_ids: set[str] = set()

        if cutoff_date is not None:
            cutoff_at = datetime.combine(cutoff_date, time.min, tzinfo=UTC)
            for record in storage:
                ended_at = record.meeting.ended_at
                if ended_at is not None and _utc_datetime(ended_at) < cutoff_at:
                    selected.append(record)
                    selected_ids.add(record.meeting.id)

        remaining_bytes = total_before - sum(record.recording_size_bytes for record in selected)
        if max_total_bytes is not None:
            for record in storage:
                if remaining_bytes <= max_total_bytes:
                    break
                if record.meeting.id in selected_ids or record.recording_size_bytes <= 0:
                    continue
                selected.append(record)
                selected_ids.add(record.meeting.id)
                remaining_bytes -= record.recording_size_bytes

        return RecordingCleanupPlan(
            candidates=tuple(selected),
            total_recording_bytes_before=total_before,
            total_recording_bytes_after=total_before - sum(record.recording_size_bytes for record in selected),
        )

    async def execute(
        self,
        *,
        cutoff_date: date | None,
        max_total_bytes: int | None,
    ) -> RecordingCleanupResult:
        plan = await self.preview(cutoff_date=cutoff_date, max_total_bytes=max_total_bytes)
        deleted: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []
        for candidate in plan.candidates:
            meeting = await self._history.repository.get_meeting(candidate.meeting.id)
            if meeting is None or meeting.status != "completed":
                skipped.append(candidate.meeting.id)
                continue
            try:
                await asyncio.to_thread(_remove_recording_directory, self._user_data_dir, candidate.meeting.id)
            except RecordingCleanupError:
                failed.append(candidate.meeting.id)
                continue
            if await self._history.delete_meeting(candidate.meeting.id):
                deleted.append(candidate.meeting.id)
            else:
                # A concurrent DB deletion is idempotent from the cleanup
                # caller's perspective; it never causes a second file removal.
                skipped.append(candidate.meeting.id)
        return RecordingCleanupResult(
            plan=plan,
            deleted_meeting_ids=tuple(deleted),
            failed_meeting_ids=tuple(failed),
            skipped_meeting_ids=tuple(skipped),
        )

    async def delete_meeting_with_recordings(self, meeting_id: str) -> bool:
        """Manually delete one meeting, preserving files if deletion fails."""
        meeting = await self._history.repository.get_meeting(meeting_id)
        if meeting is None:
            return False
        await asyncio.to_thread(_remove_recording_directory, self._user_data_dir, meeting_id)
        return await self._history.delete_meeting(meeting_id)


__all__ = [
    "RecordingCleanupError",
    "RecordingCleanupPlan",
    "RecordingCleanupResult",
    "RecordingRetentionService",
    "_remove_recording_directory",
]
