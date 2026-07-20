"""MeetingHistoryService — maps runtime meeting models to persistence records.

Business rules live here; the repository remains a pure persistence detail.
Lifecycle persistence methods (draft/complete/abort) may raise repository
errors so the caller can compensate.  Real-time methods (turn, suggestion)
catch and log errors in background tasks to avoid disrupting the STT/reply
flow.
"""

import asyncio
import logging
from datetime import UTC, datetime

from app.meetings.history_models import (
    CompletedMeetingStorageRecord,
    MeetingListItemRecord,
    MeetingRecord,
    MeetingTurnRecord,
    RecordingAsset,
    ReplySuggestionRecord,
)
from app.meetings.models import MeetingSession, ReplySuggestion, Turn
from app.meetings.repository import MeetingHistoryRepository

logger = logging.getLogger(__name__)


def _now_timestamp() -> datetime:
    return datetime.now(UTC)


def _compute_duration_seconds(start: datetime, end: datetime) -> int:
    diff = end - start
    return int(diff.total_seconds())


class MeetingHistoryService:
    """High-level service that translates runtime domain objects into records
    and delegates persistence to a ``MeetingHistoryRepository``."""

    def __init__(self, repository: MeetingHistoryRepository) -> None:
        self._repository: MeetingHistoryRepository = repository
        self._pending_tasks: set[asyncio.Task[None]] = set()

    @property
    def repository(self) -> MeetingHistoryRepository:
        return self._repository

    def _track_persistence_task(self, task: asyncio.Task[None]) -> asyncio.Task[None]:
        self._pending_tasks.add(task)

        def _on_done(done_task: asyncio.Task[None]) -> None:
            self._pending_tasks.discard(done_task)
            if done_task.cancelled():
                logger.warning("Persistence task was cancelled")
                return
            exc = done_task.exception()
            if exc is not None:
                logger.error(
                    "Persistence task failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(_on_done)
        return task

    async def flush_pending(self, timeout: float = 5.0) -> None:
        """Wait for currently pending and newly scheduled persistence tasks."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self._pending_tasks:
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning(
                    "Timed out waiting for %d persistence task(s)",
                    len(self._pending_tasks),
                )
                return
            pending_snapshot = set(self._pending_tasks)
            _done, pending = await asyncio.wait(pending_snapshot, timeout=remaining)
            if pending and loop.time() >= deadline:
                logger.warning(
                    "Timed out waiting for %d persistence task(s)",
                    len(pending),
                )
                return

    # ── Meeting lifecycle (may raise) ─────────────────────────────────────────

    async def create_draft_meeting(self, session: MeetingSession) -> None:
        """Persist a draft meeting record from a newly started session.

        Raises on failure so the coordinator can compensate.
        """
        now = _now_timestamp()
        record = MeetingRecord(
            id=session.id,
            started_at=session.started_at,
            status="active",
            title=session.title,
            created_at=now,
            updated_at=now,
        )
        await self._repository.create_meeting(record)

    async def complete_meeting(self, session: MeetingSession) -> None:
        """Persist meeting completion details.

        Raises on failure so the coordinator can compensate.
        """
        ended_at = session.ended_at or _now_timestamp()
        duration = _compute_duration_seconds(session.started_at, ended_at)
        await self._repository.complete_meeting(
            meeting_id=session.id,
            ended_at=ended_at,
            duration_seconds=duration,
            ai_note=session.ai_note,
        )

    async def abort_meeting(self, meeting_id: str) -> None:
        """Mark a meeting as aborted.

        Raises on failure so the coordinator can compensate.
        """
        await self._repository.abort_meeting(
            meeting_id=meeting_id,
            ended_at=_now_timestamp(),
        )

    # ── Turns (fire-and-forget — errors are logged) ───────────────────────────

    async def insert_turn(self, meeting_id: str, sequence: int, turn: Turn) -> None:
        """Persist a single turn.

        Errors are logged but not raised so the real-time STT flow is not
        disrupted.
        """
        record = MeetingTurnRecord(
            id=turn.id,
            meeting_id=meeting_id,
            sequence=sequence,
            speaker=turn.speaker,
            text=turn.text,
            speaker_id=turn.speaker_id,
            created_at=_now_timestamp(),
        )
        try:
            await self._repository.insert_turn(record)
        except Exception:
            logger.exception("Failed to persist turn %s for meeting %s", turn.id, meeting_id)

    def schedule_insert_turn(self, meeting_id: str, sequence: int, turn: Turn) -> asyncio.Task[None]:
        """Schedule turn persistence as a tracked background task."""
        return self._track_persistence_task(asyncio.create_task(self.insert_turn(meeting_id, sequence, turn)))

    # ── Reply suggestions (fire-and-forget — errors are logged) ───────────────

    async def save_reply_suggestion(
        self,
        meeting_id: str,
        sequence: int,
        suggestion: ReplySuggestion,
    ) -> None:
        """Persist a single reply suggestion.

        Errors are logged but not raised so the reply generation flow is not
        disrupted.
        """
        record = ReplySuggestionRecord(
            id=suggestion.id,
            meeting_id=meeting_id,
            target_turn_id=suggestion.target_turn_id,
            sequence=sequence,
            agent_id=suggestion.agent_id,
            agent_label=suggestion.agent_label,
            text=suggestion.text,
            created_at=_now_timestamp(),
        )
        try:
            await self._repository.insert_reply_suggestion(record)
        except Exception:
            logger.exception(
                "Failed to persist reply suggestion %s for meeting %s",
                suggestion.id,
                meeting_id,
            )

    def schedule_save_reply_suggestion(
        self, meeting_id: str, sequence: int, suggestion: ReplySuggestion
    ) -> asyncio.Task[None]:
        """Schedule reply suggestion persistence as a tracked background task."""
        return self._track_persistence_task(
            asyncio.create_task(self.save_reply_suggestion(meeting_id, sequence, suggestion))
        )

    # ── Query / mutation (delegates to repository) ─────────────────────────────

    async def list_meetings(self, *, limit: int = 50, offset: int = 0) -> tuple[list[MeetingListItemRecord], int]:
        """Return a page of meetings and the total row count.

        Records are ordered by ``started_at DESC, id DESC`` so offset pages are
        deterministic even when multiple meetings share the same timestamp.
        """
        records = await self._repository.list_meetings(limit=limit, offset=offset)
        total = await self._repository.count_meetings()
        return records, total

    async def list_completed_meeting_storage_oldest(self) -> list[CompletedMeetingStorageRecord]:
        """Return only completed meetings in retention deletion order."""
        return await self._repository.list_completed_meeting_storage_oldest()

    async def get_meeting_detail(
        self, meeting_id: str
    ) -> tuple[MeetingRecord | None, list[MeetingTurnRecord], list[ReplySuggestionRecord], list[RecordingAsset]]:
        """Return a meeting with its turns, reply suggestions, and recording assets."""
        meeting = await self._repository.get_meeting(meeting_id)
        if meeting is None:
            return (None, [], [], [])
        turns = await self._repository.list_turns(meeting_id)
        suggestions = await self._repository.list_reply_suggestions(meeting_id)
        assets = await self._repository.list_recording_assets(meeting_id)
        return (meeting, turns, suggestions, assets)

    async def get_minutes_snapshot(self, meeting_id: str) -> MeetingSession | None:
        """Return an immutable post-completion snapshot from persisted meeting data."""
        meeting = await self._repository.get_meeting(meeting_id)
        if meeting is None or meeting.status != "completed":
            return None
        turns = await self._repository.list_turns(meeting_id)
        return MeetingSession(
            id=meeting.id,
            started_at=meeting.started_at,
            title=meeting.title,
            ended_at=meeting.ended_at,
            turns=tuple(
                Turn(id=turn.id, speaker=turn.speaker, text=turn.text, speaker_id=turn.speaker_id) for turn in turns
            ),
            ai_note=meeting.ai_note,
            is_active=False,
        )

    async def save_minutes(self, meeting_id: str, minutes: str) -> bool:
        """Persist one fully completed canonical minutes result."""
        return await self._repository.update_meeting_minutes(meeting_id, minutes) > 0

    async def update_meeting_title(self, meeting_id: str, title: str) -> bool:
        """Update a meeting's title.  Returns True if a row was updated."""
        rowcount = await self._repository.update_meeting_title(meeting_id, title)
        return rowcount > 0

    async def persist_recording_assets(self, assets: list[RecordingAsset]) -> None:
        """Atomically persist finalised recording metadata.

        This is a closing-boundary operation: errors intentionally propagate so
        a meeting cannot be silently marked complete while its audio files have
        no corresponding durable asset rows.
        """
        await self._repository.insert_recording_assets(assets)

    async def delete_meeting(self, meeting_id: str) -> bool:
        """Delete a meeting (DB only; caller handles file cleanup).

        Returns True if a row was actually deleted.
        """
        # Check existence first so callers can distinguish "didn't exist" from "deleted".
        meeting = await self._repository.get_meeting(meeting_id)
        if meeting is None:
            return False
        await self._repository.delete_meeting(meeting_id)
        return True


__all__ = ["MeetingHistoryService"]
