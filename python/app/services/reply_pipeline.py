import asyncio
import logging
from dataclasses import dataclass
from typing import NamedTuple, cast
from uuid import uuid4

from app.agents.codex_models import CodexSafeError
from app.agents.models import ReplyAgentSpec, ReplyPrompt
from app.agents.prompts import build_reply_prompt
from app.core.config import UsageBudgetConfig
from app.core.messages import (
    ErrorMsg,
    OutgoingBroadcastFn,
    ReplyChunkMsg,
    SuggestionErrorMsg,
    SuggestionMode,
    SuggestionsStartMsg,
)
from app.core.protocols import ConversationState, TurnLike
from app.meetings.models import MeetingSession, ReplySuggestion, _new_utterance_id, history_texts
from app.meetings.service import MeetingHistoryService
from app.services.usage_logger import UsageBudget, UsageLogger

logger = logging.getLogger(__name__)


class ReplyGenerationKey(NamedTuple):
    generation_id: str
    target_turn_id: str
    agent_id: str


class ReplyCancellation(NamedTuple):
    generation_id: str
    target_turn_id: str
    cancelled_suggestion_ids: tuple[str, ...]


@dataclass(slots=True)
class ReplyTaskRecord:
    suggestion_id: str
    task: asyncio.Task[None]
    cancellable: bool


