"""Persistence dataclasses for meeting history (ADR-003 Phase 1).

These types mirror the runtime models in ``models.py`` but are optimised for
storage: frozen, serialisable, and carrying the exact fields that get written
to the database.  They are the currency between ``MeetingHistoryService`` and
``MeetingHistoryRepository``.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

MeetingStatus = Literal["active", "completed", "aborted"]
RecordingRole = Literal["other", "self"]
RecordingFormat = Literal["wav", "mp3", "ogg", "flac", "webm"]


@dataclass(frozen=True)
class MeetingRecord:
    id: str
    started_at: datetime
    status: MeetingStatus = "active"
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    title: str | None = None
    ai_note: str = ""
    minutes: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class MeetingTurnRecord:
    id: str
    meeting_id: str
    sequence: int
    speaker: str
    text: str
    speaker_id: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class ReplySuggestionRecord:
    id: str
    meeting_id: str
    target_turn_id: str
    sequence: int
    agent_id: str
    agent_label: str
    text: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class RecordingAsset:
    """Recording audio asset associated with a meeting.

    Persisted atomically by ``MeetingHistoryService`` during recording
    finalisation. WAV files live under
    ``<user_data_dir>/recordings/<meeting_id>/``.
    """

    id: str
    meeting_id: str
    role: RecordingRole
    relative_path: str
    started_at: datetime
    format: RecordingFormat = "wav"
    sample_rate: int = 16000
    channels: int = 1
    ended_at: datetime | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class CompletedMeetingStorageRecord:
    """A completed meeting and the persisted size of its recording assets.

    Retention cleanup uses this narrow projection so it never considers active
    or aborted meetings and can delete completed recordings oldest first.
    """

    meeting: MeetingRecord
    recording_size_bytes: int


@dataclass(frozen=True)
class MeetingListItemRecord:
    """Lightweight meeting list item with computed UI flags.

    Carries the same meeting fields as ``MeetingRecord`` plus a
    ``has_recording`` flag computed from the recording_assets table.
    ``has_ai_note`` is derived from ``ai_note`` at the API response layer.
    """

    id: str
    started_at: datetime
    status: MeetingStatus = "active"
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    title: str | None = None
    ai_note: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    has_recording: bool = False


__all__ = [
    "CompletedMeetingStorageRecord",
    "MeetingListItemRecord",
    "MeetingRecord",
    "MeetingStatus",
    "MeetingTurnRecord",
    "RecordingAsset",
    "RecordingFormat",
    "RecordingRole",
    "ReplySuggestionRecord",
]
