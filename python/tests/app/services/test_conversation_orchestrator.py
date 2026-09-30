# pyright: reportUninitializedInstanceVariable=false, reportPrivateUsage=false
import asyncio
import unittest
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast, override

from app.agents.models import PydanticAIReplyAgentRuntime, ReplyAgentDefinition, ReplyAgentSpec
from app.core.config import AgentSettings, UsageBudgetConfig
from app.core.messages import OutgoingMessage
from app.core.protocols import TurnLike
from app.meetings.models import MeetingSession, Turn
from app.meetings.service import MeetingHistoryService
from app.services.conversation_orchestrator import ConversationOrchestrator
from app.services.usage_logger import UsageLogger


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0.01)


@dataclass
class FakeConversationState:
    _turns: list[TurnLike] = field(default_factory=list)
    active_suggestion_target_id: str | None = None
    _is_running: bool = True
    context_text: str = ""
    _ai_note: str = ""
    current_session: object | None = None

    @property
    def turns(self) -> Sequence[TurnLike]:
        session = cast(MeetingSession | None, self.current_session)
        return session.turns if session else tuple(self._turns)

    @property
    def is_running(self) -> bool:
        return self._is_running

    def set_running(self, is_running: bool) -> None:
        self._is_running = is_running

    @property
    def ai_note(self) -> str:
        session = cast(MeetingSession | None, self.current_session)
        return session.ai_note if session else self._ai_note


class FakeStream:
    def __init__(self, chunks: list[str], *, delay_seconds: float = 0) -> None:
        self._chunks: list[str] = chunks
        self._delay_seconds: float = delay_seconds

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        return None

    async def stream_text(self, *, delta: bool):  # pyright: ignore[reportUnusedParameter]
        for chunk in self._chunks:
            await asyncio.sleep(self._delay_seconds)
            yield chunk


class FakeAgent:
    def __init__(self, chunks: list[str], *, name: str, delay_seconds: float = 0) -> None:
        self._chunks: list[str] = chunks
        self._delay_seconds: float = delay_seconds
        self.name: str = name
        self.prompts: list[str] = []

    def run_stream(self, user_prompt: str) -> FakeStream:
        self.prompts.append(user_prompt)
        return FakeStream(self._chunks, delay_seconds=self._delay_seconds)


class WaitingFakeStream(FakeStream):
    def __init__(
        self,
        chunks: list[str],
        *,
        start_event: asyncio.Event,
        proceed_event: asyncio.Event,
        delay_seconds: float = 0,
    ) -> None:
        super().__init__(chunks, delay_seconds=delay_seconds)
        self._start_event: asyncio.Event = start_event
        self._proceed_event: asyncio.Event = proceed_event

    @override
    async def stream_text(self, *, delta: bool):
        _ = self._start_event.set()
        _ = await self._proceed_event.wait()
        async for chunk in super().stream_text(delta=delta):
            yield chunk


class WaitingFakeAgent(FakeAgent):
    def __init__(
        self,
        chunks: list[str],
        *,
        name: str,
        start_event: asyncio.Event,
        proceed_event: asyncio.Event,
        delay_seconds: float = 0,
    ) -> None:
        super().__init__(chunks, name=name, delay_seconds=delay_seconds)
        self._start_event: asyncio.Event = start_event
        self._proceed_event: asyncio.Event = proceed_event

    @override
    def run_stream(self, user_prompt: str) -> FakeStream:
        _ = user_prompt
        return WaitingFakeStream(
            self._chunks,
            start_event=self._start_event,
            proceed_event=self._proceed_event,
            delay_seconds=self._delay_seconds,
        )


class FailingAgent:
    def run_stream(self, user_prompt: str) -> FakeStream:
        _ = user_prompt
        raise RuntimeError("agent failed")


@dataclass
class FakeConfig:
    agent_settings: AgentSettings
    reply_agent_definitions: list[ReplyAgentDefinition]


class _RecordingHistoryService:
    def __init__(self) -> None:
        self.turn_calls: list[tuple[str, int, Turn]] = []

    def schedule_insert_turn(self, meeting_id: str, sequence: int, turn: Turn) -> object:
        self.turn_calls.append((meeting_id, sequence, turn))
        return object()


