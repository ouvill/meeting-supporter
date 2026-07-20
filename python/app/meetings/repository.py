"""MeetingHistoryRepository protocol — a pure interface for meeting persistence.

All methods are async so that callers can await persistence without blocking
the event loop.  Implementations handle their own concurrency strategy.
"""

from datetime import datetime
from typing import Protocol

from app.meetings.history_models import (
    CompletedMeetingStorageRecord,
    MeetingListItemRecord,
    MeetingRecord,
    MeetingTurnRecord,
    RecordingAsset,
    ReplySuggestionRecord,
)


class MeetingHistoryRepository(Protocol):
    """Persistence interface for meeting history records.

    Each method that modifies state may raise standard exceptions (e.g.
    sqlite3.IntegrityError) when constraints are violated.  Callers should
    handle those at an appropriate boundary.
    """

    async def initialize(self) -> None:
        """Open the backing store and create schema if needed."""
        ...

    async def close(self) -> None:
        """Close the backing store gracefully."""
        ...

    # ── Meetings ──────────────────────────────────────────────────────────────

    async def create_meeting(self, record: MeetingRecord) -> None:
        """Insert a new meeting record."""
        ...

    async def complete_meeting(
        self,
        meeting_id: str,
        ended_at: datetime,
        duration_seconds: int | None = None,
        ai_note: str = "",
    ) -> None:
        """Mark an active meeting as completed."""
        ...

    async def abort_meeting(
        self,
        meeting_id: str,
        ended_at: datetime,
    ) -> None:
        """Mark an active meeting as aborted."""
        ...

    async def get_meeting(self, meeting_id: str) -> MeetingRecord | None:
        """Retrieve a single meeting by id, or None."""
        ...

    async def list_meetings(self, *, limit: int = 50, offset: int = 0) -> list[MeetingListItemRecord]:
        """List meetings ordered by started_at/id descending.

        Returns lightweight records that include the ``has_recording`` flag
        computed from the recording_assets table.
        """
        ...

    async def count_meetings(self) -> int:
        """Return the total number of meeting rows."""
        ...

    async def update_meeting_title(self, meeting_id: str, title: str) -> int:
        """Update the title of a meeting.  Returns number of rows changed (0 or 1)."""
        ...

    async def update_meeting_minutes(self, meeting_id: str, minutes: str) -> int:
        """Replace the meeting's canonical completed minutes. Returns rows changed."""
        ...

    async def delete_meeting(self, meeting_id: str) -> None:
        """Delete a meeting row (cascade removes turns / suggestions / assets)."""
        ...

    async def list_completed_meeting_storage_oldest(self) -> list[CompletedMeetingStorageRecord]:
        """List completed meetings and recording sizes ordered oldest first."""
        ...

    # ── Turns ─────────────────────────────────────────────────────────────────

    async def insert_turn(self, record: MeetingTurnRecord) -> None:
        """Insert a turn record."""
        ...

    async def list_turns(self, meeting_id: str) -> list[MeetingTurnRecord]:
        """List all turns for a meeting, ordered by sequence."""
        ...

    # ── Reply suggestions ─────────────────────────────────────────────────────

    async def insert_reply_suggestion(self, record: ReplySuggestionRecord) -> None:
        """Insert a reply-suggestion record."""
        ...

    async def list_reply_suggestions(self, meeting_id: str) -> list[ReplySuggestionRecord]:
        """List all reply suggestions for a meeting, ordered by sequence."""
        ...

    # ── Recording assets (Phase 1: schema only) ───────────────────────────────

    async def insert_recording_assets(self, records: list[RecordingAsset]) -> None:
        """Atomically insert all recording asset records for one finalisation."""
        ...

    async def list_recording_assets(self, meeting_id: str) -> list[RecordingAsset]:
        """List recording assets for a meeting."""
        ...

    async def get_recording_asset_by_role(self, meeting_id: str, role: str) -> RecordingAsset | None:
        """Get a single recording asset by meeting_id and role."""
        ...


__all__ = ["MeetingHistoryRepository"]
