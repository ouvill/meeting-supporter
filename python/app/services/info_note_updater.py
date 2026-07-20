import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import cast
from uuid import uuid4

from app.agents.models import InfoAgentRuntime, InfoPrompt
from app.agents.prompts import build_info_prompt
from app.core.config import UsageBudgetConfig
from app.core.messages import (
    AiNoteUpdatedMsg,
    ErrorMsg,
    InfoResearchingFinishedMsg,
    InfoResearchingMsg,
    OutgoingBroadcastFn,
    StatusMsg,
)
from app.core.protocols import ConversationState, TurnLike
from app.meetings.models import MeetingSession, history_texts
from app.services.usage_logger import UsageBudget, UsageLogger

INFO_AUTO_UPDATE_INTERVAL = 5
INFO_COMPLETE_NOTE_MAX_CHARS = 20_000
_EXPECTED_NOTE_HEADINGS = (
    "# 会話メモ",
    "## 決まったこと",
    "## 未確認・懸念",
    "## 次にすること",
)
_H1_OR_H2_PATTERN = re.compile(r"^[ \t]{0,3}#{1,2}(?:[ \t]+|$)")
_SETEXT_HEADING_PATTERN = re.compile(r"^[ \t]{0,3}(?:=+|-+)[ \t]*$", re.MULTILINE)
_HTML_HEADING_PATTERN = re.compile(r"<\s*/?\s*h[12]\b", re.IGNORECASE)
_FENCE_PATTERN = re.compile(r"^\s*(?:```|~~~)", re.MULTILINE)


def normalize_complete_ai_note(text: str) -> str | None:
    """Normalize a complete note only when it satisfies the fixed Markdown envelope."""
    if not text or len(text) > INFO_COMPLETE_NOTE_MAX_CHARS or "\0" in text:
        return None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if (
        not normalized
        or _FENCE_PATTERN.search(normalized)
        or _SETEXT_HEADING_PATTERN.search(normalized)
        or _HTML_HEADING_PATTERN.search(normalized)
    ):
        return None
    lines = normalized.splitlines()
    headings = [line.rstrip() for line in lines if _H1_OR_H2_PATTERN.match(line)]
    if headings != list(_EXPECTED_NOTE_HEADINGS) or lines[0].rstrip() != _EXPECTED_NOTE_HEADINGS[0]:
        return None
    return "\n".join(line.rstrip() for line in lines)


