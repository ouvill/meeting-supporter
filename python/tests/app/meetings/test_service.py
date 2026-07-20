# pyright: reportUninitializedInstanceVariable=false
"""Tests for MeetingHistoryService — runtime model ↔ persistence record mapping."""

import asyncio
import sqlite3
import unittest
from datetime import UTC, datetime, timedelta
from typing import override

from app.meetings.models import MeetingSession, ReplySuggestion, Turn
from app.meetings.service import MeetingHistoryService
from app.meetings.sqlite_repository import SqliteMeetingHistoryRepository


class _DelayedRepository:
    """Repository wrapper that lets tests hold specific writes in-flight."""

    def __init__(self, delegate: SqliteMeetingHistoryRepository) -> None:
        self._delegate: SqliteMeetingHistoryRepository = delegate
        self.delay_turn: bool = False
        self.delay_suggestion: bool = False
        self.turn_started: asyncio.Event = asyncio.Event()
        self.turn_continue: asyncio.Event = asyncio.Event()
        self.suggestion_started: asyncio.Event = asyncio.Event()
        self.suggestion_continue: asyncio.Event = asyncio.Event()

    async def create_meeting(self, record: object) -> None:
        await self._delegate.create_meeting(record)  # pyright: ignore[reportArgumentType]

    async def insert_turn(self, record: object) -> None:
        if self.delay_turn:
            _ = self.turn_started.set()
            _ = await self.turn_continue.wait()
        await self._delegate.insert_turn(record)  # pyright: ignore[reportArgumentType]

    async def insert_reply_suggestion(self, record: object) -> None:
        if self.delay_suggestion:
            _ = self.suggestion_started.set()
            _ = await self.suggestion_continue.wait()
        await self._delegate.insert_reply_suggestion(record)  # pyright: ignore[reportArgumentType]

    async def list_turns(self, meeting_id: str) -> object:
        return await self._delegate.list_turns(meeting_id)

    async def list_reply_suggestions(self, meeting_id: str) -> object:
        return await self._delegate.list_reply_suggestions(meeting_id)


