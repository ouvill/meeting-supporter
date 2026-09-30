# pyright: reportPrivateUsage=false, reportUninitializedInstanceVariable=false
import asyncio
import unittest
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast, override

from app.agents.models import ReplyAgentSpec, ReplyPrompt
from app.core.messages import OutgoingMessage
from app.core.protocols import TurnLike
from app.meetings.models import MeetingSession, ReplySuggestion, Turn
from app.services.reply_pipeline import ReplyPipeline


@dataclass
class FakeConversationState:
    current_session: object | None
    context_text: str = ""
    _turns: list[TurnLike] = field(default_factory=list)
    active_suggestion_target_id: str | None = None
    _is_running: bool = True
    _ai_note: str = ""

    @property
    def turns(self) -> Sequence[TurnLike]:
        session = cast(MeetingSession | None, self.current_session)
        return session.turns if session is not None else tuple(self._turns)

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def ai_note(self) -> str:
        session = cast(MeetingSession | None, self.current_session)
        return session.ai_note if session is not None else self._ai_note


class RecordingStream:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks: list[str] = chunks
        self.delta_flags: list[bool] = []

    async def __aenter__(self) -> "RecordingStream":
        return self

    async def __aexit__(self, *exc_info: object) -> bool | None:
        return None

    async def stream_text(self, *, delta: bool) -> AsyncIterator[str]:
        self.delta_flags.append(delta)
        for chunk in self._chunks:
            yield chunk


class FailsOnceReplyRuntime:
    def __init__(self) -> None:
        self.prompts: list[ReplyPrompt] = []
        self.calls: int = 0

    def run_stream(self, prompt: ReplyPrompt) -> RecordingStream:
        self.prompts.append(prompt)
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("model exploded")
        return RecordingStream(["再", "試行"])


class SafeFailureReplyRuntime:
    def run_stream(self, prompt: ReplyPrompt) -> RecordingStream:
        _ = prompt
        raise RuntimeError("synthetic-private-provider-detail")


class ControlledReplyStream(RecordingStream):
    def __init__(
        self,
        chunks: list[str],
        *,
        started: asyncio.Event,
        proceed: asyncio.Event,
    ) -> None:
        super().__init__(chunks)
        self._started: asyncio.Event = started
        self._proceed: asyncio.Event = proceed

    @override
    async def stream_text(self, *, delta: bool) -> AsyncIterator[str]:
        self.delta_flags.append(delta)
        self._started.set()
        _ = await self._proceed.wait()
        for chunk in self._chunks:
            yield chunk


class ControlledReplyRuntime:
    def __init__(self, *, started: asyncio.Event, proceed: asyncio.Event) -> None:
        self._started: asyncio.Event = started
        self._proceed: asyncio.Event = proceed

    def run_stream(self, prompt: ReplyPrompt) -> ControlledReplyStream:
        _ = prompt
        return ControlledReplyStream(
            ["境界を越えた返答案"],
            started=self._started,
            proceed=self._proceed,
        )


class RecordingReplyHistory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, ReplySuggestion]] = []

    def schedule_save_reply_suggestion(
        self,
        *,
        meeting_id: str,
        sequence: int,
        suggestion: ReplySuggestion,
    ) -> object:
        self.calls.append((meeting_id, sequence, suggestion))
        return object()


