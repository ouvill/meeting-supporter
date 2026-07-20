# pyright: reportUninitializedInstanceVariable=false
"""Tests for SqliteMeetingHistoryRepository — CRUD, constraints, status checks."""

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override

from app.meetings.history_models import (
    MeetingRecord,
    MeetingTurnRecord,
    RecordingAsset,
    ReplySuggestionRecord,
)
from app.meetings.sqlite_repository import SqliteMeetingHistoryRepository


class SqliteRepositoryTest(unittest.IsolatedAsyncioTestCase):
    repo: SqliteMeetingHistoryRepository

    @override
    async def asyncSetUp(self) -> None:
        self.repo = SqliteMeetingHistoryRepository(":memory:")
        await self.repo.initialize()

    @override
    async def asyncTearDown(self) -> None:
        await self.repo.close()

    # ── Internal connection helper (private API access for test verification) ─

    def _conn(self) -> sqlite3.Connection:
        return self.repo._require_conn()  # pyright: ignore[reportPrivateUsage]  # tests need direct DB access for verification

    # ── Schema version ────────────────────────────────────────────────────────

    async def test_v1_database_migrates_existing_meeting_and_initializes_minutes(self) -> None:
        """Opening a v1 database preserves its meeting history and gives it an empty minutes artifact."""
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "history.sqlite3"
            connection = sqlite3.connect(database)
            _ = connection.executescript(
                """
                CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
                INSERT INTO schema_version (version, applied_at) VALUES (1, '2026-07-08T00:00:00+00:00');
                CREATE TABLE meetings (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_seconds INTEGER,
                    title TEXT,
                    ai_note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active', 'completed', 'aborted')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO meetings (
                    id, started_at, ended_at, duration_seconds, title, ai_note, status, created_at, updated_at
                ) VALUES (
                    'v1-completed', '2026-07-08T09:00:00+00:00', '2026-07-08T09:30:00+00:00', 1800,
                    'Existing meeting', 'Existing note', 'completed',
                    '2026-07-08T09:00:00+00:00', '2026-07-08T09:30:00+00:00'
                );
                """
            )
            connection.close()

            upgraded = SqliteMeetingHistoryRepository(database)
            try:
                await upgraded.initialize()
                meeting = await upgraded.get_meeting("v1-completed")
                assert meeting is not None
                self.assertEqual("Existing meeting", meeting.title)
                self.assertEqual("Existing note", meeting.ai_note)
                self.assertEqual("completed", meeting.status)
                self.assertEqual("", meeting.minutes)
            finally:
                await upgraded.close()

    # ── Meetings ──────────────────────────────────────────────────────────────

    async def test_create_and_get_meeting(self) -> None:
        record = MeetingRecord(
            id="m1",
            started_at=datetime.now(UTC),
            title="Test Meeting",
        )
        await self.repo.create_meeting(record)
        fetched = await self.repo.get_meeting("m1")
        assert fetched is not None
        self.assertEqual(fetched.id, "m1")
        self.assertEqual(fetched.title, "Test Meeting")
        self.assertEqual(fetched.status, "active")
        self.assertIsNotNone(fetched.created_at)
        self.assertIsNotNone(fetched.updated_at)

    async def test_get_meeting_nonexistent(self) -> None:
        result = await self.repo.get_meeting("nonexistent")
        self.assertIsNone(result)

    async def test_complete_meeting(self) -> None:
        started = datetime.now(UTC)
        record = MeetingRecord(id="m2", started_at=started)
        await self.repo.create_meeting(record)

        ended = started + timedelta(minutes=5)
        await self.repo.complete_meeting(
            meeting_id="m2",
            ended_at=ended,
            duration_seconds=300,
            ai_note="Test note",
        )

        fetched = await self.repo.get_meeting("m2")
        assert fetched is not None
        self.assertEqual(fetched.status, "completed")
        self.assertIsNotNone(fetched.ended_at)
        self.assertEqual(fetched.duration_seconds, 300)
        self.assertEqual(fetched.ai_note, "Test note")

    async def test_abort_meeting(self) -> None:
        record = MeetingRecord(id="m3", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)

        ended = datetime.now(UTC)
        await self.repo.abort_meeting(meeting_id="m3", ended_at=ended)

        fetched = await self.repo.get_meeting("m3")
        assert fetched is not None
        self.assertEqual(fetched.status, "aborted")
        self.assertIsNotNone(fetched.ended_at)

    async def test_meeting_status_check_enforced(self) -> None:
        """Verify the status CHECK constraint rejects invalid values."""
        conn = self._conn()
        with self.assertRaises(sqlite3.IntegrityError):
            _ = conn.execute(
                "INSERT INTO meetings (id, started_at, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("bad-status", "2024-01-01T00:00:00", "invalid_status", "2024-01-01T00:00:00", "2024-01-01T00:00:00"),
            )
            conn.commit()

    async def test_meeting_created_at_updated_at_default(self) -> None:
        """created_at and updated_at are required NOT NULL."""
        conn = self._conn()
        with self.assertRaises(sqlite3.IntegrityError):
            _ = conn.execute(
                "INSERT INTO meetings (id, started_at, status) VALUES (?, ?, ?)",
                ("no-ts", "2024-01-01T00:00:00", "active"),
            )
            conn.commit()

    # ── Turns ─────────────────────────────────────────────────────────────────

    async def test_insert_and_list_turns(self) -> None:
        record = MeetingRecord(id="m4", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)

        turn1 = MeetingTurnRecord(id="t1", meeting_id="m4", sequence=1, speaker="other", text="Hello")
        turn2 = MeetingTurnRecord(id="t2", meeting_id="m4", sequence=2, speaker="self", text="Hi there")
        await self.repo.insert_turn(turn1)
        await self.repo.insert_turn(turn2)

        turns = await self.repo.list_turns("m4")
        self.assertEqual(2, len(turns))
        self.assertEqual("t1", turns[0].id)
        self.assertEqual(1, turns[0].sequence)
        self.assertEqual("t2", turns[1].id)
        self.assertEqual(2, turns[1].sequence)

    async def test_empty_collections_return_empty_lists(self) -> None:
        cases = (
            ("turns", await self.repo.list_turns("nonexistent")),
            ("reply suggestions", await self.repo.list_reply_suggestions("nonexistent")),
            ("meetings", await self.repo.list_meetings()),
        )
        for collection, records in cases:
            with self.subTest(collection=collection):
                self.assertEqual([], records)

    async def test_turn_foreign_key_enforced(self) -> None:
        turn = MeetingTurnRecord(id="orphan", meeting_id="nonexistent", sequence=1, speaker="other", text="x")
        with self.assertRaises(sqlite3.IntegrityError):
            await self.repo.insert_turn(turn)

    async def test_turn_unique_meeting_sequence(self) -> None:
        """UNIQUE(meeting_id, sequence) is enforced."""
        record = MeetingRecord(id="m-uniq-seq", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)
        turn1 = MeetingTurnRecord(id="t-uniq-1", meeting_id="m-uniq-seq", sequence=1, speaker="other", text="A")
        turn2 = MeetingTurnRecord(id="t-uniq-2", meeting_id="m-uniq-seq", sequence=1, speaker="self", text="B")
        await self.repo.insert_turn(turn1)
        with self.assertRaises(sqlite3.IntegrityError):
            await self.repo.insert_turn(turn2)

    # ── Reply suggestions ─────────────────────────────────────────────────────

    async def test_insert_and_list_reply_suggestions(self) -> None:
        record = MeetingRecord(id="m5", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)

        await self.repo.insert_turn(MeetingTurnRecord(id="t1", meeting_id="m5", sequence=1, speaker="other", text="Q"))

        sug1 = ReplySuggestionRecord(
            id="s1",
            meeting_id="m5",
            target_turn_id="t1",
            sequence=1,
            agent_id="agent_a",
            agent_label="Agent A",
            text="Reply A",
        )
        sug2 = ReplySuggestionRecord(
            id="s2",
            meeting_id="m5",
            target_turn_id="t1",
            sequence=2,
            agent_id="agent_b",
            agent_label="Agent B",
            text="Reply B",
        )
        await self.repo.insert_reply_suggestion(sug1)
        await self.repo.insert_reply_suggestion(sug2)

        suggestions = await self.repo.list_reply_suggestions("m5")
        self.assertEqual(2, len(suggestions))
        self.assertEqual("s1", suggestions[0].id)
        self.assertEqual("s2", suggestions[1].id)
        self.assertEqual("t1", suggestions[0].target_turn_id)
        self.assertEqual("t1", suggestions[1].target_turn_id)

    async def test_reply_suggestion_target_turn_fk_enforced(self) -> None:
        """target_turn_id must reference an existing meeting_turn."""
        record = MeetingRecord(id="m-fk-target", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)
        sug = ReplySuggestionRecord(
            id="s-orphan",
            meeting_id="m-fk-target",
            target_turn_id="nonexistent-turn",
            sequence=1,
            agent_id="agent_a",
            agent_label="Agent A",
            text="x",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            await self.repo.insert_reply_suggestion(sug)

    async def test_reply_suggestion_unique_target_turn_sequence(self) -> None:
        """UNIQUE(target_turn_id, sequence) is enforced."""
        record = MeetingRecord(id="m-uniq-sug", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)
        await self.repo.insert_turn(
            MeetingTurnRecord(id="t-uniq-sug", meeting_id="m-uniq-sug", sequence=1, speaker="other", text="Q")
        )
        sug1 = ReplySuggestionRecord(
            id="s-uniq-1",
            meeting_id="m-uniq-sug",
            target_turn_id="t-uniq-sug",
            sequence=1,
            agent_id="agent_a",
            agent_label="Agent A",
            text="A",
        )
        sug2 = ReplySuggestionRecord(
            id="s-uniq-2",
            meeting_id="m-uniq-sug",
            target_turn_id="t-uniq-sug",
            sequence=1,
            agent_id="agent_b",
            agent_label="Agent B",
            text="B",
        )
        await self.repo.insert_reply_suggestion(sug1)
        with self.assertRaises(sqlite3.IntegrityError):
            await self.repo.insert_reply_suggestion(sug2)

    # ── Recording assets ──────────────────────────────────────────────────────

    async def test_insert_and_list_recording_assets(self) -> None:
        record = MeetingRecord(id="m6", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)

        asset1 = RecordingAsset(
            id="r1",
            meeting_id="m6",
            role="other",
            relative_path="recordings/other.wav",
            started_at=datetime.now(UTC),
        )
        asset2 = RecordingAsset(
            id="r2",
            meeting_id="m6",
            role="self",
            relative_path="recordings/self.wav",
            started_at=datetime.now(UTC),
        )
        await self.repo.insert_recording_assets([asset1, asset2])

        assets = await self.repo.list_recording_assets("m6")
        self.assertEqual(2, len(assets))
        self.assertEqual("r1", assets[0].id)
        self.assertEqual("other", assets[0].role)
        self.assertEqual("recordings/other.wav", assets[0].relative_path)
        self.assertEqual("r2", assets[1].id)
        self.assertEqual("self", assets[1].role)
        self.assertEqual("recordings/self.wav", assets[1].relative_path)

    async def test_recording_asset_batch_failure_rolls_back_every_asset(self) -> None:
        """A failed finalisation batch leaves no durable metadata for either file."""
        record = MeetingRecord(id="m-atomic-assets", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)
        first = RecordingAsset(
            id="asset-first",
            meeting_id=record.id,
            role="other",
            relative_path="recordings/m-atomic-assets/other.wav",
            started_at=datetime.now(UTC),
        )
        conflicting_second = RecordingAsset(
            id="asset-conflict",
            meeting_id=record.id,
            role="other",
            relative_path="recordings/m-atomic-assets/other-retry.wav",
            started_at=datetime.now(UTC),
        )

        with self.assertRaises(sqlite3.IntegrityError):
            await self.repo.insert_recording_assets([first, conflicting_second])

        self.assertEqual([], await self.repo.list_recording_assets(record.id))

    # ── List meetings ──────────────────────────────────────────────────────────

    async def test_list_meetings_returns_all_ordered(self) -> None:
        same_started = datetime.now(UTC)
        m1 = MeetingRecord(id="list-m1", started_at=same_started - timedelta(hours=2), title="Oldest")
        m2 = MeetingRecord(id="list-m2", started_at=same_started, title="Middle")
        m3 = MeetingRecord(id="list-m3", started_at=same_started, title="Newest")
        await self.repo.create_meeting(m1)
        await self.repo.create_meeting(m2)
        await self.repo.create_meeting(m3)

        meetings = await self.repo.list_meetings()
        self.assertEqual(3, len(meetings))
        # Ordered by started_at DESC, then id DESC for deterministic ties.
        self.assertEqual("list-m3", meetings[0].id)
        self.assertEqual("list-m2", meetings[1].id)
        self.assertEqual("list-m1", meetings[2].id)
        self.assertEqual(["Newest", "Middle", "Oldest"], [meeting.title for meeting in meetings])

    async def test_list_meetings_paginates_without_overlap(self) -> None:
        same_started = datetime.now(UTC)
        for idx in range(5):
            await self.repo.create_meeting(
                MeetingRecord(id=f"page-m{idx}", started_at=same_started, title=f"Meeting {idx}")
            )

        first_page = await self.repo.list_meetings(limit=2, offset=0)
        second_page = await self.repo.list_meetings(limit=2, offset=2)
        third_page = await self.repo.list_meetings(limit=2, offset=4)

        self.assertEqual(["page-m4", "page-m3"], [m.id for m in first_page])
        self.assertEqual(["page-m2", "page-m1"], [m.id for m in second_page])
        self.assertEqual(["page-m0"], [m.id for m in third_page])
        self.assertEqual(5, await self.repo.count_meetings())

    async def test_list_meetings_has_recording(self) -> None:
        """MeetingListItemRecord.has_recording is True when recording assets exist."""
        m = MeetingRecord(id="list-rec", started_at=datetime.now(UTC))
        await self.repo.create_meeting(m)

        # No recording assets yet.
        items = await self.repo.list_meetings()
        self.assertFalse(items[0].has_recording)

        # Add a recording asset.
        asset = RecordingAsset(
            id="r-list",
            meeting_id="list-rec",
            role="other",
            relative_path="test.wav",
            started_at=datetime.now(UTC),
        )
        await self.repo.insert_recording_assets([asset])

        items = await self.repo.list_meetings()
        self.assertTrue(items[0].has_recording)

    # ── Update meeting title ───────────────────────────────────────────────────

    async def test_update_meeting_title(self) -> None:
        record = MeetingRecord(id="title-upd", started_at=datetime.now(UTC), title="Before")
        await self.repo.create_meeting(record)

        _ = await self.repo.update_meeting_title("title-upd", "After")
        fetched = await self.repo.get_meeting("title-upd")
        assert fetched is not None
        self.assertEqual("After", fetched.title)
        self.assertIsNotNone(fetched.updated_at)

    async def test_update_meeting_title_nonexistent_is_noop(self) -> None:
        """Updating a nonexistent meeting should not raise."""
        self.assertEqual(0, await self.repo.update_meeting_title("nonexistent", "Noop"))

    # ── Delete meeting ─────────────────────────────────────────────────────────

    async def test_delete_meeting(self) -> None:
        record = MeetingRecord(id="del-me", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)

        await self.repo.delete_meeting("del-me")
        fetched = await self.repo.get_meeting("del-me")
        self.assertIsNone(fetched)

    async def test_delete_meeting_nonexistent_is_noop(self) -> None:
        """Deleting a nonexistent meeting should not raise."""
        await self.repo.delete_meeting("nonexistent")

    # ── Get recording asset by role ────────────────────────────────────────────

    async def test_get_recording_asset_by_role(self) -> None:
        record = MeetingRecord(id="m-asset-role", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)

        asset = RecordingAsset(
            id="a-role",
            meeting_id="m-asset-role",
            role="other",
            relative_path="other.wav",
            started_at=datetime.now(UTC),
        )
        await self.repo.insert_recording_assets([asset])

        result = await self.repo.get_recording_asset_by_role("m-asset-role", "other")
        assert result is not None
        self.assertEqual("a-role", result.id)
        self.assertEqual("other", result.role)

    async def test_get_recording_asset_by_role_not_found(self) -> None:
        result = await self.repo.get_recording_asset_by_role("nonexistent", "other")
        self.assertIsNone(result)

    async def test_get_recording_asset_by_role_wrong_role(self) -> None:
        record = MeetingRecord(id="m-asset-role2", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)
        asset = RecordingAsset(
            id="a-role2",
            meeting_id="m-asset-role2",
            role="other",
            relative_path="other.wav",
            started_at=datetime.now(UTC),
        )
        await self.repo.insert_recording_assets([asset])

        # Requesting 'self' when only 'other' exists returns None.
        result = await self.repo.get_recording_asset_by_role("m-asset-role2", "self")
        self.assertIsNone(result)

    # ── Cascade delete ─────────────────────────────────────────────────────────

    async def test_turn_cascade_delete(self) -> None:
        """Deleting a meeting cascades to its turns."""
        record = MeetingRecord(id="m-cascade", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)
        await self.repo.insert_turn(
            MeetingTurnRecord(id="t-cascade", meeting_id="m-cascade", sequence=1, speaker="other", text="x")
        )
        await self.repo.delete_meeting("m-cascade")
        turns = await self.repo.list_turns("m-cascade")
        self.assertEqual([], turns)

    # ── Row validation (invalid DB values) ──────────────────────────────────
    # The tests below verify that the row decoder helpers raise ValueError when
    # DB rows contain invalid or malformed data.  We use direct SQL UPDATE to
    # bypass domain-level validators (CHECK constraints / dataclass defaults)
    # and inject corrupt values into the test database.
    #
    # Since the schema has CHECK constraints on status/role/format columns, we
    # temporarily disable them (PRAGMA ignore_check_constraints=ON) before the
    # UPDATE and re-enable afterwards so other tests are unaffected.
    #
    # SQLite NOT NULL constraints prevent direct NULL insertion for required
    # columns, so we cover "missing semantic value" via malformed datetime and
    # literal validation tests.

    async def test_invalid_meeting_status_raises_error(self) -> None:
        """Reading a meeting with an invalid status raises ValueError."""
        record = MeetingRecord(id="m-bad-status", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)
        conn = self._conn()
        _ = conn.execute("PRAGMA ignore_check_constraints=ON")
        _ = conn.execute("UPDATE meetings SET status = ? WHERE id = ?", ("invalid", "m-bad-status"))
        _ = conn.execute("PRAGMA ignore_check_constraints=OFF")
        conn.commit()
        with self.assertRaises(ValueError):
            _ = await self.repo.get_meeting("m-bad-status")

    async def test_invalid_meeting_status_in_list_raises_error(self) -> None:
        """Listing meetings when one has an invalid status raises ValueError."""
        good = MeetingRecord(id="m-good", started_at=datetime.now(UTC))
        await self.repo.create_meeting(good)
        bad = MeetingRecord(id="m-bad-list", started_at=datetime.now(UTC))
        await self.repo.create_meeting(bad)
        conn = self._conn()
        _ = conn.execute("PRAGMA ignore_check_constraints=ON")
        _ = conn.execute("UPDATE meetings SET status = ? WHERE id = ?", ("invalid", "m-bad-list"))
        _ = conn.execute("PRAGMA ignore_check_constraints=OFF")
        conn.commit()
        with self.assertRaises(ValueError):
            _ = await self.repo.list_meetings()

    async def test_invalid_recording_role_raises_error(self) -> None:
        """Listing recording assets when one has an invalid role raises ValueError."""
        record = MeetingRecord(id="m-bad-role", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)
        asset = RecordingAsset(
            id="a-bad-role",
            meeting_id="m-bad-role",
            role="other",
            relative_path="test.wav",
            started_at=datetime.now(UTC),
        )
        await self.repo.insert_recording_assets([asset])
        conn = self._conn()
        _ = conn.execute("PRAGMA ignore_check_constraints=ON")
        _ = conn.execute("UPDATE recording_assets SET role = ? WHERE id = ?", ("invalid", "a-bad-role"))
        _ = conn.execute("PRAGMA ignore_check_constraints=OFF")
        conn.commit()
        with self.assertRaises(ValueError):
            _ = await self.repo.list_recording_assets("m-bad-role")

    async def test_invalid_recording_format_raises_error(self) -> None:
        """Reading a recording asset with an invalid format raises ValueError."""
        record = MeetingRecord(id="m-bad-fmt", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)
        asset = RecordingAsset(
            id="a-bad-fmt",
            meeting_id="m-bad-fmt",
            role="other",
            relative_path="test.wav",
            started_at=datetime.now(UTC),
        )
        await self.repo.insert_recording_assets([asset])
        conn = self._conn()
        _ = conn.execute("PRAGMA ignore_check_constraints=ON")
        _ = conn.execute("UPDATE recording_assets SET format = ? WHERE id = ?", ("invalid", "a-bad-fmt"))
        _ = conn.execute("PRAGMA ignore_check_constraints=OFF")
        conn.commit()
        with self.assertRaises(ValueError):
            _ = await self.repo.list_recording_assets("m-bad-fmt")

    async def test_malformed_datetime_raises_error(self) -> None:
        """Reading a meeting with a non-parseable ISO datetime raises ValueError."""
        record = MeetingRecord(id="m-bad-dt", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)
        conn = self._conn()
        _ = conn.execute("UPDATE meetings SET started_at = ? WHERE id = ?", ("not-a-date", "m-bad-dt"))
        conn.commit()
        with self.assertRaises(ValueError):
            _ = await self.repo.get_meeting("m-bad-dt")

    async def test_get_recording_asset_by_role_malformed_datetime(self) -> None:
        """Reading a recording asset with a non-parseable started_at raises ValueError."""
        record = MeetingRecord(id="m-bad-asset-dt", started_at=datetime.now(UTC))
        await self.repo.create_meeting(record)
        asset = RecordingAsset(
            id="a-bad-asset-dt",
            meeting_id="m-bad-asset-dt",
            role="other",
            relative_path="test.wav",
            started_at=datetime.now(UTC),
        )
        await self.repo.insert_recording_assets([asset])
        conn = self._conn()
        _ = conn.execute(
            "UPDATE recording_assets SET started_at = ? WHERE id = ?",
            ("corrupted-timestamp", "a-bad-asset-dt"),
        )
        conn.commit()
        with self.assertRaises(ValueError):
            _ = await self.repo.get_recording_asset_by_role("m-bad-asset-dt", "other")


if __name__ == "__main__":
    _ = unittest.main()