class MeetingHistoryServiceTest(unittest.IsolatedAsyncioTestCase):
    repo: SqliteMeetingHistoryRepository
    service: MeetingHistoryService

    @override
    async def asyncSetUp(self) -> None:
        self.repo = SqliteMeetingHistoryRepository(":memory:")
        await self.repo.initialize()
        self.service = MeetingHistoryService(repository=self.repo)

    @override
    async def asyncTearDown(self) -> None:
        await self.repo.close()

    async def test_create_draft_meeting(self) -> None:
        session = MeetingSession(
            id="session-1",
            started_at=datetime.now(UTC),
            title="My Meeting",
        )
        await self.service.create_draft_meeting(session)

        fetched = await self.repo.get_meeting("session-1")
        assert fetched is not None
        self.assertEqual(fetched.id, "session-1")
        self.assertEqual(fetched.status, "active")
        self.assertEqual(fetched.title, "My Meeting")
        self.assertIsNotNone(fetched.created_at)
        self.assertIsNotNone(fetched.updated_at)

    async def test_create_draft_meeting_raises_on_failure(self) -> None:
        """Lifecycle methods raise so callers can compensate."""
        # Duplicate insert should raise IntegrityError.
        session = MeetingSession(
            id="dup-session",
            started_at=datetime.now(UTC),
        )
        await self.service.create_draft_meeting(session)
        with self.assertRaises(sqlite3.IntegrityError):
            await self.service.create_draft_meeting(session)

    async def test_complete_meeting(self) -> None:
        started = datetime.now(UTC)
        session = MeetingSession(id="session-2", started_at=started)
        await self.service.create_draft_meeting(session)

        ended = session.ended()
        await self.service.complete_meeting(ended)

        fetched = await self.repo.get_meeting("session-2")
        assert fetched is not None
        self.assertEqual(fetched.status, "completed")
        self.assertIsNotNone(fetched.ended_at)
        self.assertGreaterEqual(fetched.duration_seconds or 0, 0)

    async def test_complete_meeting_with_duration(self) -> None:
        started = datetime.now(UTC) - timedelta(minutes=10)
        session = MeetingSession(id="session-3", started_at=started, ai_note="Summary text")
        await self.service.create_draft_meeting(session)

        ended = session.ended()
        await self.service.complete_meeting(ended)

        fetched = await self.repo.get_meeting("session-3")
        assert fetched is not None
        self.assertEqual(fetched.status, "completed")
        self.assertIsNotNone(fetched.ended_at)
        assert fetched.duration_seconds is not None
        self.assertGreaterEqual(fetched.duration_seconds, 595)  # ~10 min
        self.assertEqual(fetched.ai_note, "Summary text")

    async def test_abort_meeting(self) -> None:
        session = MeetingSession(id="session-4", started_at=datetime.now(UTC))
        await self.service.create_draft_meeting(session)

        await self.service.abort_meeting("session-4")

        fetched = await self.repo.get_meeting("session-4")
        assert fetched is not None
        self.assertEqual(fetched.status, "aborted")

    async def test_insert_turn(self) -> None:
        session = MeetingSession(id="session-5", started_at=datetime.now(UTC))
        await self.service.create_draft_meeting(session)

        turn = Turn(id="turn-1", speaker="other", text="Hello")
        await self.service.insert_turn("session-5", 1, turn)

        turns = await self.repo.list_turns("session-5")
        self.assertEqual(1, len(turns))
        self.assertEqual("turn-1", turns[0].id)
        self.assertEqual(1, turns[0].sequence)
        self.assertEqual("other", turns[0].speaker)
        self.assertEqual("Hello", turns[0].text)

    async def test_save_reply_suggestion(self) -> None:
        session = MeetingSession(id="session-6", started_at=datetime.now(UTC))
        await self.service.create_draft_meeting(session)

        turn = Turn(id="turn-1", speaker="other", text="Hello")
        await self.service.insert_turn("session-6", 1, turn)

        suggestion = ReplySuggestion(
            id="sug-1",
            target_turn_id="turn-1",
            agent_id="agent_a",
            agent_label="Agent A",
            text="Reply text",
        )
        await self.service.save_reply_suggestion("session-6", 1, suggestion)

        suggestions = await self.repo.list_reply_suggestions("session-6")
        self.assertEqual(1, len(suggestions))
        self.assertEqual("sug-1", suggestions[0].id)
        self.assertEqual("turn-1", suggestions[0].target_turn_id)
        self.assertEqual("Agent A", suggestions[0].agent_label)
        self.assertEqual("Reply text", suggestions[0].text)

    async def test_realtime_persistence_failures_are_logged_and_do_not_raise(self) -> None:
        turn = Turn(id="orphan-turn", speaker="other", text="x")
        suggestion = ReplySuggestion(
            id="orphan-suggestion",
            target_turn_id=turn.id,
            agent_id="agent_a",
            agent_label="Agent A",
            text="Reply text",
        )

        with self.assertLogs("app.meetings.service", level="ERROR") as logs:
            await self.service.insert_turn("missing-meeting", 1, turn)
            await self.service.save_reply_suggestion("missing-meeting", 1, suggestion)

        output = "\n".join(logs.output)
        self.assertIn("orphan-turn", output)
        self.assertIn("orphan-suggestion", output)
        self.assertIn("missing-meeting", output)
        self.assertEqual(await self.repo.list_turns("missing-meeting"), [])
        self.assertEqual(await self.repo.list_reply_suggestions("missing-meeting"), [])

    async def test_scheduled_tasks_become_durable_when_awaited(self) -> None:
        session = MeetingSession(id="session-scheduled", started_at=datetime.now(UTC))
        await self.service.create_draft_meeting(session)
        turn = Turn(id="turn-scheduled", speaker="other", text="scheduled")
        suggestion = ReplySuggestion(
            id="suggestion-scheduled",
            target_turn_id=turn.id,
            agent_id="agent_a",
            agent_label="Agent A",
            text="scheduled reply",
        )

        self.assertIsNone(await self.service.schedule_insert_turn(session.id, 1, turn))
        self.assertIsNone(await self.service.schedule_save_reply_suggestion(session.id, 1, suggestion))

        turns = await self.repo.list_turns(session.id)
        suggestions = await self.repo.list_reply_suggestions(session.id)
        self.assertEqual([record.id for record in turns], [turn.id])
        self.assertEqual([record.id for record in suggestions], [suggestion.id])

    async def test_flush_pending_waits_for_delayed_scheduled_turn_and_suggestion_writes(self) -> None:
        """flush_pending does not return until scheduled real-time writes land."""
        delayed_repo = _DelayedRepository(self.repo)
        service = MeetingHistoryService(repository=delayed_repo)  # pyright: ignore[reportArgumentType]
        session = MeetingSession(id="session-flush", started_at=datetime.now(UTC))
        await service.create_draft_meeting(session)

        delayed_repo.delay_turn = True
        turn = Turn(id="turn-flush", speaker="other", text="delayed turn")
        turn_task = service.schedule_insert_turn(session.id, 1, turn)
        await asyncio.sleep(0)
        self.assertTrue(delayed_repo.turn_started.is_set())

        flush_task = asyncio.create_task(service.flush_pending(timeout=1.0))
        await asyncio.sleep(0)
        self.assertFalse(flush_task.done())

        delayed_repo.turn_continue.set()
        await flush_task
        await turn_task

        turns = await self.repo.list_turns(session.id)
        self.assertEqual(1, len(turns))
        self.assertEqual("turn-flush", turns[0].id)
        self.assertEqual("delayed turn", turns[0].text)

        delayed_repo.delay_suggestion = True
        suggestion = ReplySuggestion(
            id="suggestion-flush",
            target_turn_id="turn-flush",
            agent_id="agent_a",
            agent_label="Agent A",
            text="delayed suggestion",
        )
        suggestion_task = service.schedule_save_reply_suggestion(session.id, 1, suggestion)
        await asyncio.sleep(0)
        self.assertTrue(delayed_repo.suggestion_started.is_set())

        flush_task = asyncio.create_task(service.flush_pending(timeout=1.0))
        await asyncio.sleep(0)
        self.assertFalse(flush_task.done())

        delayed_repo.suggestion_continue.set()
        await flush_task
        await suggestion_task

        suggestions = await self.repo.list_reply_suggestions(session.id)
        self.assertEqual(1, len(suggestions))
        self.assertEqual("suggestion-flush", suggestions[0].id)
        self.assertEqual("delayed suggestion", suggestions[0].text)


if __name__ == "__main__":
    _ = unittest.main()