class ReplyPipeline:
    """Live Reply use case: target selection, prompt construction, streaming, and persistence."""

    def __init__(
        self,
        *,
        state: ConversationState,
        broadcast: OutgoingBroadcastFn,
        reply_agents: list[ReplyAgentSpec],
        turn_lock: asyncio.Lock,
        history_service: MeetingHistoryService | None = None,
        usage_logger: UsageLogger | None = None,
        usage_budget: UsageBudgetConfig | None = None,
    ) -> None:
        self._state: ConversationState = state
        self._broadcast: OutgoingBroadcastFn = broadcast
        self._reply_agents: list[ReplyAgentSpec] = list(reply_agents)
        self._turn_lock: asyncio.Lock = turn_lock
        self._history_service: MeetingHistoryService | None = history_service
        self._usage_logger: UsageLogger | None = usage_logger
        self._usage_budget: UsageBudgetConfig = usage_budget or UsageBudgetConfig()
        self._reply_tasks: dict[ReplyGenerationKey, ReplyTaskRecord] = {}

    def apply_agents(self, reply_agents: list[ReplyAgentSpec]) -> None:
        self._reply_agents = list(reply_agents)

    def update_budget(self, usage_budget: UsageBudgetConfig) -> None:
        self._usage_budget = usage_budget

    async def cancel(
        self,
        generation_id: str | None = None,
        target_turn_id: str | None = None,
    ) -> list[ReplyCancellation]:
        if (generation_id is None) != (target_turn_id is None):
            raise ValueError("generation_id and target_turn_id must be provided together")

        matched = [
            (key, record)
            for key, record in self._reply_tasks.items()
            if generation_id is None or (key.generation_id == generation_id and key.target_turn_id == target_turn_id)
        ]
        cancelled: dict[tuple[str, str], list[str]] = {}
        for key, record in matched:
            if record.task.done() or not record.cancellable:
                continue
            record.cancellable = False
            cancelled.setdefault((key.generation_id, key.target_turn_id), []).append(record.suggestion_id)
            _ = record.task.cancel()

        pending = [record.task for _, record in matched if not record.task.done()]
        if pending:
            _ = await asyncio.gather(*pending, return_exceptions=True)

        return [
            ReplyCancellation(
                generation_id=key[0],
                target_turn_id=key[1],
                cancelled_suggestion_ids=tuple(suggestion_ids),
            )
            for key, suggestion_ids in cancelled.items()
        ]

    def start_for_turn(
        self,
        *,
        target_turn_id: str,
        target_turn_idx: int,
        target_role: str,
        mode: SuggestionMode = "normal",
        generation_id: str | None = None,
    ) -> str | None:
        if not self._reply_agents:
            return None
        resolved_generation_id = generation_id or str(uuid4())
        for reply_agent_spec in sorted(self._reply_agents, key=lambda spec: spec.priority):
            generation_key = ReplyGenerationKey(
                generation_id=resolved_generation_id,
                target_turn_id=target_turn_id,
                agent_id=reply_agent_spec.id,
            )
            existing = self._reply_tasks.get(generation_key)
            if existing is not None and not existing.task.done():
                continue
            suggestion_id = _new_utterance_id()
            task = asyncio.create_task(
                self._generate_reply(
                    generation_key=generation_key,
                    suggestion_id=suggestion_id,
                    reply_agent_spec=reply_agent_spec,
                    target_turn_idx=target_turn_idx,
                    target_role=target_role,
                    mode=mode,
                )
            )
            self._reply_tasks[generation_key] = ReplyTaskRecord(
                suggestion_id=suggestion_id,
                task=task,
                cancellable=True,
            )
            task.add_done_callback(lambda done_task, key=generation_key: self._remove_task(key, done_task))

        return resolved_generation_id

    async def generate_reply(
        self,
        target_turn_id: str | None = None,
        mode: SuggestionMode = "normal",
        *,
        generation_id: str,
    ) -> None:
        """Generate reply suggestions from the current conversation on explicit user request."""
        if not self._state.is_running or self._state.current_session is None:
            await self._broadcast(ErrorMsg(text="会議が開始されていません"))
            return

        if not self._reply_agents:
            await self._broadcast(ErrorMsg(text="返答案は現在オフです"))
            return

        target = self._find_reply_target(target_turn_id)
        if target is None:
            await self._broadcast(ErrorMsg(text="返答案を作れる発言がありません"))
            return

        turn, turn_idx = target
        self._state.active_suggestion_target_id = turn.id
        _ = self.start_for_turn(
            target_turn_id=turn.id,
            target_turn_idx=turn_idx,
            target_role=turn.speaker,
            mode=mode,
            generation_id=generation_id,
        )

    def _find_reply_target(self, target_turn_id: str | None) -> tuple[TurnLike, int] | None:
        turns = list(self._state.turns)
        if target_turn_id is not None:
            for idx, turn in enumerate(turns):
                if turn.id == target_turn_id:
                    return turn, idx
            return None

        for idx in range(len(turns) - 1, -1, -1):
            turn = turns[idx]
            if turn.speaker == "other":
                return turn, idx
        if turns:
            return turns[-1], len(turns) - 1
        return None

    def _remove_task(self, key: ReplyGenerationKey, task: asyncio.Task[None]) -> None:
        current = self._reply_tasks.get(key)
        if current is not None and current.task is task:
            _ = self._reply_tasks.pop(key)

    def _mark_terminal(self, key: ReplyGenerationKey) -> bool:
        record = self._reply_tasks.get(key)
        current_task = asyncio.current_task()
        if record is None or record.task is not current_task or not record.cancellable:
            return False
        record.cancellable = False
        return True

    def _budget_blocks_generation(self) -> bool:
        if self._usage_logger is None:
            return False
        session = cast(MeetingSession | None, self._state.current_session)
        meeting_id = session.id if session is not None else None
        return self._usage_logger.is_budget_exceeded(
            UsageBudget(
                meeting_limit_jpy=self._usage_budget.meeting_limit_jpy,
                monthly_limit_jpy=self._usage_budget.monthly_limit_jpy,
            ),
            meeting_id=meeting_id,
        )

    async def _generate_reply(
        self,
        *,
        generation_key: ReplyGenerationKey,
        suggestion_id: str,
        reply_agent_spec: ReplyAgentSpec,
        target_turn_idx: int,
        target_role: str,
        mode: SuggestionMode,
    ) -> None:
        generation_id = generation_key.generation_id
        target_turn_id = generation_key.target_turn_id
        agent_id = reply_agent_spec.id
        agent_label = reply_agent_spec.label
        agent_priority = reply_agent_spec.priority
        if self._budget_blocks_generation():
            if not self._mark_terminal(generation_key):
                return
            await self._broadcast(
                SuggestionErrorMsg(
                    text="AI利用量の予算上限に達したため、返答生成を停止しました",
                    agent_id=agent_id,
                    agent_label=agent_label,
                    agent_priority=agent_priority,
                    generation_id=generation_id,
                    suggestion_id=suggestion_id,
                    target_utterance_id=target_turn_id,
                    target_role=target_role,
                    mode=mode,
                )
            )
            return

        await self._broadcast(
            SuggestionsStartMsg(
                agent_id=agent_id,
                agent_label=agent_label,
                agent_priority=agent_priority,
                generation_id=generation_id,
                suggestion_id=suggestion_id,
                target_utterance_id=target_turn_id,
                target_role=target_role,
                mode=mode,
            )
        )
        session_snapshot = cast(MeetingSession | None, self._state.current_session)
        prompt = build_reply_prompt(
            history=history_texts(self._state.turns[: target_turn_idx + 1]),
            ai_note=self._state.ai_note,
            meeting_context=session_snapshot.meeting_context if session_snapshot is not None else None,
            references=list(session_snapshot.references) if session_snapshot is not None else [],
            mode=mode,
        )
        parts: list[str] = []
        try:
            async with reply_agent_spec.runtime.run_stream(ReplyPrompt(text=prompt)) as stream:
                async for delta in stream.stream_text(delta=True):
                    parts.append(delta)
                    await self._broadcast(
                        ReplyChunkMsg(
                            text=delta,
                            final=False,
                            agent_id=agent_id,
                            agent_label=agent_label,
                            agent_priority=agent_priority,
                            generation_id=generation_id,
                            suggestion_id=suggestion_id,
                            target_utterance_id=target_turn_id,
                            target_role=target_role,
                            mode=mode,
                        )
                    )

            result = "".join(parts)
            async with self._turn_lock:
                session = cast(MeetingSession | None, self._state.current_session)
                if session is not None:
                    suggestion = ReplySuggestion(
                        id=suggestion_id,
                        target_turn_id=target_turn_id,
                        agent_id=agent_id,
                        agent_label=agent_label,
                        text=result,
                        mode=mode,
                    )
                    self._state.current_session = session.with_reply_suggestion(suggestion)
                    if self._history_service is not None:
                        existing_for_target = [
                            item for item in session.reply_suggestions if item.target_turn_id == target_turn_id
                        ]
                        _ = self._history_service.schedule_save_reply_suggestion(
                            meeting_id=session.id,
                            sequence=len(existing_for_target),
                            suggestion=suggestion,
                        )
                if not self._mark_terminal(generation_key):
                    return

            await self._broadcast(
                ReplyChunkMsg(
                    text="",
                    final=True,
                    agent_id=agent_id,
                    agent_label=agent_label,
                    agent_priority=agent_priority,
                    generation_id=generation_id,
                    suggestion_id=suggestion_id,
                    target_utterance_id=target_turn_id,
                    target_role=target_role,
                    mode=mode,
                )
            )
        except Exception as exc:
            if not self._mark_terminal(generation_key):
                return
            safe_error = exc if isinstance(exc, CodexSafeError) else None
            logger.warning(
                "Reply generation failed agent_id=%s error_type=%s error_code=%s",
                agent_id,
                type(exc).__name__,
                safe_error.code if safe_error is not None else None,
            )
            await self._broadcast(
                SuggestionErrorMsg(
                    text=safe_error.message if safe_error is not None else "返答案を作れませんでした",
                    agent_id=agent_id,
                    agent_label=agent_label,
                    agent_priority=agent_priority,
                    generation_id=generation_id,
                    suggestion_id=suggestion_id,
                    target_utterance_id=target_turn_id,
                    target_role=target_role,
                    mode=mode,
                )
            )


__all__ = ["ReplyCancellation", "ReplyGenerationKey", "ReplyPipeline", "ReplyTaskRecord"]