class ConversationOrchestratorTest(unittest.IsolatedAsyncioTestCase):
    messages: list[dict[str, object]]
    counter: int
    state: FakeConversationState
    orchestrator: ConversationOrchestrator
    main_agent: FakeAgent
    polite_agent: FakeAgent

    async def _record_message(self, msg: OutgoingMessage) -> None:
        self.messages.append(cast(dict[str, object], msg.model_dump()))

    def _make_turn(self, *, speaker: str, text: str, speaker_id: str | None = None) -> Turn:
        self.counter += 1
        return Turn(
            id=f"utt-{self.counter}",
            speaker=speaker,
            text=text,
            speaker_id=speaker_id,
        )

    @override
    def setUp(self) -> None:
        self.messages = []
        self.counter = 0

        self.state = FakeConversationState(
            current_session=MeetingSession(
                id="test-session",
                started_at=datetime.now(UTC),
            )
        )

        self.main_agent = FakeAgent(["候補", "です"], name="main")
        self.polite_agent = FakeAgent(["ご提案", "します"], name="polite")
        self.orchestrator = ConversationOrchestrator(
            state=self.state,
            broadcast=self._record_message,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_main",
                    label="標準",
                    runtime=PydanticAIReplyAgentRuntime(self.main_agent),
                    priority=10,
                ),
                ReplyAgentSpec(
                    id="reply_polite",
                    label="丁寧",
                    runtime=PydanticAIReplyAgentRuntime(self.polite_agent),
                    priority=20,
                ),
            ],
            turn_factory=self._make_turn,
        )

    async def test_cancel_replies_applies_before_commit_and_replays_cached_result(self) -> None:
        reply_started = asyncio.Event()
        reply_proceed = asyncio.Event()
        self.orchestrator = ConversationOrchestrator(
            state=self.state,
            broadcast=self._record_message,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_main",
                    label="標準",
                    runtime=PydanticAIReplyAgentRuntime(
                        WaitingFakeAgent(
                            ["停止される返答案"],
                            name="main",
                            start_event=reply_started,
                            proceed_event=reply_proceed,
                        )
                    ),
                    priority=10,
                )
            ],
            turn_factory=self._make_turn,
        )
        await self.orchestrator.handle_speech("other", "停止してください")
        await self.orchestrator.generate_reply(generation_id="generation-cancel")
        _ = await reply_started.wait()
        record = next(iter(self.orchestrator._reply_pipeline._reply_tasks.values()))

        first = await self.orchestrator.cancel_replies("generation-cancel", "utt-1")

        self.assertEqual("applied", first[0].status)
        self.assertEqual([record.suggestion_id], first[0].cancelled_suggestion_ids)
        session = cast(MeetingSession, self.state.current_session)
        self.assertEqual((), session.reply_suggestions)
        self.assertFalse(
            any(message.get("type") == "reply_chunk" and message.get("final") is True for message in self.messages)
        )

        second = await self.orchestrator.cancel_replies("generation-cancel", "utt-1")

        self.assertEqual(first, second)
        results = [message for message in self.messages if message.get("type") == "reply_cancel_result"]
        self.assertEqual(2, len(results))
        self.assertTrue(all(message.get("status") == "applied" for message in results))

        self.orchestrator.clear_reply_cancel_results()
        after_reset = await self.orchestrator.cancel_replies("generation-cancel", "utt-1")
        self.assertEqual("not_applied", after_reset[0].status)

    async def test_cancel_replies_does_not_apply_after_commit(self) -> None:
        await self.orchestrator.handle_speech("other", "完了させてください")
        await self.orchestrator.generate_reply(generation_id="generation-complete")
        records = list(self.orchestrator._reply_pipeline._reply_tasks.values())
        _ = await asyncio.gather(*(record.task for record in records))

        results = await self.orchestrator.cancel_replies("generation-complete", "utt-1")

        self.assertEqual("not_applied", results[0].status)
        self.assertEqual([], results[0].cancelled_suggestion_ids)
        session = cast(MeetingSession, self.state.current_session)
        self.assertEqual(2, len(session.reply_suggestions))
        self.assertTrue(
            any(message.get("type") == "reply_chunk" and message.get("final") is True for message in self.messages)
        )

    async def test_cancel_replies_reports_only_pending_agent_in_partial_generation(self) -> None:
        waiting_started = asyncio.Event()
        waiting_proceed = asyncio.Event()
        self.orchestrator = ConversationOrchestrator(
            state=self.state,
            broadcast=self._record_message,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_fast",
                    label="即時",
                    runtime=PydanticAIReplyAgentRuntime(FakeAgent(["保存済み"], name="fast")),
                    priority=10,
                ),
                ReplyAgentSpec(
                    id="reply_waiting",
                    label="待機",
                    runtime=PydanticAIReplyAgentRuntime(
                        WaitingFakeAgent(
                            ["停止対象"],
                            name="waiting",
                            start_event=waiting_started,
                            proceed_event=waiting_proceed,
                        )
                    ),
                    priority=20,
                ),
            ],
            turn_factory=self._make_turn,
        )
        await self.orchestrator.handle_speech("other", "一部だけ停止してください")
        await self.orchestrator.generate_reply(generation_id="generation-partial")
        records_by_agent = {
            key.agent_id: record for key, record in self.orchestrator._reply_pipeline._reply_tasks.items()
        }
        fast_record = records_by_agent["reply_fast"]
        waiting_record = records_by_agent["reply_waiting"]
        await fast_record.task
        _ = await waiting_started.wait()

        results = await self.orchestrator.cancel_replies("generation-partial", "utt-1")

        self.assertTrue(fast_record.task.done())
        self.assertEqual("applied", results[0].status)
        self.assertEqual([waiting_record.suggestion_id], results[0].cancelled_suggestion_ids)
        session = cast(MeetingSession, self.state.current_session)
        self.assertEqual(1, len(session.reply_suggestions))
        self.assertEqual("reply_fast", session.reply_suggestions[0].agent_id)
        final_agents = {
            message.get("agent_id")
            for message in self.messages
            if message.get("type") == "reply_chunk" and message.get("final") is True
        }
        self.assertEqual({"reply_fast"}, final_agents)

    async def test_same_target_generations_cancel_independently_and_allow_retry(self) -> None:
        reply_started = asyncio.Event()
        reply_proceed = asyncio.Event()
        self.orchestrator = ConversationOrchestrator(
            state=self.state,
            broadcast=self._record_message,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_main",
                    label="標準",
                    runtime=PydanticAIReplyAgentRuntime(
                        WaitingFakeAgent(
                            ["再試行成功"],
                            name="main",
                            start_event=reply_started,
                            proceed_event=reply_proceed,
                        )
                    ),
                    priority=10,
                )
            ],
            turn_factory=self._make_turn,
        )
        await self.orchestrator.handle_speech("other", "同じ発言で再試行します")
        await self.orchestrator.generate_reply(generation_id="generation-1")
        _ = await reply_started.wait()

        wrong_generation = await self.orchestrator.cancel_replies("generation-2", "utt-1")
        self.assertEqual("not_applied", wrong_generation[0].status)
        self.assertEqual([], wrong_generation[0].cancelled_suggestion_ids)
        self.assertTrue(
            any(key.generation_id == "generation-1" for key in self.orchestrator._reply_pipeline._reply_tasks)
        )

        first_cancel = await self.orchestrator.cancel_replies("generation-1", "utt-1")
        self.assertEqual("applied", first_cancel[0].status)
        reply_proceed.set()
        await self.orchestrator.generate_reply(generation_id="generation-2")
        retry_records = list(self.orchestrator._reply_pipeline._reply_tasks.values())
        _ = await asyncio.gather(*(record.task for record in retry_records))

        session = cast(MeetingSession, self.state.current_session)
        self.assertEqual(1, len(session.reply_suggestions))
        self.assertEqual("再試行成功", session.reply_suggestions[0].text)
        final_generations = [
            message.get("generation_id")
            for message in self.messages
            if message.get("type") == "reply_chunk" and message.get("final") is True
        ]
        self.assertEqual(["generation-2"], final_generations)

    async def test_cancel_replies_without_identity_cancels_all_generations(self) -> None:
        reply_started = asyncio.Event()
        reply_proceed = asyncio.Event()
        self.orchestrator = ConversationOrchestrator(
            state=self.state,
            broadcast=self._record_message,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_main",
                    label="標準",
                    runtime=PydanticAIReplyAgentRuntime(
                        WaitingFakeAgent(
                            ["保存されない返答案"],
                            name="main",
                            start_event=reply_started,
                            proceed_event=reply_proceed,
                        )
                    ),
                    priority=10,
                )
            ],
            turn_factory=self._make_turn,
        )
        await self.orchestrator.handle_speech("other", "全生成を停止してください")
        await self.orchestrator.generate_reply(generation_id="generation-1")
        await self.orchestrator.generate_reply(generation_id="generation-2")
        _ = await reply_started.wait()
        await asyncio.sleep(0)

        results = await self.orchestrator.cancel_replies()

        self.assertEqual(
            {"generation-1", "generation-2"},
            {result.generation_id for result in results},
        )
        self.assertTrue(all(result.status == "applied" for result in results))
        self.assertTrue(all(len(result.cancelled_suggestion_ids) == 1 for result in results))
        session = cast(MeetingSession, self.state.current_session)
        self.assertEqual((), session.reply_suggestions)
        self.assertFalse(
            any(message.get("type") == "reply_chunk" and message.get("final") is True for message in self.messages)
        )

    async def test_apply_agent_settings_cancels_active_generation_before_disabling_reply(self) -> None:
        reply_started = asyncio.Event()
        reply_proceed = asyncio.Event()
        self.orchestrator = ConversationOrchestrator(
            state=self.state,
            broadcast=self._record_message,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_main",
                    label="標準",
                    runtime=PydanticAIReplyAgentRuntime(
                        WaitingFakeAgent(
                            ["無効化前の返答案"],
                            name="main",
                            start_event=reply_started,
                            proceed_event=reply_proceed,
                        )
                    ),
                    priority=10,
                )
            ],
            turn_factory=self._make_turn,
        )
        await self.orchestrator.handle_speech("other", "返答案を無効化します")
        await self.orchestrator.generate_reply(generation_id="generation-disable")
        _ = await reply_started.wait()

        await self.orchestrator.apply_agent_settings(reply_agents=[])

        self.assertEqual({}, self.orchestrator._reply_pipeline._reply_tasks)
        session = cast(MeetingSession, self.state.current_session)
        self.assertEqual((), session.reply_suggestions)
        self.assertTrue(
            any(
                message.get("type") == "reply_cancel_result"
                and message.get("generation_id") == "generation-disable"
                and message.get("status") == "applied"
                for message in self.messages
            )
        )

    async def test_apply_agent_settings_cancels_old_runtime_before_replacement_generates(self) -> None:
        old_started = asyncio.Event()
        old_proceed = asyncio.Event()
        self.orchestrator = ConversationOrchestrator(
            state=self.state,
            broadcast=self._record_message,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_old",
                    label="旧経路",
                    runtime=PydanticAIReplyAgentRuntime(
                        WaitingFakeAgent(
                            ["保存されない旧返答案"],
                            name="old",
                            start_event=old_started,
                            proceed_event=old_proceed,
                        )
                    ),
                    priority=10,
                )
            ],
            turn_factory=self._make_turn,
        )
        await self.orchestrator.handle_speech("other", "経路を切り替えてください")
        await self.orchestrator.generate_reply(generation_id="generation-old")
        _ = await old_started.wait()

        await self.orchestrator.apply_agent_settings(
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_new",
                    label="新経路",
                    runtime=PydanticAIReplyAgentRuntime(FakeAgent(["新経路の返答案"], name="new")),
                    priority=10,
                )
            ],
        )
        await self.orchestrator.generate_reply(generation_id="generation-new")
        replacement_records = list(self.orchestrator._reply_pipeline._reply_tasks.values())
        _ = await asyncio.gather(*(record.task for record in replacement_records))

        session = cast(MeetingSession, self.state.current_session)
        self.assertEqual(1, len(session.reply_suggestions))
        self.assertEqual("reply_new", session.reply_suggestions[0].agent_id)
        self.assertEqual("新経路の返答案", session.reply_suggestions[0].text)
        self.assertTrue(
            any(
                message.get("type") == "reply_cancel_result"
                and message.get("generation_id") == "generation-old"
                and message.get("status") == "applied"
                for message in self.messages
            )
        )
        final_generations = [
            message.get("generation_id")
            for message in self.messages
            if message.get("type") == "reply_chunk" and message.get("final") is True
        ]
        self.assertEqual(["generation-new"], final_generations)

    async def test_reply_cancel_result_cache_keeps_only_thirty_most_recent_requests(self) -> None:
        for index in range(31):
            _ = await self.orchestrator.cancel_replies(f"generation-{index}", "utt-missing")

        self.assertEqual(30, len(self.orchestrator._reply_cancel_results))
        self.assertNotIn(("generation-0", "utt-missing"), self.orchestrator._reply_cancel_results)
        self.assertIn(("generation-30", "utt-missing"), self.orchestrator._reply_cancel_results)

    async def test_handle_speech_does_not_auto_generate_reply_by_default(self) -> None:
        await self.orchestrator.handle_speech("other", "こんにちは")

        await asyncio.sleep(0)

        self.assertTrue(any(m.get("type") == "stt_final" for m in self.messages))
        self.assertFalse(any(m.get("type") == "suggestions_start" for m in self.messages))
        self.assertEqual([], self.main_agent.prompts)
        self.assertEqual([], self.polite_agent.prompts)

    async def test_generate_reply_uses_latest_other_turn(self) -> None:
        await self.orchestrator.handle_speech("other", "こんにちは")
        await self.orchestrator.handle_speech("self", "ありがとうございます")
        await self.orchestrator.generate_reply(generation_id="generation-1")
        await _wait_until(
            lambda: len([m for m in self.messages if m.get("type") == "reply_chunk" and m.get("final") is True]) == 2
        )

        stt_final_messages = [m for m in self.messages if m.get("type") == "stt_final"]
        self.assertEqual(2, len(stt_final_messages))
        self.assertEqual("utt-1", stt_final_messages[0].get("utterance_id"))
        self.assertEqual("utt-2", stt_final_messages[1].get("utterance_id"))

        suggestion_start_messages = [m for m in self.messages if m.get("type") == "suggestions_start"]
        self.assertEqual(2, len(suggestion_start_messages))
        start_targets = {m.get("target_utterance_id") for m in suggestion_start_messages}
        self.assertEqual({"utt-1"}, start_targets)
        start_agent_ids = {m.get("agent_id") for m in suggestion_start_messages}
        self.assertEqual({"reply_main", "reply_polite"}, start_agent_ids)
        priorities_by_agent = {m.get("agent_id"): m.get("agent_priority") for m in suggestion_start_messages}
        self.assertEqual(10, priorities_by_agent["reply_main"])
        self.assertEqual(20, priorities_by_agent["reply_polite"])

        reply_messages = [m for m in self.messages if m.get("type") == "reply_chunk"]
        self.assertTrue(
            any(m.get("target_utterance_id") == "utt-1" and m.get("target_role") == "other" for m in reply_messages)
        )
        self.assertTrue(any(m.get("agent_id") == "reply_main" for m in reply_messages))
        self.assertTrue(any(m.get("agent_id") == "reply_polite" for m in reply_messages))
        session = cast(MeetingSession | None, self.state.current_session)
        if session is None:
            self.fail("current_session should exist")
        self.assertEqual(2, len(session.turns))
        suggestions = session.reply_suggestions
        reply_main_suggestions = [s for s in suggestions if s.agent_id == "reply_main"]
        reply_polite_suggestions = [s for s in suggestions if s.agent_id == "reply_polite"]
        self.assertEqual(1, len(reply_main_suggestions))
        self.assertEqual(1, len(reply_polite_suggestions))
        self.assertEqual({"utt-1"}, {s.target_turn_id for s in suggestions})
        self.assertEqual("候補です", reply_main_suggestions[0].text)
        self.assertEqual("ご提案します", reply_polite_suggestions[0].text)
        self.assertEqual("utt-1", reply_main_suggestions[0].target_turn_id)
        self.assertEqual("utt-1", reply_polite_suggestions[0].target_turn_id)

    async def test_handle_speech_auto_generates_reply_when_enabled(self) -> None:
        agent = FakeAgent(["候補"], name="main")
        self.orchestrator = ConversationOrchestrator(
            state=self.state,
            broadcast=self._record_message,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_main",
                    label="標準",
                    runtime=PydanticAIReplyAgentRuntime(agent),
                    priority=10,
                )
            ],
            turn_factory=self._make_turn,
            agent_settings={
                "reply_enabled": True,
                "reply_auto_generate": True,
            },
        )

        await self.orchestrator.handle_speech("other", "自動生成してください")
        await _wait_until(lambda: any(m.get("type") == "reply_chunk" and m.get("final") is True for m in self.messages))

        suggestion_start_messages = [m for m in self.messages if m.get("type") == "suggestions_start"]
        self.assertEqual(1, len(suggestion_start_messages))
        self.assertEqual("utt-1", suggestion_start_messages[0].get("target_utterance_id"))

    async def test_handle_speech_auto_generation_only_runs_for_other_turns(self) -> None:
        agent = FakeAgent(["候補"], name="main")
        self.orchestrator = ConversationOrchestrator(
            state=self.state,
            broadcast=self._record_message,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_main",
                    label="標準",
                    runtime=PydanticAIReplyAgentRuntime(agent),
                    priority=10,
                )
            ],
            turn_factory=self._make_turn,
            agent_settings={
                "reply_enabled": True,
                "reply_auto_generate": True,
            },
        )

        await self.orchestrator.handle_speech("self", "自分の発話です")
        await asyncio.sleep(0)

        self.assertFalse(any(m.get("type") == "suggestions_start" for m in self.messages))
        self.assertEqual([], agent.prompts)

    async def test_generate_reply_emits_error_when_meeting_is_not_started(self) -> None:
        self.state.set_running(False)

        await self.orchestrator.generate_reply(generation_id="generation-1")

        self.assertTrue(
            any(m.get("type") == "error" and m.get("text") == "会議が開始されていません" for m in self.messages)
        )

    async def test_generate_reply_emits_error_when_no_turn_exists(self) -> None:
        await self.orchestrator.generate_reply(generation_id="generation-1")

        self.assertTrue(
            any(m.get("type") == "error" and m.get("text") == "返答案を作れる発言がありません" for m in self.messages)
        )

    async def test_generate_reply_emits_error_when_reply_is_disabled(self) -> None:
        await self.orchestrator.apply_agent_settings(reply_agents=[])

        await self.orchestrator.generate_reply(generation_id="generation-1")

        self.assertTrue(
            any(m.get("type") == "error" and m.get("text") == "返答案は現在オフです" for m in self.messages)
        )

    async def test_generate_reply_emits_error_for_missing_target_turn(self) -> None:
        await self.orchestrator.handle_speech("other", "こんにちは")

        await self.orchestrator.generate_reply(target_turn_id="missing-turn", generation_id="generation-1")

        self.assertTrue(
            any(m.get("type") == "error" and m.get("text") == "返答案を作れる発言がありません" for m in self.messages)
        )

    async def test_generate_reply_deduplicates_consecutive_requests_for_same_turn_and_agent(self) -> None:
        first_request_started = asyncio.Event()
        first_request_proceed = asyncio.Event()

        self.orchestrator = ConversationOrchestrator(
            state=self.state,
            broadcast=self._record_message,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_main",
                    label="標準",
                    runtime=PydanticAIReplyAgentRuntime(
                        WaitingFakeAgent(
                            ["候補"],
                            name="main",
                            start_event=first_request_started,
                            proceed_event=first_request_proceed,
                        )
                    ),
                    priority=10,
                )
            ],
            turn_factory=self._make_turn,
        )
        await self.orchestrator.handle_speech("other", "重複しないで")

        first_request = asyncio.create_task(self.orchestrator.generate_reply(generation_id="generation-1"))
        _ = await first_request_started.wait()

        second_request = asyncio.create_task(self.orchestrator.generate_reply(generation_id="generation-1"))
        first_request_proceed.set()

        _ = await asyncio.gather(first_request, second_request)

        suggestion_start_messages = [m for m in self.messages if m.get("type") == "suggestions_start"]
        self.assertEqual(1, len(suggestion_start_messages))
        self.assertEqual("reply_main", suggestion_start_messages[0].get("agent_id"))
        self.assertEqual("utt-1", suggestion_start_messages[0].get("target_utterance_id"))

    async def test_emits_suggestion_error_when_one_agent_fails(self) -> None:
        messages: list[dict[str, object]] = []

        async def broadcast(msg: OutgoingMessage) -> None:
            messages.append(cast(dict[str, object], msg.model_dump()))

        def _turn_factory(*, speaker: str, text: str, speaker_id: str | None = None) -> Turn:
            return Turn(
                id="utt-x",
                speaker=speaker,
                text=text,
                speaker_id=speaker_id,
            )

        self.orchestrator = ConversationOrchestrator(
            state=self.state,
            broadcast=broadcast,
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_main",
                    label="標準",
                    runtime=PydanticAIReplyAgentRuntime(FakeAgent(["候補", "です"], name="main")),
                    priority=10,
                ),
                ReplyAgentSpec(
                    id="reply_polite",
                    label="丁寧",
                    runtime=PydanticAIReplyAgentRuntime(FailingAgent()),
                    priority=20,
                ),
            ],
            turn_factory=_turn_factory,
        )

        await self.orchestrator.handle_speech("other", "こんにちは")
        await self.orchestrator.generate_reply(generation_id="generation-1")
        await _wait_until(
            lambda: (
                any(m.get("type") == "suggestion_error" for m in messages)
                and any(m.get("type") == "reply_chunk" and m.get("final") is True for m in messages)
            )
        )

        self.assertTrue(any(m.get("type") == "suggestion_error" for m in messages))
        self.assertTrue(
            any(m.get("type") == "suggestion_error" and m.get("agent_id") == "reply_polite" for m in messages)
        )
        self.assertTrue(
            any(
                m.get("type") == "reply_chunk" and m.get("agent_id") == "reply_main" and m.get("final") is True
                for m in messages
            )
        )

    async def test_set_reply_agents_applies_to_next_speech(self) -> None:
        await self.orchestrator.apply_agent_settings(
            reply_agents=[
                ReplyAgentSpec(
                    id="reply_polite",
                    label="丁寧",
                    runtime=PydanticAIReplyAgentRuntime(FakeAgent(["ご提案", "します"], name="polite")),
                    priority=20,
                )
            ],
        )

        await self.orchestrator.handle_speech("other", "進め方を教えてください")
        await self.orchestrator.generate_reply(generation_id="generation-1")
        await _wait_until(
            lambda: (
                isinstance(self.state.current_session, MeetingSession)
                and any(
                    suggestion.agent_id == "reply_polite" for suggestion in self.state.current_session.reply_suggestions
                )
            )
        )

        suggestion_start_messages = [m for m in self.messages if m.get("type") == "suggestions_start"]
        self.assertEqual(1, len(suggestion_start_messages))
        self.assertEqual("reply_polite", suggestion_start_messages[0].get("agent_id"))

        session = cast(MeetingSession | None, self.state.current_session)
        if session is None:
            self.fail("current_session should exist")
        suggestions = session.reply_suggestions
        polite_suggestions = [s for s in suggestions if s.agent_id == "reply_polite"]
        main_suggestions = [s for s in suggestions if s.agent_id == "reply_main"]
        self.assertEqual(1, len(polite_suggestions))
        self.assertEqual(0, len(main_suggestions))
        self.assertEqual("ご提案します", polite_suggestions[0].text)

    async def test_apply_agent_settings_can_disable_reply(self) -> None:
        await self.orchestrator.apply_agent_settings(reply_agents=[])

        await self.orchestrator.handle_speech("other", "確認したいです")
        await asyncio.sleep(0)

        self.assertFalse(any(m.get("type") == "suggestions_start" for m in self.messages))
        self.assertEqual([], self.main_agent.prompts)
        self.assertEqual([], self.polite_agent.prompts)

    async def test_budget_limit_blocks_reply_generation(self) -> None:
        with TemporaryDirectory() as td:
            usage_logger = UsageLogger(Path(td) / "usage.jsonl")
            usage_logger.log(
                agent_id="reply_main",
                model="gemini/gemini-3.1-flash-lite",
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                elapsed_s=0.1,
                meeting_id="test-session",
            )
            self.orchestrator = ConversationOrchestrator(
                state=self.state,
                broadcast=self._record_message,
                reply_agents=[
                    ReplyAgentSpec(
                        id="reply_main",
                        label="標準",
                        runtime=PydanticAIReplyAgentRuntime(FakeAgent(["候補"], name="main")),
                        priority=10,
                    )
                ],
                turn_factory=self._make_turn,
                usage_logger=usage_logger,
                usage_budget=UsageBudgetConfig(meeting_limit_jpy=0.01, monthly_limit_jpy=0),
            )

            await self.orchestrator.handle_speech("other", "予算超過です")
            await self.orchestrator.generate_reply(generation_id="generation-1")
            await _wait_until(lambda: any(m.get("type") == "suggestion_error" for m in self.messages))

            self.assertFalse(any(m.get("type") == "suggestions_start" for m in self.messages))
            self.assertTrue(any(m.get("type") == "suggestion_error" for m in self.messages))

    async def test_update_agents_enables_new_custom_reply_agent_after_config_filter(self) -> None:
        await self.orchestrator.update_agents(
            reply_agent_specs=[
                ReplyAgentSpec(
                    id="reply_custom",
                    label="Custom",
                    runtime=PydanticAIReplyAgentRuntime(FakeAgent(["custom"], name="custom")),
                    priority=5,
                )
            ],
        )
        await self.orchestrator.on_config_changed(
            FakeConfig(
                agent_settings={
                    "reply_enabled": True,
                    "reply_auto_generate": False,
                },
                reply_agent_definitions=[
                    ReplyAgentDefinition(
                        id="reply_custom",
                        label="Custom",
                        enabled=True,
                        priority=5,
                        instruction="Custom",
                    )
                ],
            )
        )

        await self.orchestrator.handle_speech("other", "カスタム提案をください")
        await self.orchestrator.generate_reply(generation_id="generation-1")
        await _wait_until(lambda: any(m.get("type") == "reply_chunk" and m.get("final") is True for m in self.messages))

        suggestion_start_messages = [m for m in self.messages if m.get("type") == "suggestions_start"]
        self.assertEqual(1, len(suggestion_start_messages))
        self.assertEqual("reply_custom", suggestion_start_messages[0].get("agent_id"))

    async def test_handle_user_reply_appends_self_turn_and_broadcasts(self) -> None:
        await self.orchestrator.handle_user_reply("ユーザー発言")

        stt_final_messages = [m for m in self.messages if m.get("type") == "stt_final"]
        self.assertEqual(1, len(stt_final_messages))
        self.assertEqual("self", stt_final_messages[0].get("role"))
        self.assertEqual("ユーザー発言", stt_final_messages[0].get("text"))
        self.assertIsNone(stt_final_messages[0].get("speaker_id"))

        session = cast(MeetingSession | None, self.state.current_session)
        if session is None:
            self.fail("current_session should exist")
        self.assertEqual(1, len(session.turns))
        self.assertEqual("self", session.turns[0].speaker)
        self.assertEqual("ユーザー発言", session.turns[0].text)
        # user_reply should not mark itself as the active suggestion target.
        self.assertIsNone(self.state.active_suggestion_target_id)

    async def test_handle_user_reply_does_not_start_reply_suggestions(self) -> None:
        await self.orchestrator.handle_user_reply("返答不要")
        await asyncio.sleep(0)

        self.assertTrue(any(m.get("type") == "stt_final" for m in self.messages))
        self.assertFalse(any(m.get("type") == "suggestions_start" for m in self.messages))
        self.assertEqual([], self.main_agent.prompts)
        self.assertEqual([], self.polite_agent.prompts)

    async def test_handle_user_reply_persists_with_correct_sequence(self) -> None:
        history = _RecordingHistoryService()

        async def broadcast(msg: OutgoingMessage) -> None:
            self.messages.append(cast(dict[str, object], msg.model_dump()))

        def turn_factory(*, speaker: str, text: str, speaker_id: str | None = None) -> Turn:
            self.counter += 1
            return Turn(
                id=f"utt-{self.counter}",
                speaker=speaker,
                text=text,
                speaker_id=speaker_id,
            )

        self.orchestrator = ConversationOrchestrator(
            state=self.state,
            broadcast=broadcast,
            reply_agents=[],
            turn_factory=turn_factory,
            history_service=cast(MeetingHistoryService, cast(object, history)),
        )

        await self.orchestrator.handle_user_reply("保存テスト")

        self.assertEqual(1, len(history.turn_calls))
        meeting_id, sequence, turn = history.turn_calls[0]
        self.assertEqual("test-session", meeting_id)
        self.assertEqual(0, sequence)
        self.assertEqual("self", turn.speaker)
        self.assertEqual("保存テスト", turn.text)

    async def test_handle_user_reply_no_op_when_no_session(self) -> None:
        self.state.current_session = None

        await self.orchestrator.handle_user_reply("無視される")

        self.assertFalse(any(m.get("type") == "stt_final" for m in self.messages))

    async def test_concurrent_handle_speech_and_user_reply_keep_all_turns(self) -> None:
        history = _RecordingHistoryService()

        async def broadcast(msg: OutgoingMessage) -> None:
            self.messages.append(cast(dict[str, object], msg.model_dump()))

        def turn_factory(*, speaker: str, text: str, speaker_id: str | None = None) -> Turn:
            self.counter += 1
            return Turn(
                id=f"utt-{self.counter}",
                speaker=speaker,
                text=text,
                speaker_id=speaker_id,
            )

        self.orchestrator = ConversationOrchestrator(
            state=self.state,
            broadcast=broadcast,
            reply_agents=[],
            turn_factory=turn_factory,
            history_service=cast(MeetingHistoryService, cast(object, history)),
        )

        speech_tasks = [self.orchestrator.handle_speech("other", f"o{i}") for i in range(20)]
        reply_tasks = [self.orchestrator.handle_user_reply(f"s{i}") for i in range(20)]
        _ = await asyncio.gather(*speech_tasks, *reply_tasks)

        session = cast(MeetingSession | None, self.state.current_session)
        if session is None:
            self.fail("current_session should exist")
        self.assertEqual(40, len(session.turns))

        stt_final_messages = [m for m in self.messages if m.get("type") == "stt_final"]
        self.assertEqual(40, len(stt_final_messages))

        self.assertEqual(40, len(history.turn_calls))
        sequences = sorted(call[1] for call in history.turn_calls)
        self.assertEqual(list(range(40)), sequences)
        self.assertEqual(40, len({call[2].id for call in history.turn_calls}))


if __name__ == "__main__":
    _ = unittest.main()
