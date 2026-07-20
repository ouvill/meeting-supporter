import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from app.agents.models import InfoAgentRuntime, ReplyAgentDefinition, ReplyAgentSpec
from app.core.config import AgentSettings, UsageBudgetConfig
from app.core.messages import (
    AgentSettingsMsg,
    OutgoingBroadcastFn,
    ReplyAgentSettingsItem,
    ReplyCancelResultMsg,
    SttFinalMsg,
    SuggestionMode,
)
from app.core.protocols import ConversationState
from app.meetings.models import MeetingSession, Turn
from app.meetings.service import MeetingHistoryService
from app.services.info_note_updater import InfoNoteUpdater
from app.services.reply_pipeline import ReplyPipeline
from app.services.usage_logger import UsageLogger


class AgentRuntimeConfig(Protocol):
    """Config slice needed by ConversationOrchestrator."""

    agent_settings: AgentSettings
    reply_agent_definitions: list[ReplyAgentDefinition]


class ConversationOrchestrator:
    """Meeting/STT/session coordinator and websocket-facing facade for Live Reply use cases."""

    def __init__(
        self,
        state: ConversationState,
        broadcast: OutgoingBroadcastFn,
        reply_agents: list[ReplyAgentSpec],
        info_runtime: InfoAgentRuntime | None,
        turn_factory: Callable[..., Turn],
        info_readiness: Callable[[], Awaitable[bool]] | None = None,
        info_enabled: bool = True,
        agent_settings: AgentSettings | None = None,
        history_service: MeetingHistoryService | None = None,
        usage_logger: UsageLogger | None = None,
        usage_budget: UsageBudgetConfig | None = None,
    ) -> None:
        self._state: ConversationState = state
        self._broadcast: OutgoingBroadcastFn = broadcast
        self._all_reply_agents: list[ReplyAgentSpec] = list(reply_agents)
        self._turn_factory: Callable[..., Turn] = turn_factory
        self._history_service: MeetingHistoryService | None = history_service
        self._reply_auto_generate: bool = agent_settings["reply_auto_generate"] if agent_settings is not None else False
        self._agent_settings: AgentSettings | None = agent_settings
        self._turn_lock: asyncio.Lock = asyncio.Lock()
        active_reply_agents = (
            self._filter_reply_agents(agent_settings) if agent_settings is not None else list(reply_agents)
        )
        self._reply_pipeline: ReplyPipeline = ReplyPipeline(
            state=state,
            broadcast=broadcast,
            reply_agents=active_reply_agents,
            turn_lock=self._turn_lock,
            history_service=history_service,
            usage_logger=usage_logger,
            usage_budget=usage_budget,
        )
        self._info_note_updater: InfoNoteUpdater = InfoNoteUpdater(
            state=state,
            broadcast=broadcast,
            info_runtime=info_runtime,
            turn_lock=self._turn_lock,
            info_enabled=info_enabled,
            info_readiness=info_readiness,
            usage_logger=usage_logger,
            usage_budget=usage_budget,
        )
        self._reply_cancel_results: OrderedDict[tuple[str, str], ReplyCancelResultMsg] = OrderedDict()

    def _filter_reply_agents(self, settings: AgentSettings | None) -> list[ReplyAgentSpec]:
        if settings is not None and not settings["reply_enabled"]:
            return []
        # Individual reply-style enabled flags are applied when
        # ReplyAgentDefinition is converted into runtime ReplyAgentSpec objects.
        # This layer only applies the global reply feature switch.
        return list(self._all_reply_agents)

    async def on_config_changed(self, new_config: AgentRuntimeConfig) -> None:
        """Apply agent settings from updated config. Each service owns its own slice."""
        settings = new_config.agent_settings
        self._agent_settings = settings
        active = self._filter_reply_agents(settings)
        await self.apply_agent_settings(
            reply_agents=active,
            info_enabled=settings["info_enabled"],
            reply_auto_generate=settings["reply_auto_generate"],
        )
        usage_budget = getattr(new_config, "usage_budget", None)
        if isinstance(usage_budget, UsageBudgetConfig):
            self._reply_pipeline.update_budget(usage_budget)
            self._info_note_updater.update_budget(usage_budget)
        await self._broadcast(
            AgentSettingsMsg(
                reply_enabled=settings["reply_enabled"],
                reply_auto_generate=settings["reply_auto_generate"],
                reply_agents=[
                    ReplyAgentSettingsItem(
                        id=d.id,
                        label=d.label,
                        enabled=d.enabled,
                        priority=d.priority,
                    )
                    for d in new_config.reply_agent_definitions
                ],
                info_enabled=settings["info_enabled"],
            )
        )

    async def update_agents(
        self,
        *,
        info_runtime: InfoAgentRuntime | None,
        reply_agent_specs: list[ReplyAgentSpec],
    ) -> None:
        """Atomically stop in-flight generation before replacing its runtimes."""
        await self._info_note_updater.update_runtime(info_runtime)
        _ = await self.cancel_replies()
        self._all_reply_agents = list(reply_agent_specs)
        self._reply_pipeline.apply_agents(self._filter_reply_agents(self._agent_settings))

    async def apply_agent_settings(
        self,
        *,
        reply_agents: list[ReplyAgentSpec],
        info_enabled: bool,
        reply_auto_generate: bool | None = None,
    ) -> None:
        _ = await self.cancel_replies()
        self._reply_pipeline.apply_agents(reply_agents)
        if reply_auto_generate is not None:
            self._reply_auto_generate = reply_auto_generate
        await self._info_note_updater.apply_enabled(info_enabled)

    async def replace_ai_note(self, old_str: str, new_str: str) -> str:
        return await self._info_note_updater.replace_ai_note(old_str, new_str)

    async def generate_reply(
        self,
        target_turn_id: str | None = None,
        mode: SuggestionMode = "normal",
        *,
        generation_id: str,
    ) -> None:
        """Generate reply suggestions from the current conversation on explicit user request."""
        await self._reply_pipeline.generate_reply(
            target_turn_id=target_turn_id,
            mode=mode,
            generation_id=generation_id,
        )

    async def cancel_replies(
        self,
        generation_id: str | None = None,
        target_utterance_id: str | None = None,
    ) -> list[ReplyCancelResultMsg]:
        if (generation_id is None) != (target_utterance_id is None):
            raise ValueError("generation_id and target_utterance_id must be provided together")

        if generation_id is not None and target_utterance_id is not None:
            cache_key = (generation_id, target_utterance_id)
            cached = self._reply_cancel_results.get(cache_key)
            if cached is not None:
                self._reply_cancel_results.move_to_end(cache_key)
                await self._broadcast(cached)
                return [cached]

        cancellations = await self._reply_pipeline.cancel(
            generation_id=generation_id,
            target_turn_id=target_utterance_id,
        )
        if generation_id is not None and target_utterance_id is not None:
            cancelled_suggestion_ids = list(cancellations[0].cancelled_suggestion_ids) if cancellations else []
            result = ReplyCancelResultMsg(
                generation_id=generation_id,
                target_utterance_id=target_utterance_id,
                status="applied" if cancelled_suggestion_ids else "not_applied",
                cancelled_suggestion_ids=cancelled_suggestion_ids,
            )
            self._cache_reply_cancel_result(result)
            await self._broadcast(result)
            return [result]

        results = [
            ReplyCancelResultMsg(
                generation_id=cancellation.generation_id,
                target_utterance_id=cancellation.target_turn_id,
                status="applied",
                cancelled_suggestion_ids=list(cancellation.cancelled_suggestion_ids),
            )
            for cancellation in cancellations
        ]
        for result in results:
            self._cache_reply_cancel_result(result)
            await self._broadcast(result)
        return results

    def clear_reply_cancel_results(self) -> None:
        self._reply_cancel_results.clear()

    def _cache_reply_cancel_result(self, result: ReplyCancelResultMsg) -> None:
        cache_key = (result.generation_id, result.target_utterance_id)
        self._reply_cancel_results[cache_key] = result
        self._reply_cancel_results.move_to_end(cache_key)
        while len(self._reply_cancel_results) > 30:
            _ = self._reply_cancel_results.popitem(last=False)

    async def run_info_now(self) -> None:
        """Start an explicit info-note refresh for the current meeting."""
        await self._info_note_updater.run_now()

    async def reset_info_note_updater(self) -> None:
        await self._info_note_updater.reset()

    async def _append_turn(
        self,
        role: str,
        text: str,
        speaker_id: str | None = None,
        *,
        set_active_target: bool = False,
    ) -> tuple[Turn, int, MeetingSession] | None:
        """Append a turn under ``_turn_lock`` and return it with its sequence.

        Returns ``None`` when there is no running session. The returned
        ``MeetingSession`` is the *original* session object (before the turn
        was added); callers should use the returned sequence number, which is
        computed from the updated session stored in ``state.current_session``.
        """
        if not self._state.is_running:
            return None

        async with self._turn_lock:
            session = cast(MeetingSession | None, self._state.current_session)
            if session is None:
                return None
            turn = self._turn_factory(speaker=role, text=text, speaker_id=speaker_id)
            next_session = session.with_turn(turn)
            self._state.current_session = next_session
            if set_active_target:
                self._state.active_suggestion_target_id = turn.id
            turn_sequence = len(next_session.turns) - 1
            return turn, turn_sequence, session

    async def handle_speech(self, role: str, text: str, speaker_id: str | None = None) -> None:
        result = await self._append_turn(role, text, speaker_id, set_active_target=True)
        if result is None:
            return

        turn, turn_idx, session = result
        meeting_id = session.id

        if self._history_service is not None:
            _ = self._history_service.schedule_insert_turn(meeting_id, turn_idx, turn)
        await self._broadcast(SttFinalMsg(role=role, text=text, speaker_id=speaker_id, utterance_id=turn.id))
        self._info_note_updater.trigger()
        if self._reply_auto_generate and role == "other":
            _ = self._reply_pipeline.start_for_turn(
                target_turn_id=turn.id,
                target_turn_idx=turn_idx,
                target_role=role,
                mode="normal",
            )

    async def handle_user_reply(self, text: str) -> None:
        """Append a user-authored self turn and broadcast it.

        Unlike ``handle_speech``, this does not generate reply suggestions,
        preserving the legacy ``user_reply`` semantics while still using the
        shared lock/broadcast/persist path.
        """
        result = await self._append_turn("self", text)
        if result is None:
            return

        turn, turn_sequence, session = result
        if self._history_service is not None:
            _ = self._history_service.schedule_insert_turn(session.id, turn_sequence, turn)
        await self._broadcast(SttFinalMsg(role="self", text=text, speaker_id=None, utterance_id=turn.id))
        self._info_note_updater.trigger()


__all__ = ["ConversationOrchestrator"]
