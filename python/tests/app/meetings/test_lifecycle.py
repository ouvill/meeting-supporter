# pyright: reportUninitializedInstanceVariable=false
"""Tests for MeetingLifecycleCoordinator — start/stop orchestration order."""

import asyncio
import tempfile
import unittest
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast, override

from app.core.protocols import ConversationState, TurnLike
from app.meetings.history_models import MeetingRecord, RecordingAsset
from app.meetings.lifecycle import MeetingLifecycleCoordinator
from app.meetings.models import MeetingSession
from app.meetings.repository import MeetingHistoryRepository
from app.meetings.service import MeetingHistoryService
from app.meetings.sqlite_repository import SqliteMeetingHistoryRepository


async def _cancel_replies() -> None:
    return None


def _reset_reply_cancel_results() -> None:
    return None


async def _reset_info_note_updater() -> None:
    return None


# ── Fakes ──────────────────────────────────────────────────────────────────────


@dataclass
class FakeConversationState(ConversationState):
    _turns: list[TurnLike] = field(default_factory=list)
    active_suggestion_target_id: str | None = None
    _is_running: bool = False
    context_text: str = ""
    _ai_note: str = ""
    current_session: object | None = None

    @property
    @override
    def turns(self) -> Sequence[TurnLike]:
        return tuple(self._turns)

    @property
    @override
    def is_running(self) -> bool:
        return self._is_running

    @property
    @override
    def ai_note(self) -> str:
        session = cast(MeetingSession | None, self.current_session)
        return session.ai_note if session else self._ai_note


class FakeWs:
    """Minimal WebSocket mock for start_meeting."""

    sent: list[dict[str, object]]

    def __init__(self) -> None:
        self.sent = []

    async def send_json(self, data: object) -> None:
        self.sent.append(cast(dict[str, object], data))


class RecordingSttController:
    """Records start/stop calls but doesn't touch state."""

    started: bool = False
    stopped: bool = False
    start_meeting_success: bool = True
    last_session_already_started: bool | None = None
    audio_other: object | None = None
    audio_self: object | None = None

    async def start_meeting(self, _ws: object, *, session_already_started: bool = False) -> bool:
        self.started = True
        self.last_session_already_started = session_already_started
        return self.start_meeting_success

    async def stop_meeting(self) -> None:
        self.stopped = True


# ── Recording fakes ────────────────────────────────────────────────────────────


