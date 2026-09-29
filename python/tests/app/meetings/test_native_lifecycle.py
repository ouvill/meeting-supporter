# pyright: reportUninitializedInstanceVariable=false
"""Real Rust lifecycle + SQLx workers, synthetic effects, existing Python services."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from typing import final, override

from app.core.messages import OutgoingMessage
from app.meetings.models import MeetingSession, Turn
from app.meetings.native_lifecycle import NativeMeetingLifecycleCoordinator
from app.meetings.native_repository import NativeMeetingHistoryRepository
from app.meetings.service import MeetingHistoryService
from tests.app.meetings.test_lifecycle import (
    FakeConversationState,
    FakeRecordingService,
    FakeWs,
    RecordingSttController,
)


@final
class FinalSpeechController(RecordingSttController):
    def __init__(self, state: FakeConversationState, history: MeetingHistoryService) -> None:
        self.meeting_state = state
        self.history = history
        self.block_start = False
        self.start_entered = asyncio.Event()
        self.release_start = asyncio.Event()
        self.shutdown_called = False

    @override
    async def start_meeting(self, _ws: object, *, session_already_started: bool = False) -> bool:
        self.start_entered.set()
        if self.block_start:
            _ = await self.release_start.wait()
        return await super().start_meeting(_ws, session_already_started=session_already_started)

    @override
    async def stop_meeting(self, *, require_complete: bool = False, publish_state: bool = True) -> None:
        _ = require_complete, publish_state
        await super().stop_meeting()
        session = self.meeting_state.current_session
        if isinstance(session, MeetingSession):
            _ = self.history.schedule_insert_turn(
                session.id,
                0,
                Turn(id="final-" + session.id, speaker="self", text="Synthetic final utterance"),
            )

    async def shutdown_stt(self) -> None:
        self.shutdown_called = True


@unittest.skipUnless(
    os.environ.get("MEETING_SESSION_WORKER") and os.environ.get("MEETING_STORAGE_WORKER"),
    "Build and select Rust meeting-session and meeting-storage workers",
)
@final
class NativeLifecycleTest(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.repository = NativeMeetingHistoryRepository(self.directory / "history.sqlite3")
        await self.repository.initialize()
        self.history = MeetingHistoryService(self.repository)
        self.state = FakeConversationState()
        self.stt = FinalSpeechController(self.state, self.history)
        self.recording = FakeRecordingService()
        self.recording.user_data_dir = self.directory
        self.recording.asset_count = 1
        self.messages: list[OutgoingMessage] = []
        self.cancel_count = 0

        async def broadcast(message: OutgoingMessage) -> None:
            self.messages.append(message)

        async def cancel() -> None:
            self.cancel_count += 1

        async def reset() -> None:
            pass

        self.coordinator = NativeMeetingLifecycleCoordinator(
            state=self.state,
            stt_controller=self.stt,  # pyright: ignore[reportArgumentType] # Synthetic device adapter.
            broadcast=broadcast,
            history=self.history,
            cancel_replies=cancel,
            reset_reply_cancel_results=lambda: None,
            reset_info_note_updater=reset,
            recording=self.recording,  # pyright: ignore[reportArgumentType] # Synthetic WAV adapter.
            user_data_dir=self.directory,
        )

    @override
    async def asyncTearDown(self) -> None:
        await self.coordinator.close()
        await self.history.flush_pending()
        await self.repository.close()
        self.temporary.cleanup()

    async def test_concurrent_start_stop_flush_recording_and_restart(self) -> None:
        _ = await asyncio.gather(self.coordinator.start_meeting(FakeWs()), self.coordinator.start_meeting(FakeWs()))
        assert isinstance(self.state.current_session, MeetingSession)
        first_id = self.state.current_session.id
        self.assertEqual(await self.repository.count_meetings(), 1)
        _ = await asyncio.gather(self.coordinator.stop_meeting(), self.coordinator.stop_meeting())
        self.assertIsNone(self.state.current_session)
        meeting = await self.repository.get_meeting(first_id)
        assert meeting is not None
        self.assertEqual(meeting.status, "completed")
        self.assertEqual((await self.repository.list_turns(first_id))[0].text, "Synthetic final utterance")
        self.assertEqual(len(await self.repository.list_recording_assets(first_id)), 1)
        self.assertEqual(self.cancel_count, 2)
        await self.coordinator.start_meeting(FakeWs())
        assert isinstance(self.state.current_session, MeetingSession)
        self.assertNotEqual(first_id, self.state.current_session.id)
        await self.coordinator.stop_meeting()
        self.assertEqual(await self.repository.count_meetings(), 2)

    async def test_start_failure_aborts_and_stops_partial_speech(self) -> None:
        self.stt.start_meeting_success = False
        await self.coordinator.start_meeting(FakeWs())
        self.assertIsNone(self.state.current_session)
        self.assertTrue(self.stt.stopped)
        meetings = await self.repository.list_meetings()
        self.assertEqual(meetings[0].status, "aborted")
        self.assertEqual(len(await self.repository.list_recording_assets(meetings[0].id)), 1)

    async def test_recording_compensation_failure_preserves_draft(self) -> None:
        self.recording.recording_directory_is_file = True
        self.recording.stop_raise = True
        await self.coordinator.start_meeting(FakeWs())
        await self.coordinator.stop_meeting()
        meetings = await self.repository.list_meetings()
        self.assertEqual(meetings[0].status, "active")
        self.assertTrue((self.directory / "recordings" / meetings[0].id).is_file())

    async def test_cancelled_start_stops_resources_and_refuses_restart(self) -> None:
        self.stt.block_start = True
        task = asyncio.create_task(self.coordinator.start_meeting(FakeWs()))
        _ = await self.stt.start_entered.wait()
        _ = task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.stt.shutdown_called)
        self.assertIsNone(self.state.current_session)
        await self.coordinator.start_meeting(FakeWs())
        self.assertEqual(await self.repository.count_meetings(), 1)
        self.assertEqual((await self.repository.list_meetings())[0].status, "active")

    async def test_failed_turn_write_prevents_completion(self) -> None:
        await self.coordinator.start_meeting(FakeWs())
        assert isinstance(self.state.current_session, MeetingSession)
        session_id = self.state.current_session.id
        # A duplicate turn is a real SQLx constraint failure for this meeting.
        await self.history.insert_turn(session_id, 0, Turn(id="existing", speaker="self", text="Synthetic"))
        await self.coordinator.stop_meeting()
        meeting = await self.repository.get_meeting(session_id)
        assert meeting is not None
        self.assertEqual(meeting.status, "active")

    async def test_dead_owner_does_not_restart_or_complete_unknown_session(self) -> None:
        await self.coordinator.start_meeting(FakeWs())
        worker = self.coordinator._session_worker()  # pyright: ignore[reportPrivateUsage] # Fault injection.
        process = worker._process  # pyright: ignore[reportPrivateUsage] # Kill the actual child.
        assert process is not None
        process.kill()
        _ = await process.wait()
        await self.coordinator.stop_meeting()
        self.assertTrue(self.stt.shutdown_called)
        self.assertIsNone(self.state.current_session)
        await self.coordinator.start_meeting(FakeWs())
        self.assertEqual(await self.repository.count_meetings(), 1)
        self.assertEqual((await self.repository.list_meetings())[0].status, "active")
