"""SQLite-backed MeetingHistoryRepository.

Uses stdlib ``sqlite3`` only, no external dependencies.  All database work is
off-loaded to a thread pool via ``asyncio.to_thread`` to avoid blocking the
event loop.  A single connection is guarded by a ``threading.RLock``.
"""

import asyncio
import logging
import sqlite3
import threading
import typing
from datetime import datetime
from pathlib import Path
from typing import cast

from app.meetings.history_models import (
    CompletedMeetingStorageRecord,
    MeetingListItemRecord,
    MeetingRecord,
    MeetingStatus,
    MeetingTurnRecord,
    RecordingAsset,
    RecordingFormat,
    RecordingRole,
    ReplySuggestionRecord,
)

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 2

_SQL_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds INTEGER,
    title TEXT,
    ai_note TEXT NOT NULL DEFAULT '',
    minutes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'completed', 'aborted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meetings_started_at_id_desc
    ON meetings(started_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS meeting_turns (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    speaker TEXT NOT NULL,
    text TEXT NOT NULL,
    speaker_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(meeting_id, sequence)
);

CREATE TABLE IF NOT EXISTS reply_suggestions (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    target_turn_id TEXT NOT NULL REFERENCES meeting_turns(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    agent_id TEXT NOT NULL,
    agent_label TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(target_turn_id, sequence)
);

CREATE TABLE IF NOT EXISTS recording_assets (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('other', 'self')),
    relative_path TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'wav'
        CHECK(format IN ('wav', 'mp3', 'ogg', 'flac', 'webm')),
    sample_rate INTEGER NOT NULL DEFAULT 16000,
    channels INTEGER NOT NULL DEFAULT 1,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    size_bytes INTEGER,
    UNIQUE(meeting_id, role)
);
"""


def _iso_now() -> str:
    return datetime.now().isoformat()


# ── Row decoder helpers ────────────────────────────────────────────────────
# These functions decode untrusted sqlite3.Row values into typed Python values.
# sqlite3.Row.__getitem__ returns Any, so every access is validated here
# rather than propagated into domain dataclass construction.


def _row_value(row: sqlite3.Row, col: str) -> object:
    """Read a column value as ``object``, breaking the ``Any`` chain.

    ``sqlite3.Row.__getitem__`` is typed as returning ``Any``.  Every decoder
    routes through this helper so that the unavoidable boundary suppression
    lives in one place.
    """
    return row[col]  # pyright: ignore[reportAny] — unavoidable: sqlite3.Row trust boundary


def _required_str(row: sqlite3.Row, col: str) -> str:
    """Extract a required ``str`` column, rejecting ``None`` and non-str types."""
    val = _row_value(row, col)
    if val is None:
        raise ValueError(f"Column {col!r} is NULL but a str is required")
    if isinstance(val, str):
        return val
    raise ValueError(f"Column {col!r}: expected str, got {type(val).__name__}: {val!r}")


def _optional_str(row: sqlite3.Row, col: str) -> str | None:
    """Extract an optional ``str`` column."""
    val = _row_value(row, col)
    if val is None:
        return None
    if isinstance(val, str):
        return val
    raise ValueError(f"Column {col!r}: expected str | None, got {type(val).__name__}: {val!r}")


def _required_int(row: sqlite3.Row, col: str) -> int:
    """Extract a required ``int`` column, rejecting ``None`` and ``bool``.

    ``bool`` is explicitly rejected because ``bool`` is a subclass of ``int``
    in Python and would otherwise pass an ``isinstance(val, int)`` check.
    """
    val = _row_value(row, col)
    if val is None:
        raise ValueError(f"Column {col!r} is NULL but an int is required")
    if isinstance(val, bool):
        raise ValueError(f"Column {col!r}: expected int, got bool ({val!r})")
    if isinstance(val, int):
        return val
    raise ValueError(f"Column {col!r}: expected int, got {type(val).__name__}: {val!r}")


def _optional_int(row: sqlite3.Row, col: str) -> int | None:
    """Extract an optional ``int`` column, rejecting ``bool``."""
    val = _row_value(row, col)
    if val is None:
        return None
    if isinstance(val, bool):
        raise ValueError(f"Column {col!r}: expected int | None, got bool ({val!r})")
    if isinstance(val, int):
        return val
    raise ValueError(f"Column {col!r}: expected int | None, got {type(val).__name__}: {val!r}")


def _required_datetime(row: sqlite3.Row, col: str) -> datetime:
    """Extract a required ISO-8601 datetime column.

    Raises ``ValueError`` if the value is ``None`` or not parseable via
    ``datetime.fromisoformat``.
    """
    raw = _required_str(row, col)
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"Column {col!r}: invalid ISO datetime {raw!r}: {exc}") from exc


def _optional_datetime(row: sqlite3.Row, col: str) -> datetime | None:
    """Extract an optional ISO-8601 datetime column."""
    raw = _optional_str(row, col)
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"Column {col!r}: invalid ISO datetime {raw!r}: {exc}") from exc


# ── Literal validators ─────────────────────────────────────────────────────
# Each Literal type alias (MeetingStatus, RecordingRole, RecordingFormat) is
# not a class, so a single generic _validate_literal cannot be expressed via
# "type[T]" in a way that pyright accepts.  Instead, we have one concrete
# validator per Literal type, each backed by the shared _check_literal helper.
# The cast inside each is narrow and justified: we verify membership and then
# tell the type checker the returned value is the Literal type.


def _check_literal(value: str, valid_values: tuple[str, ...], col: str) -> None:
    """Validate *value* is in *valid_values*, raising ``ValueError`` if not."""
    if value not in valid_values:
        raise ValueError(f"Invalid value for column {col!r}: {value!r}. Expected one of {valid_values}")


def _validate_meeting_status(value: str, col: str) -> MeetingStatus:
    """Validate that *value* is a valid ``MeetingStatus`` literal."""
    valid_values = typing.get_args(MeetingStatus)
    _check_literal(value, valid_values, col)
    # Narrow, justified cast: see note above.
    return cast(MeetingStatus, value)


def _validate_recording_role(value: str, col: str) -> RecordingRole:
    """Validate that *value* is a valid ``RecordingRole`` literal."""
    valid_values = typing.get_args(RecordingRole)
    _check_literal(value, valid_values, col)
    return cast(RecordingRole, value)


def _validate_recording_format(value: str, col: str) -> RecordingFormat:
    """Validate that *value* is a valid ``RecordingFormat`` literal."""
    valid_values = typing.get_args(RecordingFormat)
    _check_literal(value, valid_values, col)
    return cast(RecordingFormat, value)


def _fetch_rows(cursor: sqlite3.Cursor) -> list[sqlite3.Row]:
    """Fetch all rows, typed as ``list[sqlite3.Row]``.

    typeshed types ``Cursor.fetchall()`` as returning ``list[Any]``, but with
    ``row_factory = sqlite3.Row`` every row is a ``sqlite3.Row`` at runtime.
    The narrow ``cast`` here is the trust boundary after the factory switch.
    """
    # Cursor.fetchall() is typed as list[Any] in typeshed; row_factory guarantees sqlite3.Row at runtime.
    return cast(list[sqlite3.Row], cursor.fetchall())


def _fetch_one(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    """Fetch one row, typed as ``sqlite3.Row | None``.

    See ``_fetch_rows`` for rationale.
    """
    # Cursor.fetchone() is typed as Any | None in typeshed; row_factory guarantees sqlite3.Row at runtime.
    return cast(sqlite3.Row | None, cursor.fetchone())


# ── Row-to-record mappers ──────────────────────────────────────────────────
# These functions form the trust boundary between the raw DB row and the rest
# of the application.  Every value is type-validated before being passed to a
# domain dataclass constructor.


def _meeting_from_row(row: sqlite3.Row) -> MeetingRecord:
    """Decode a ``meetings`` row into a ``MeetingRecord`` with validation."""
    return MeetingRecord(
        id=_required_str(row, "id"),
        started_at=_required_datetime(row, "started_at"),
        status=_validate_meeting_status(_required_str(row, "status"), "status"),
        ended_at=_optional_datetime(row, "ended_at"),
        duration_seconds=_optional_int(row, "duration_seconds"),
        title=_optional_str(row, "title"),
        ai_note=_required_str(row, "ai_note"),
        minutes=_required_str(row, "minutes"),
        created_at=_optional_datetime(row, "created_at"),
        updated_at=_optional_datetime(row, "updated_at"),
    )


def _list_item_from_row(row: sqlite3.Row) -> MeetingListItemRecord:
    """Decode a meeting row (with extra ``has_recording`` column) into ``MeetingListItemRecord``.

    ``has_recording`` comes from the SQL subquery as INTEGER (0/1).  ``_required_int``
    is used to reject ``bool`` (which is a subclass of ``int``) and ``None``,
    consistent with the other int decoder helpers in this module.
    """
    # has_recording comes from the SQL subquery as INTEGER (0/1); _required_int
    # rejects bool (subclass of int) and None.
    raw_has_recording = _required_int(row, "has_recording")
    return MeetingListItemRecord(
        id=_required_str(row, "id"),
        started_at=_required_datetime(row, "started_at"),
        status=_validate_meeting_status(_required_str(row, "status"), "status"),
        ended_at=_optional_datetime(row, "ended_at"),
        duration_seconds=_optional_int(row, "duration_seconds"),
        title=_optional_str(row, "title"),
        ai_note=_required_str(row, "ai_note"),
        created_at=_optional_datetime(row, "created_at"),
        updated_at=_optional_datetime(row, "updated_at"),
        has_recording=bool(raw_has_recording),
    )


def _turn_from_row(row: sqlite3.Row) -> MeetingTurnRecord:
    """Decode a ``meeting_turns`` row into a ``MeetingTurnRecord`` with validation."""
    return MeetingTurnRecord(
        id=_required_str(row, "id"),
        meeting_id=_required_str(row, "meeting_id"),
        sequence=_required_int(row, "sequence"),
        speaker=_required_str(row, "speaker"),
        text=_required_str(row, "text"),
        speaker_id=_optional_str(row, "speaker_id"),
        created_at=_optional_datetime(row, "created_at"),
    )


def _reply_suggestion_from_row(row: sqlite3.Row) -> ReplySuggestionRecord:
    """Decode a ``reply_suggestions`` row into a ``ReplySuggestionRecord`` with validation."""
    return ReplySuggestionRecord(
        id=_required_str(row, "id"),
        meeting_id=_required_str(row, "meeting_id"),
        target_turn_id=_required_str(row, "target_turn_id"),
        sequence=_required_int(row, "sequence"),
        agent_id=_required_str(row, "agent_id"),
        agent_label=_required_str(row, "agent_label"),
        text=_required_str(row, "text"),
        created_at=_optional_datetime(row, "created_at"),
    )


def _recording_asset_from_row(row: sqlite3.Row) -> RecordingAsset:
    """Decode a ``recording_assets`` row into a ``RecordingAsset`` with validation."""
    return RecordingAsset(
        id=_required_str(row, "id"),
        meeting_id=_required_str(row, "meeting_id"),
        role=_validate_recording_role(_required_str(row, "role"), "role"),
        relative_path=_required_str(row, "relative_path"),
        format=_validate_recording_format(_required_str(row, "format"), "format"),
        sample_rate=_required_int(row, "sample_rate"),
        channels=_required_int(row, "channels"),
        started_at=_required_datetime(row, "started_at"),
        ended_at=_optional_datetime(row, "ended_at"),
        size_bytes=_optional_int(row, "size_bytes"),
    )


class SqliteMeetingHistoryRepository:
    """Thread-safe SQLite repository for meeting history.

    Uses a single connection with ``WAL`` journaling and foreign keys enabled.
    All public async methods delegate synchronous work to a thread pool.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path: str = str(db_path)
        self._lock: threading.RLock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        def _init() -> None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            _ = conn.execute("PRAGMA journal_mode=WAL")
            _ = conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            _ = conn.executescript(_SQL_CREATE_TABLES)
            version_row = _fetch_one(conn.execute("SELECT MAX(version) AS version FROM schema_version"))
            current_version = (
                _required_int(version_row, "version")
                if version_row is not None and _row_value(version_row, "version") is not None
                else 0
            )
            if current_version < 2:
                if current_version == 1:
                    _ = conn.execute("ALTER TABLE meetings ADD COLUMN minutes TEXT NOT NULL DEFAULT ''")
                _ = conn.execute("DELETE FROM schema_version")
                _ = conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (_SCHEMA_VERSION, _iso_now()),
                )
            conn.commit()
            self._conn = conn

        await asyncio.to_thread(_init)
        logger.info("SQLite repository initialised: %s (WAL, foreign_keys=ON)", self._db_path)

    async def close(self) -> None:
        def _close() -> None:
            with self._lock:
                if self._conn is not None:
                    self._conn.close()
                    self._conn = None

        await asyncio.to_thread(_close)
        logger.info("SQLite repository closed: %s", self._db_path)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SQLite repository not initialized")
        return self._conn

    # ── Meetings ──────────────────────────────────────────────────────────────

    async def create_meeting(self, record: MeetingRecord) -> None:
        def _run() -> None:
            now = _iso_now()
            with self._lock:
                conn = self._require_conn()
                _ = conn.execute(
                    """INSERT INTO meetings
                       (id, started_at, status, ended_at, duration_seconds,
                        title, ai_note, minutes, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.id,
                        record.started_at.isoformat(),
                        record.status,
                        record.ended_at.isoformat() if record.ended_at else None,
                        record.duration_seconds,
                        record.title,
                        record.ai_note,
                        record.minutes,
                        record.created_at.isoformat() if record.created_at else now,
                        record.updated_at.isoformat() if record.updated_at else now,
                    ),
                )
                conn.commit()

        await asyncio.to_thread(_run)

    async def complete_meeting(
        self,
        meeting_id: str,
        ended_at: datetime,
        duration_seconds: int | None = None,
        ai_note: str = "",
    ) -> None:
        def _run() -> None:
            now = _iso_now()
            with self._lock:
                conn = self._require_conn()
                _ = conn.execute(
                    """UPDATE meetings
                       SET status = 'completed', ended_at = ?, duration_seconds = ?, ai_note = ?, updated_at = ?
                       WHERE id = ?""",
                    (ended_at.isoformat(), duration_seconds, ai_note, now, meeting_id),
                )
                conn.commit()

        await asyncio.to_thread(_run)

    async def abort_meeting(self, meeting_id: str, ended_at: datetime) -> None:
        def _run() -> None:
            now = _iso_now()
            with self._lock:
                conn = self._require_conn()
                _ = conn.execute(
                    """UPDATE meetings
                       SET status = 'aborted', ended_at = ?, updated_at = ?
                       WHERE id = ?""",
                    (ended_at.isoformat(), now, meeting_id),
                )
                conn.commit()

        await asyncio.to_thread(_run)

    async def list_meetings(self, *, limit: int = 50, offset: int = 0) -> list[MeetingListItemRecord]:
        def _run() -> list[MeetingListItemRecord]:
            with self._lock:
                conn = self._require_conn()
                rows = _fetch_rows(
                    conn.execute(
                        """SELECT m.*,
                                  CASE WHEN EXISTS (SELECT 1 FROM recording_assets WHERE meeting_id = m.id)
                                       THEN 1 ELSE 0 END AS has_recording
                           FROM meetings m
                           ORDER BY m.started_at DESC, m.id DESC
                           LIMIT ? OFFSET ?""",
                        (limit, offset),
                    )
                )
            return [_list_item_from_row(r) for r in rows]

        return await asyncio.to_thread(_run)

    async def count_meetings(self) -> int:
        def _run() -> int:
            with self._lock:
                conn = self._require_conn()
                row = _fetch_one(conn.execute("SELECT COUNT(*) AS count FROM meetings"))
            if row is None:
                return 0
            count = _row_value(row, "count")
            if isinstance(count, bool) or not isinstance(count, int):
                raise ValueError(f"Expected integer meeting count, got {count!r}")
            return count

        return await asyncio.to_thread(_run)

    async def update_meeting_title(self, meeting_id: str, title: str) -> int:
        def _run() -> int:
            now = _iso_now()
            with self._lock:
                conn = self._require_conn()
                cursor = conn.execute(
                    "UPDATE meetings SET title = ?, updated_at = ? WHERE id = ?",
                    (title, now, meeting_id),
                )
                conn.commit()
                return cursor.rowcount

        return await asyncio.to_thread(_run)

    async def update_meeting_minutes(self, meeting_id: str, minutes: str) -> int:
        def _run() -> int:
            now = _iso_now()
            with self._lock:
                conn = self._require_conn()
                cursor = conn.execute(
                    "UPDATE meetings SET minutes = ?, updated_at = ? WHERE id = ?",
                    (minutes, now, meeting_id),
                )
                conn.commit()
                return cursor.rowcount

        return await asyncio.to_thread(_run)

    async def delete_meeting(self, meeting_id: str) -> None:
        def _run() -> None:
            with self._lock:
                conn = self._require_conn()
                _ = conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
                conn.commit()

        await asyncio.to_thread(_run)

    async def list_completed_meeting_storage_oldest(self) -> list[CompletedMeetingStorageRecord]:
        """Return completed meetings and persisted recording bytes, oldest first."""

        def _run() -> list[CompletedMeetingStorageRecord]:
            with self._lock:
                conn = self._require_conn()
                rows = _fetch_rows(
                    conn.execute(
                        """SELECT m.*, COALESCE(SUM(recording_assets.size_bytes), 0) AS recording_size_bytes
                           FROM meetings AS m
                           LEFT JOIN recording_assets ON recording_assets.meeting_id = m.id
                           WHERE m.status = 'completed'
                           GROUP BY m.id
                           ORDER BY m.ended_at ASC, m.started_at ASC, m.id ASC"""
                    )
                )
            return [
                CompletedMeetingStorageRecord(
                    meeting=_meeting_from_row(row),
                    recording_size_bytes=_required_int(row, "recording_size_bytes"),
                )
                for row in rows
            ]

        return await asyncio.to_thread(_run)

    async def get_meeting(self, meeting_id: str) -> MeetingRecord | None:
        def _run() -> MeetingRecord | None:
            with self._lock:
                conn = self._require_conn()
                row = _fetch_one(conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)))
            if row is None:
                return None
            return _meeting_from_row(row)

        return await asyncio.to_thread(_run)

    # ── Turns ─────────────────────────────────────────────────────────────────

    async def insert_turn(self, record: MeetingTurnRecord) -> None:
        def _run() -> None:
            with self._lock:
                conn = self._require_conn()
                _ = conn.execute(
                    """INSERT INTO meeting_turns (id, meeting_id, sequence, speaker, text, speaker_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.id,
                        record.meeting_id,
                        record.sequence,
                        record.speaker,
                        record.text,
                        record.speaker_id,
                        record.created_at.isoformat() if record.created_at else _iso_now(),
                    ),
                )
                conn.commit()

        await asyncio.to_thread(_run)

    async def list_turns(self, meeting_id: str) -> list[MeetingTurnRecord]:
        def _run() -> list[MeetingTurnRecord]:
            with self._lock:
                conn = self._require_conn()
                rows = _fetch_rows(
                    conn.execute(
                        "SELECT * FROM meeting_turns WHERE meeting_id = ? ORDER BY sequence",
                        (meeting_id,),
                    )
                )
            return [_turn_from_row(r) for r in rows]

        return await asyncio.to_thread(_run)

    # ── Reply suggestions ─────────────────────────────────────────────────────

    async def insert_reply_suggestion(self, record: ReplySuggestionRecord) -> None:
        def _run() -> None:
            with self._lock:
                conn = self._require_conn()
                _ = conn.execute(
                    """INSERT INTO reply_suggestions
                       (id, meeting_id, target_turn_id, sequence, agent_id, agent_label, text, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.id,
                        record.meeting_id,
                        record.target_turn_id,
                        record.sequence,
                        record.agent_id,
                        record.agent_label,
                        record.text,
                        record.created_at.isoformat() if record.created_at else _iso_now(),
                    ),
                )
                conn.commit()

        await asyncio.to_thread(_run)

    async def list_reply_suggestions(self, meeting_id: str) -> list[ReplySuggestionRecord]:
        def _run() -> list[ReplySuggestionRecord]:
            with self._lock:
                conn = self._require_conn()
                rows = _fetch_rows(
                    conn.execute(
                        "SELECT * FROM reply_suggestions WHERE meeting_id = ? ORDER BY sequence",
                        (meeting_id,),
                    )
                )
            return [_reply_suggestion_from_row(r) for r in rows]

        return await asyncio.to_thread(_run)

    # ── Recording assets ──────────────────────────────────────────────────────

    async def insert_recording_assets(self, records: list[RecordingAsset]) -> None:
        """Atomically persist a recording finalisation's asset rows."""
        if not records:
            return

        def _run() -> None:
            values = [
                (
                    record.id,
                    record.meeting_id,
                    record.role,
                    record.relative_path,
                    record.format,
                    record.sample_rate,
                    record.channels,
                    record.started_at.isoformat(),
                    record.ended_at.isoformat() if record.ended_at else None,
                    record.size_bytes,
                )
                for record in records
            ]
            with self._lock:
                conn = self._require_conn()
                with conn:
                    _ = conn.executemany(
                        """INSERT INTO recording_assets
                           (id, meeting_id, role, relative_path, format,
                            sample_rate, channels, started_at, ended_at,
                            size_bytes)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        values,
                    )

        await asyncio.to_thread(_run)

    async def get_recording_asset_by_role(self, meeting_id: str, role: str) -> RecordingAsset | None:
        def _run() -> RecordingAsset | None:
            with self._lock:
                conn = self._require_conn()
                row = _fetch_one(
                    conn.execute(
                        "SELECT * FROM recording_assets WHERE meeting_id = ? AND role = ?",
                        (meeting_id, role),
                    )
                )
            if row is None:
                return None
            return _recording_asset_from_row(row)

        return await asyncio.to_thread(_run)

    async def list_recording_assets(self, meeting_id: str) -> list[RecordingAsset]:
        def _run() -> list[RecordingAsset]:
            with self._lock:
                conn = self._require_conn()
                rows = _fetch_rows(
                    conn.execute(
                        "SELECT * FROM recording_assets WHERE meeting_id = ?",
                        (meeting_id,),
                    )
                )
            return [_recording_asset_from_row(r) for r in rows]

        return await asyncio.to_thread(_run)


__all__ = ["SqliteMeetingHistoryRepository"]