class FakeRecordingService:
    """Records start/stop calls for recording lifecycle tests."""

    def __init__(self, *, events: list[str] | None = None, user_data_dir: Path | None = None) -> None:
        self.started: bool = False
        self.stopped: bool = False
        self.start_raise: bool = False
        self.stop_raise: bool = False
        self.asset_count: int = 0
        self.events: list[str] = events if events is not None else []
        self.user_data_dir: Path | None = user_data_dir
        self.recording_directory_is_file: bool = False
        self.meeting_ids: list[str] = []

    # Test stub: unused parameters are required by the interface.
    # Each is annotated with pyright: ignore[reportUnusedParameter].

    async def start_recording(
        self,
        meeting_id: str,
        audio_other: object | None = None,  # pyright: ignore[reportUnusedParameter]
        audio_self: object | None = None,  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        self.events.append("recording.start")
        self.meeting_ids.append(meeting_id)
        if self.start_raise:
            msg = "Simulated recording start failure"
            raise RuntimeError(msg)
        if self.user_data_dir is not None:
            recording_path = self.user_data_dir / "recordings" / meeting_id
            recording_path.parent.mkdir(parents=True, exist_ok=True)
            if self.recording_directory_is_file:
                _ = recording_path.write_bytes(b"not a directory")
            else:
                recording_path.mkdir()
        self.started = True

    async def stop_recording(
        self,
        meeting_id: str,
        audio_other: object | None = None,  # pyright: ignore[reportUnusedParameter]
        audio_self: object | None = None,  # pyright: ignore[reportUnusedParameter]
    ) -> list[RecordingAsset]:
        self.events.append("recording.stop")
        if self.stop_raise:
            msg = "Simulated recording stop failure"
            raise RuntimeError(msg)
        self.stopped = True

        return [
            RecordingAsset(
                id=f"asset-{role}-{meeting_id}",
                meeting_id=meeting_id,
                role=role,  # pyright: ignore[reportArgumentType]  # dynamic str in test, runtime-safe
                relative_path=f"recordings/{meeting_id}/{role}.wav",
                format="wav",
                sample_rate=16000,
                channels=1,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                size_bytes=100,
            )
            for role, _ in [("other", 0), ("self", 1)][: self.asset_count]
        ]


class RecordingRepositorySpy:
    """Traces lifecycle writes while optionally rejecting one asset batch."""

    def __init__(self, delegate: SqliteMeetingHistoryRepository, events: list[str]) -> None:
        self.delegate: SqliteMeetingHistoryRepository = delegate
        self.events: list[str] = events
        self.reject_asset_batch: bool = False

    async def create_meeting(self, record: MeetingRecord) -> None:
        self.events.append("draft.persist")
        await self.delegate.create_meeting(record)

    async def abort_meeting(self, meeting_id: str, ended_at: datetime) -> None:
        self.events.append("draft.abort")
        await self.delegate.abort_meeting(meeting_id, ended_at)

    async def complete_meeting(
        self, meeting_id: str, ended_at: datetime, duration_seconds: int | None = None, ai_note: str = ""
    ) -> None:
        self.events.append("meeting.complete")
        await self.delegate.complete_meeting(meeting_id, ended_at, duration_seconds, ai_note)

    async def insert_recording_assets(self, records: list[RecordingAsset]) -> None:
        self.events.append("assets.persist")
        if self.reject_asset_batch:
            raise OSError("simulated recording asset write failure")
        await self.delegate.insert_recording_assets(records)


class BlockingHistoryService:
    """History fake that exposes whether completion waits for flush."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.flush_started: asyncio.Event = asyncio.Event()
        self.release_flush: asyncio.Event = asyncio.Event()

    async def flush_pending(self, timeout: float = 5.0) -> None:
        _ = timeout
        self.events.append("flush_started")
        _ = self.flush_started.set()
        _ = await self.release_flush.wait()
        self.events.append("flush_finished")

    async def complete_meeting(self, session: MeetingSession) -> None:
        self.events.append(f"complete:{session.id}")


# ── Tests ──────────────────────────────────────────────────────────────────────


class MeetingLifecycleCoordinatorTest(unittest.IsolatedAsyncioTestCase):
    coordinator: MeetingLifecycleCoordinator
    state: FakeConversationState
    stt: RecordingSttController
    messages: list[dict[str, object]]
    repo: SqliteMeetingHistoryRepository
    history: MeetingHistoryService
    recording: FakeRecordingService

    @override
    async def asyncSetUp(self) -> None:
        self.state = FakeConversationState()
        self.stt = RecordingSttController()
        self.messages = []
        self.repo = SqliteMeetingHistoryRepository(":memory:")
        await self.repo.initialize()
        self.history = MeetingHistoryService(repository=self.repo)
        self.recording = FakeRecordingService()
        self.recording.asset_count = 2  # other + self

        async def broadcast(msg: object) -> None:
            self.messages.append(cast(dict[str, object], msg))

        # Duplicate fakes satisfy the expected protocols structurally.
        self.coordinator = MeetingLifecycleCoordinator(
            state=self.state,
            stt_controller=self.stt,  # pyright: ignore[reportArgumentType]
            broadcast=broadcast,
            history=self.history,
            cancel_replies=_cancel_replies,
            reset_reply_cancel_results=_reset_reply_cancel_results,
            reset_info_note_updater=_reset_info_note_updater,
            recording=self.recording,  # pyright: ignore[reportArgumentType]
        )

    @override
    async def asyncTearDown(self) -> None:
        await self.repo.close()

    async def test_start_and_stop_reset_info_in_reply_cancellation_order(self) -> None:
        cancel_calls = 0
        reset_calls = 0
        events: list[str] = []

        async def cancel_replies() -> None:
            nonlocal cancel_calls
            cancel_calls += 1
            events.append("reply.cancel")

        async def reset_info_note_updater() -> None:
            events.append("info.reset")

        original_stop_meeting = self.stt.stop_meeting

        async def stop_meeting() -> None:
            events.append("stt.stop")
            await original_stop_meeting()

        self.stt.stop_meeting = stop_meeting

        def reset_reply_cancel_results() -> None:
            nonlocal reset_calls
            reset_calls += 1

        async def broadcast(_: object) -> None:
            return None

        coordinator = MeetingLifecycleCoordinator(
            state=self.state,
            stt_controller=self.stt,  # pyright: ignore[reportArgumentType]
            broadcast=broadcast,
            history=self.history,
            cancel_replies=cancel_replies,
            reset_reply_cancel_results=reset_reply_cancel_results,
            reset_info_note_updater=reset_info_note_updater,
            recording=self.recording,  # pyright: ignore[reportArgumentType]
        )

        await coordinator.stop_meeting()
        self.assertEqual(2, cancel_calls)
        self.assertEqual(
            ["reply.cancel", "info.reset", "stt.stop", "reply.cancel", "info.reset"],
            events,
        )

        events.clear()
        await coordinator.start_meeting(FakeWs())
        self.assertEqual(1, reset_calls)
        self.assertEqual(["info.reset"], events)

    async def test_start_meeting_creates_session_and_draft(self) -> None:
        ws = FakeWs()
        await self.coordinator.start_meeting(ws)

        # Recording was started after draft
        self.assertTrue(self.recording.started)

        # STT was started with session_already_started=True
        self.assertTrue(self.stt.started)
        self.assertTrue(self.stt.last_session_already_started)

        # A session was created in state
        session = self.state.current_session
        assert isinstance(session, MeetingSession)
        self.assertIsNotNone(session.id)
        self.assertIsNotNone(session.started_at)
        self.assertTrue(session.is_active)

        # Draft was persisted
        fetched = await self.repo.get_meeting(session.id)
        assert fetched is not None
        self.assertEqual(fetched.status, "active")

        # SessionInfoMsg was broadcast
        info_msgs = [m for m in self.messages if getattr(m, "type", None) == "session_info"]
        self.assertGreaterEqual(len(info_msgs), 1)

    async def test_start_meeting_when_already_running_skips_new_draft(self) -> None:
        """When a meeting is already running, second start delegates to STT
        controller without creating a new session or draft."""
        ws = FakeWs()
        await self.coordinator.start_meeting(ws)
        session1 = self.state.current_session
        assert isinstance(session1, MeetingSession)
        session1_id = session1.id

        # Force state to indicate running (FakeConversationState uses _is_running)
        self.state._is_running = True  # pyright: ignore[reportPrivateUsage]  # test: set internal state directly

        self.recording.started = False  # reset for second call
        await self.coordinator.start_meeting(ws)
        session2 = self.state.current_session
        assert isinstance(session2, MeetingSession)

        # No new session was created — same session remains
        self.assertEqual(session1_id, session2.id)

        # Recording was NOT started again (no new draft/recording)
        self.assertFalse(self.recording.started)

    async def test_stop_meeting_completes_session_and_clears_state(self) -> None:
        ws = FakeWs()
        await self.coordinator.start_meeting(ws)
        session = self.state.current_session
        assert isinstance(session, MeetingSession)
        session_id = session.id

        await self.coordinator.stop_meeting()

        # STT was stopped first
        self.assertTrue(self.stt.stopped)

        # Recording was stopped, assets persisted
        self.assertTrue(self.recording.stopped)

        # Session was cleared from state (ended info preserved in broadcast)
        self.assertIsNone(self.state.current_session)

        # Meeting was completed in DB
        fetched = await self.repo.get_meeting(session_id)
        assert fetched is not None
        self.assertEqual(fetched.status, "completed")

        # Recording assets were inserted
        assets = await self.repo.list_recording_assets(session_id)
        self.assertEqual(len(assets), 2)

        # Broadcast contains final ended-session info
        session_info_msgs = [m for m in self.messages if getattr(m, "type", None) == "session_info"]
        self.assertGreaterEqual(len(session_info_msgs), 1)
        last_info = session_info_msgs[-1]
        self.assertFalse(getattr(last_info, "is_active"))
        self.assertIsNotNone(getattr(last_info, "ended_at", None))

    async def test_stop_meeting_waits_for_pending_history_before_completion(self) -> None:
        state = FakeConversationState(current_session=MeetingSession(id="session-flush", started_at=datetime.now(UTC)))
        stt = RecordingSttController()
        messages: list[dict[str, object]] = []
        history = BlockingHistoryService()

        async def broadcast(msg: object) -> None:
            messages.append(cast(dict[str, object], msg))

        coordinator = MeetingLifecycleCoordinator(
            state=state,
            stt_controller=stt,  # pyright: ignore[reportArgumentType]
            broadcast=broadcast,
            history=history,  # pyright: ignore[reportArgumentType]
            cancel_replies=_cancel_replies,
            reset_reply_cancel_results=_reset_reply_cancel_results,
            reset_info_note_updater=_reset_info_note_updater,
        )

        stop_task = asyncio.create_task(coordinator.stop_meeting())
        await asyncio.sleep(0)
        self.assertTrue(history.flush_started.is_set())

        self.assertTrue(stt.stopped)
        self.assertFalse(stop_task.done())
        self.assertEqual(["flush_started"], history.events)

        history.release_flush.set()
        await stop_task

        self.assertEqual(["flush_started", "flush_finished", "complete:session-flush"], history.events)
        self.assertIsNone(state.current_session)
        session_info_msgs = [m for m in messages if getattr(m, "type", None) == "session_info"]
        self.assertEqual(1, len(session_info_msgs))

    async def test_stop_meeting_without_start_does_not_crash(self) -> None:
        # No meeting started — STT stop is still called (no-op if already idle)
        await self.coordinator.stop_meeting()
        self.assertTrue(self.stt.stopped)
        self.assertIsNone(self.state.current_session)

    async def test_recording_start_failure_non_fatal(self) -> None:
        """Recording start failure should not prevent STT from starting."""
        self.recording.start_raise = True
        ws = FakeWs()
        await self.coordinator.start_meeting(ws)

        # Recording failed but STT still started
        self.assertTrue(self.stt.started)
        session = self.state.current_session
        assert isinstance(session, MeetingSession)
        self.assertIsNotNone(session.id)

    async def test_recording_stop_failure_without_compensation_keeps_draft_and_notifies(self) -> None:
        """A normal stop leaves the draft recoverable when untracked audio cannot be cleaned up."""
        self.recording.stop_raise = True
        ws = FakeWs()
        await self.coordinator.start_meeting(ws)
        session = self.state.current_session
        assert isinstance(session, MeetingSession)

        await self.coordinator.stop_meeting()

        fetched = await self.repo.get_meeting(session.id)
        assert fetched is not None
        self.assertEqual("active", fetched.status)
        self.assertIn(
            "録音の保存または削除に失敗しました。会議履歴から削除して再試行してください。",
            [getattr(message, "text", None) for message in self.messages],
        )


class MeetingLifecycleCoordinatorDraftFailureTest(unittest.IsolatedAsyncioTestCase):
    """Tests for draft persistence and STT start failure compensation."""

    coordinator: MeetingLifecycleCoordinator
    state: FakeConversationState
    stt: RecordingSttController
    messages: list[dict[str, object]]
    repo: SqliteMeetingHistoryRepository | None = None

    async def _make_coordinator(self, repo: SqliteMeetingHistoryRepository) -> None:
        self.state = FakeConversationState()
        self.stt = RecordingSttController()
        self.messages = []
        self.repo = repo

        async def broadcast(msg: object) -> None:
            self.messages.append(cast(dict[str, object], msg))

        # Duplicate fakes satisfy the expected protocols structurally.
        self.coordinator = MeetingLifecycleCoordinator(
            state=self.state,
            stt_controller=self.stt,  # pyright: ignore[reportArgumentType]
            broadcast=broadcast,
            history=MeetingHistoryService(repository=repo),
            cancel_replies=_cancel_replies,
            reset_reply_cancel_results=_reset_reply_cancel_results,
            reset_info_note_updater=_reset_info_note_updater,
        )

    @override
    async def asyncTearDown(self) -> None:
        if self.repo is not None:
            await self.repo.close()

    async def test_start_meeting_draft_failure_aborts_and_broadcasts_error(self) -> None:
        """When draft persistence fails, session is cleared and error broadcast.
        STT is never started because the coordinator creates the draft first."""
        # Closed repo forces draft failure
        repo = SqliteMeetingHistoryRepository(":memory:")
        await repo.initialize()
        await repo.close()
        await self._make_coordinator(repo)

        ws = FakeWs()
        await self.coordinator.start_meeting(ws)

        # STT was NOT started (draft failure happens before STT is called)
        self.assertFalse(self.stt.started)
        self.assertFalse(self.stt.stopped)

        # Session was cleared
        self.assertIsNone(self.state.current_session)

        # An error message was broadcast
        error_msgs = [m for m in self.messages if getattr(m, "type", None) == "error"]
        self.assertGreaterEqual(len(error_msgs), 1)

    async def test_start_meeting_stt_failure_after_draft_aborts_draft(self) -> None:
        """When STT start fails after draft creation, the draft is aborted,
        session is cleared, and errors are broadcast."""
        repo = SqliteMeetingHistoryRepository(":memory:")
        await repo.initialize()
        await self._make_coordinator(repo)

        self.stt.start_meeting_success = False
        ws = FakeWs()
        await self.coordinator.start_meeting(ws)

        # STT start_meeting was called (returned False)
        self.assertTrue(self.stt.started)

        # Session was cleared
        self.assertIsNone(self.state.current_session)

        # Error messages were broadcast
        error_msgs = [m for m in self.messages if getattr(m, "type", None) == "error"]
        self.assertGreaterEqual(len(error_msgs), 1)


class MeetingLifecycleCoordinatorRecordingFailureTest(unittest.IsolatedAsyncioTestCase):
    """Tests for recording + STT failure compensation in lifecycle."""

    coordinator: MeetingLifecycleCoordinator
    state: FakeConversationState
    stt: RecordingSttController
    messages: list[dict[str, object]]
    repo: SqliteMeetingHistoryRepository | None = None
    recording: FakeRecordingService

    async def _make_coordinator(self, repo: SqliteMeetingHistoryRepository) -> None:
        self.state = FakeConversationState()
        self.stt = RecordingSttController()
        self.messages = []
        self.repo = repo
        self.recording = FakeRecordingService()

        async def broadcast(msg: object) -> None:
            self.messages.append(cast(dict[str, object], msg))

        # Duplicate fakes satisfy the expected protocols structurally.
        self.coordinator = MeetingLifecycleCoordinator(
            state=self.state,
            stt_controller=self.stt,  # pyright: ignore[reportArgumentType]
            broadcast=broadcast,
            history=MeetingHistoryService(repository=repo),
            cancel_replies=_cancel_replies,
            reset_reply_cancel_results=_reset_reply_cancel_results,
            reset_info_note_updater=_reset_info_note_updater,
            recording=self.recording,  # pyright: ignore[reportArgumentType]
        )

    @override
    async def asyncTearDown(self) -> None:
        if self.repo is not None:
            await self.repo.close()

    async def test_stt_failure_with_recording_stop_exception_handled(self) -> None:
        """When both STT fails and recording stop raises, coordinator still cleans up."""
        repo = SqliteMeetingHistoryRepository(":memory:")
        await repo.initialize()
        await self._make_coordinator(repo)

        self.stt.start_meeting_success = False
        self.recording.started = True
        self.recording.stop_raise = True  # Recording stop also fails
        ws = FakeWs()
        # Should not raise — exceptions are logged
        await self.coordinator.start_meeting(ws)

        # Session was still cleared
        self.assertIsNone(self.state.current_session)

        # Error messages were broadcast
        error_msgs = [m for m in self.messages if getattr(m, "type", None) == "error"]
        self.assertGreaterEqual(len(error_msgs), 1)


class MeetingLifecycleRecordingIntegrityTest(unittest.IsolatedAsyncioTestCase):
    """Recording finalisation preserves a recoverable file/database state."""

    _temporary_directory: tempfile.TemporaryDirectory[str]
    user_data_dir: Path
    repo: SqliteMeetingHistoryRepository
    repository: RecordingRepositorySpy
    state: FakeConversationState
    stt: RecordingSttController
    recording: FakeRecordingService
    coordinator: MeetingLifecycleCoordinator
    events: list[str]
    messages: list[object]

    @override
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.user_data_dir = Path(self._temporary_directory.name)
        self.repo = SqliteMeetingHistoryRepository(":memory:")
        await self.repo.initialize()
        self.events = []
        self.repository = RecordingRepositorySpy(self.repo, self.events)
        self.state = FakeConversationState()
        self.stt = RecordingSttController()
        self.recording = FakeRecordingService(events=self.events, user_data_dir=self.user_data_dir)
        self.messages = []

        async def broadcast(message: object) -> None:
            self.messages.append(message)

        self.coordinator = MeetingLifecycleCoordinator(
            state=self.state,
            stt_controller=self.stt,  # pyright: ignore[reportArgumentType]
            broadcast=broadcast,
            history=MeetingHistoryService(repository=cast(MeetingHistoryRepository, cast(object, self.repository))),
            cancel_replies=_cancel_replies,
            reset_reply_cancel_results=_reset_reply_cancel_results,
            reset_info_note_updater=_reset_info_note_updater,
            recording=self.recording,  # pyright: ignore[reportArgumentType]
            user_data_dir=self.user_data_dir,
        )

    @override
    async def asyncTearDown(self) -> None:
        await self.repo.close()
        self._temporary_directory.cleanup()

    def _error_texts(self) -> list[str]:
        return [
            text
            for message in self.messages
            if getattr(message, "type", None) == "error"
            if isinstance(text := getattr(message, "text", None), str)
        ]

    async def test_stt_start_failure_persists_all_finalized_assets_before_aborting(self) -> None:
        """A failed STT start aborts only after the complete recording batch is durable."""
        self.stt.start_meeting_success = False
        self.recording.asset_count = 2

        await self.coordinator.start_meeting(FakeWs())

        meeting_id = self.recording.meeting_ids[0]
        meeting = await self.repo.get_meeting(meeting_id)
        assert meeting is not None
        self.assertEqual("aborted", meeting.status)
        self.assertEqual(["other", "self"], [asset.role for asset in await self.repo.list_recording_assets(meeting_id)])
        self.assertEqual(
            ["draft.persist", "recording.start", "recording.stop", "assets.persist", "draft.abort"],
            self.events,
        )
        self.assertIn("会議の開始に失敗しました（STTエラー）", self._error_texts())

    async def test_stt_start_failure_deletes_untracked_files_then_aborts(self) -> None:
        """When an abort cannot save assets, successful compensation removes files before aborting."""
        self.stt.start_meeting_success = False
        self.recording.asset_count = 1
        self.repository.reject_asset_batch = True

        await self.coordinator.start_meeting(FakeWs())

        meeting_id = self.recording.meeting_ids[0]
        meeting = await self.repo.get_meeting(meeting_id)
        assert meeting is not None
        self.assertEqual("aborted", meeting.status)
        self.assertFalse((self.user_data_dir / "recordings" / meeting_id).exists())
        self.assertEqual([], await self.repo.list_recording_assets(meeting_id))
        self.assertEqual(
            ["draft.persist", "recording.start", "recording.stop", "assets.persist", "draft.abort"],
            self.events,
        )
        self.assertIn("会議の開始に失敗しました（STTエラー）", self._error_texts())

    async def test_stt_start_failure_with_undeletable_path_keeps_draft_and_notifies(self) -> None:
        """An abort never marks a meeting aborted when failed asset writes cannot be compensated."""
        self.stt.start_meeting_success = False
        self.recording.asset_count = 1
        self.recording.recording_directory_is_file = True
        self.repository.reject_asset_batch = True

        await self.coordinator.start_meeting(FakeWs())

        meeting_id = self.recording.meeting_ids[0]
        meeting = await self.repo.get_meeting(meeting_id)
        assert meeting is not None
        self.assertEqual("active", meeting.status)
        self.assertTrue((self.user_data_dir / "recordings" / meeting_id).is_file())
        self.assertEqual([], await self.repo.list_recording_assets(meeting_id))
        self.assertEqual(["draft.persist", "recording.start", "recording.stop", "assets.persist"], self.events)
        self.assertIn(
            "録音の保存または削除に失敗しました。会議履歴から削除して再試行してください。",
            self._error_texts(),
        )
        self.assertIn("会議の開始に失敗しました（STTエラー）", self._error_texts())

    async def test_stop_asset_write_failure_deletes_files_and_notifies_before_completion(self) -> None:
        """A normal stop publishes completion only after failed asset persistence is compensated."""
        self.recording.asset_count = 1
        self.repository.reject_asset_batch = True

        await self.coordinator.start_meeting(FakeWs())
        meeting_id = self.recording.meeting_ids[0]
        recording_directory = self.user_data_dir / "recordings" / meeting_id
        self.assertTrue(recording_directory.is_dir())

        await self.coordinator.stop_meeting()

        meeting = await self.repo.get_meeting(meeting_id)
        assert meeting is not None
        self.assertEqual("completed", meeting.status)
        self.assertFalse(recording_directory.exists())
        self.assertEqual([], await self.repo.list_recording_assets(meeting_id))
        self.assertEqual(
            ["draft.persist", "recording.start", "recording.stop", "assets.persist", "meeting.complete"],
            self.events,
        )
        self.assertIn("録音を保存できなかったため、録音ファイルを削除しました。", self._error_texts())

    async def test_stop_asset_write_failure_with_undeletable_path_keeps_draft_and_notifies(self) -> None:
        """A failed compensation leaves the draft and untracked audio together for manual recovery."""
        self.recording.asset_count = 1
        self.recording.recording_directory_is_file = True
        self.repository.reject_asset_batch = True

        await self.coordinator.start_meeting(FakeWs())
        meeting_id = self.recording.meeting_ids[0]
        recording_path = self.user_data_dir / "recordings" / meeting_id
        self.assertTrue(recording_path.is_file())

        await self.coordinator.stop_meeting()

        meeting = await self.repo.get_meeting(meeting_id)
        assert meeting is not None
        self.assertEqual("active", meeting.status)
        self.assertTrue(recording_path.is_file())
        self.assertEqual([], await self.repo.list_recording_assets(meeting_id))
        self.assertEqual(["draft.persist", "recording.start", "recording.stop", "assets.persist"], self.events)
        self.assertIn(
            "録音の保存または削除に失敗しました。会議履歴から削除して再試行してください。",
            self._error_texts(),
        )


if __name__ == "__main__":
    _ = unittest.main()