class InfoNoteUpdater:
    """Hidden AI-note update use case for meeting context enrichment."""

    def __init__(
        self,
        *,
        state: ConversationState,
        broadcast: OutgoingBroadcastFn,
        info_runtime: InfoAgentRuntime | None,
        turn_lock: asyncio.Lock,
        info_enabled: bool = True,
        usage_logger: UsageLogger | None = None,
        usage_budget: UsageBudgetConfig | None = None,
        info_readiness: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._state: ConversationState = state
        self._broadcast: OutgoingBroadcastFn = broadcast
        self._info_runtime: InfoAgentRuntime | None = info_runtime
        self._turn_lock: asyncio.Lock = turn_lock
        self._info_enabled: bool = info_enabled
        self._usage_logger: UsageLogger | None = usage_logger
        self._usage_budget: UsageBudgetConfig = usage_budget or UsageBudgetConfig()
        self._info_agent_task: asyncio.Task[None] | None = None
        self._info_last_processed: int = 0
        self._info_readiness: Callable[[], Awaitable[bool]] | None = info_readiness
        self._meeting_id: str | None = None
        self._active_commit_id: str | None = None

    async def update_runtime(self, info_runtime: InfoAgentRuntime | None) -> None:
        await self.cancel()
        self._info_runtime = info_runtime

    def update_budget(self, usage_budget: UsageBudgetConfig) -> None:
        self._usage_budget = usage_budget

    async def apply_enabled(self, info_enabled: bool) -> None:
        if self._info_enabled == info_enabled:
            return

        self._info_enabled = info_enabled
        if not info_enabled:
            await self.cancel()
            await self._broadcast(AiNoteUpdatedMsg(text=self._state.ai_note))

    async def replace_ai_note(self, old_str: str, new_str: str) -> str:
        """Replace meeting ai_note text under the shared session mutation lock."""
        async with self._turn_lock:
            session = cast(MeetingSession | None, self._state.current_session)
            if session is None:
                return "ERROR: 現在アクティブなセッションがありません"
            if self._meeting_id != session.id:
                return "ERROR: 会議が切り替わったため更新できません"
            if old_str not in session.ai_note:
                return f"ERROR: old_str が資料内に見つかりません。現在の資料:\n---\n{session.ai_note}"
            self._state.current_session = session.with_ai_note(session.ai_note.replace(old_str, new_str, 1))
            return "OK"

    async def cancel(self) -> None:
        task = self._info_agent_task
        if task is None or task.done():
            self._info_agent_task = None
            return
        _ = task.cancel()
        _ = await asyncio.gather(task, return_exceptions=True)
        self._info_agent_task = None

    async def reset(self) -> None:
        """Cancel meeting-scoped work and clear all update checkpoints."""
        await self.cancel()
        self._meeting_id = None
        self._info_last_processed = 0
        self._active_commit_id = None

    def _budget_blocks_generation(self, meeting_id: str) -> bool:
        if self._usage_logger is None:
            return False
        return self._usage_logger.is_budget_exceeded(
            UsageBudget(
                meeting_limit_jpy=self._usage_budget.meeting_limit_jpy,
                monthly_limit_jpy=self._usage_budget.monthly_limit_jpy,
            ),
            meeting_id=meeting_id,
        )

    async def _notify_unavailable(self) -> None:
        await self._broadcast(ErrorMsg(text="情報AIは未設定です。設定で対応するAI経路を選択してください。"))

    async def _auto_route_ready(self) -> bool:
        provider = self._info_readiness
        if provider is None:
            return False
        try:
            return await provider()
        except Exception:
            return False

    async def _commit_complete_note(
        self,
        *,
        meeting_id: str,
        expected_note: str,
        commit_id: str,
        complete_note: str,
    ) -> tuple[bool, bool]:
        async with self._turn_lock:
            session = cast(MeetingSession | None, self._state.current_session)
            if (
                self._active_commit_id != commit_id
                or session is None
                or session.id != meeting_id
                or session.ai_note != expected_note
            ):
                return False, False
            if complete_note == expected_note:
                return True, False
            self._state.current_session = session.with_ai_note(complete_note)
            return True, True

    async def _run_info_agent(self, turns_snapshot: list[TurnLike], *, notify_unavailable: bool = True) -> bool:
        async with self._turn_lock:
            session = cast(MeetingSession | None, self._state.current_session)
            if session is None:
                return False
            meeting_id = session.id
            expected_note = session.ai_note
            commit_id = uuid4().hex
            self._meeting_id = meeting_id
        if self._budget_blocks_generation(meeting_id):
            await self._broadcast(ErrorMsg(text="AI利用量の予算上限に達したため、情報AIを停止しました"))
            return False
        info_runtime = self._info_runtime
        if info_runtime is None:
            if notify_unavailable:
                await self._notify_unavailable()
            return False
        self._active_commit_id = commit_id
        prompt = build_info_prompt(history_texts(turns_snapshot), expected_note)
        success = False
        complete_note_changed = False
        try:
            await self._broadcast(InfoResearchingMsg())
            async with info_runtime.run_stream(InfoPrompt(text=prompt)) as stream:
                if info_runtime.output_mode == "complete_note":
                    chunks: list[str] = []
                    character_count = 0
                    async for chunk in stream.stream_text(delta=True):
                        character_count += len(chunk)
                        if character_count > INFO_COMPLETE_NOTE_MAX_CHARS:
                            raise ValueError("complete note exceeded the output boundary")
                        chunks.append(chunk)
                    normalized_note = normalize_complete_ai_note("".join(chunks))
                    if normalized_note is None:
                        raise ValueError("complete note did not satisfy the Markdown contract")
                    success, complete_note_changed = await self._commit_complete_note(
                        meeting_id=meeting_id,
                        expected_note=expected_note,
                        commit_id=commit_id,
                        complete_note=normalized_note,
                    )
                else:
                    # Tool runtimes mutate the note through the guarded str_replace callback.
                    async for _ in stream.stream_text(delta=True):
                        pass
                    success = True
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._broadcast(ErrorMsg(text="情報AIの処理に失敗しました。設定と接続状態を確認してください。"))
        finally:
            if self._active_commit_id == commit_id:
                self._active_commit_id = None
            if info_runtime.output_mode == "tool_update":
                await self._broadcast(AiNoteUpdatedMsg(text=self._state.ai_note))
            else:
                try:
                    if complete_note_changed:
                        await self._broadcast(AiNoteUpdatedMsg(text=self._state.ai_note))
                finally:
                    await self._broadcast(InfoResearchingFinishedMsg())
        return success

    async def _info_agent_loop(self) -> None:
        while True:
            current_count = len(self._state.turns)
            if current_count - self._info_last_processed < INFO_AUTO_UPDATE_INTERVAL:
                break
            turns_snapshot = list(self._state.turns)
            attempt_count = len(turns_snapshot)
            self._info_last_processed = attempt_count
            if not await self._auto_route_ready():
                continue
            _ = await self._run_info_agent(turns_snapshot, notify_unavailable=False)

    def trigger(self) -> None:
        if not self._info_enabled:
            return
        if len(self._state.turns) - self._info_last_processed < INFO_AUTO_UPDATE_INTERVAL:
            return
        if self._info_agent_task is None or self._info_agent_task.done():
            self._info_agent_task = asyncio.create_task(self._info_agent_loop())

    async def _run_manual_info_agent(self, turns_snapshot: list[TurnLike]) -> None:
        self._info_last_processed = len(turns_snapshot)
        _ = await self._run_info_agent(turns_snapshot)

    async def run_now(self) -> None:
        """Start an explicit info-note refresh for the current meeting."""
        if not self._info_enabled:
            await self._broadcast(ErrorMsg(text="情報AIは現在オフです"))
            return
        if self._info_runtime is None:
            await self._notify_unavailable()
            return
        if not self._state.is_running or self._state.current_session is None:
            await self._broadcast(ErrorMsg(text="会議が開始されていません"))
            return
        turns_snapshot = list(self._state.turns)
        if not turns_snapshot:
            await self._broadcast(ErrorMsg(text="情報整理の対象となる発言がありません"))
            return
        if self._info_agent_task is not None and not self._info_agent_task.done():
            await self._broadcast(StatusMsg(text="情報AIを更新中です"))
            return
        self._info_agent_task = asyncio.create_task(self._run_manual_info_agent(turns_snapshot))


__all__ = ["InfoNoteUpdater"]