class Phase2UseCaseBoundaryTest(unittest.IsolatedAsyncioTestCase):
    messages: list[dict[str, object]]

    async def _record_message(self, msg: OutgoingMessage) -> None:
        self.messages.append(cast(dict[str, object], msg.model_dump()))

    @override
    def setUp(self) -> None:
        self.messages = []

    async def test_reply_pipeline_hides_provider_error_details(self) -> None:
        session = MeetingSession(
            id="reply-session",
            started_at=datetime.now(UTC),
            turns=(Turn(id="utt-1", speaker="other", text="返答してください"),),
        )
        pipeline = ReplyPipeline(
            state=FakeConversationState(current_session=session),
            broadcast=self._record_message,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_main",
                    label="標準",
                    runtime=SafeFailureReplyRuntime(),
                    priority=10,
                )
            ],
            turn_lock=asyncio.Lock(),
        )

        await pipeline.generate_reply(generation_id="generation-safe-error")
        records = list(pipeline._reply_tasks.values())
        _ = await asyncio.gather(*(record.task for record in records))

        self.assertTrue(
            any(
                message.get("type") == "suggestion_error" and message.get("text") == "返答案を作れませんでした"
                for message in self.messages
            )
        )

    async def test_reply_pipeline_preserves_agent_error_and_releases_target_for_retry(self) -> None:
        runtime = FailsOnceReplyRuntime()
        session = MeetingSession(
            id="reply-session",
            started_at=datetime.now(UTC),
            turns=(Turn(id="utt-1", speaker="other", text="再試行してください"),),
            ai_note="顧客は短い回答を希望",
        )
        state = FakeConversationState(current_session=session)
        pipeline = ReplyPipeline(
            state=state,
            broadcast=self._record_message,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_main",
                    label="標準",
                    runtime=runtime,
                    priority=10,
                )
            ],
            turn_lock=asyncio.Lock(),
        )

        await pipeline.generate_reply(mode="clarify", generation_id="generation-first")
        first_records = list(pipeline._reply_tasks.values())
        self.assertEqual(1, len(first_records))
        _ = await asyncio.gather(*(record.task for record in first_records))

        self.assertEqual("utt-1", state.active_suggestion_target_id)
        self.assertEqual({}, pipeline._reply_tasks)
        self.assertTrue(
            any(
                m.get("type") == "suggestion_error"
                and m.get("text") == "返答案を作れませんでした"
                and m.get("agent_id") == "reply_main"
                and m.get("target_utterance_id") == "utt-1"
                and m.get("mode") == "clarify"
                for m in self.messages
            )
        )
        self.assertFalse(any(m.get("text") == "model exploded" for m in self.messages))
        self.assertTrue(
            all(
                m.get("generation_id") == "generation-first"
                for m in self.messages
                if m.get("type") in {"suggestions_start", "reply_chunk", "suggestion_error"}
            )
        )

        await pipeline.generate_reply(
            target_turn_id="utt-1",
            mode="clarify",
            generation_id="generation-retry",
        )
        retry_records = list(pipeline._reply_tasks.values())
        self.assertEqual(1, len(retry_records))
        _ = await asyncio.gather(*(record.task for record in retry_records))

        starts = [m for m in self.messages if m.get("type") == "suggestions_start"]
        self.assertEqual(2, len(starts))
        self.assertEqual(
            ["generation-first", "generation-retry"],
            [m.get("generation_id") for m in starts],
        )
        updated = cast(MeetingSession | None, state.current_session)
        if updated is None:
            self.fail("current_session should exist")
        self.assertEqual(1, len(updated.reply_suggestions))
        self.assertEqual("再試行", updated.reply_suggestions[0].text)
        self.assertEqual("utt-1", updated.reply_suggestions[0].target_turn_id)
        self.assertEqual("clarify", updated.reply_suggestions[0].mode)

    async def test_reply_pipeline_cancel_before_commit_skips_state_history_and_final(self) -> None:
        started = asyncio.Event()
        proceed = asyncio.Event()
        state = FakeConversationState(
            current_session=MeetingSession(
                id="reply-session",
                started_at=datetime.now(UTC),
                turns=(Turn(id="utt-1", speaker="other", text="停止してください"),),
            )
        )
        history = RecordingReplyHistory()
        pipeline = ReplyPipeline(
            state=state,
            broadcast=self._record_message,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_main",
                    label="標準",
                    runtime=ControlledReplyRuntime(started=started, proceed=proceed),
                    priority=10,
                )
            ],
            turn_lock=asyncio.Lock(),
            history_service=history,  # pyright: ignore[reportArgumentType]
        )
        await pipeline.generate_reply(generation_id="generation-cancelled")
        record = next(iter(pipeline._reply_tasks.values()))
        _ = await started.wait()

        cancellations = await pipeline.cancel("generation-cancelled", "utt-1")

        self.assertEqual((record.suggestion_id,), cancellations[0].cancelled_suggestion_ids)
        updated = cast(MeetingSession, state.current_session)
        self.assertEqual((), updated.reply_suggestions)
        self.assertEqual([], history.calls)
        self.assertFalse(
            any(message.get("type") == "reply_chunk" and message.get("final") is True for message in self.messages)
        )

    async def test_reply_pipeline_cancel_after_commit_waits_for_history_and_final(self) -> None:
        started = asyncio.Event()
        proceed = asyncio.Event()
        final_started = asyncio.Event()
        final_proceed = asyncio.Event()

        async def block_final_broadcast(msg: OutgoingMessage) -> None:
            payload = cast(dict[str, object], msg.model_dump())
            self.messages.append(payload)
            if payload.get("type") == "reply_chunk" and payload.get("final") is True:
                final_started.set()
                _ = await final_proceed.wait()

        state = FakeConversationState(
            current_session=MeetingSession(
                id="reply-session",
                started_at=datetime.now(UTC),
                turns=(Turn(id="utt-1", speaker="other", text="完了してください"),),
            )
        )
        history = RecordingReplyHistory()
        pipeline = ReplyPipeline(
            state=state,
            broadcast=block_final_broadcast,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_main",
                    label="標準",
                    runtime=ControlledReplyRuntime(started=started, proceed=proceed),
                    priority=10,
                )
            ],
            turn_lock=asyncio.Lock(),
            history_service=history,  # pyright: ignore[reportArgumentType]
        )
        await pipeline.generate_reply(generation_id="generation-committed")
        record = next(iter(pipeline._reply_tasks.values()))
        _ = await started.wait()
        proceed.set()
        _ = await final_started.wait()
        cancel_task = asyncio.create_task(pipeline.cancel("generation-committed", "utt-1"))
        await asyncio.sleep(0)

        self.assertFalse(cancel_task.done())
        updated = cast(MeetingSession, state.current_session)
        self.assertEqual(1, len(updated.reply_suggestions))
        self.assertEqual(1, len(history.calls))
        self.assertEqual(updated.reply_suggestions[0], history.calls[0][2])

        final_proceed.set()
        _, cancellations = await asyncio.gather(record.task, cancel_task)

        self.assertEqual([], cancellations)
        self.assertTrue(record.task.done())
        self.assertTrue(
            any(
                message.get("type") == "reply_chunk"
                and message.get("generation_id") == "generation-committed"
                and message.get("final") is True
                for message in self.messages
            )
        )


if __name__ == "__main__":
    _ = unittest.main()
