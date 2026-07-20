"""Meeting domain models used while a meeting is running."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal

from app.core.messages import SessionInfoMsg
from app.core.protocols import TurnLike

SuggestionMode = Literal["normal", "polite", "short", "clarify", "buy_time", "push_back", "summarize"]


@dataclass(frozen=True)
class Turn:
    id: str
    speaker: str  # "other" | "self"
    text: str
    speaker_id: str | None = None


def _new_utterance_id() -> str:
    uuid7_fn = getattr(uuid, "uuid7", None)
    if callable(uuid7_fn):
        return str(uuid7_fn())
    return str(uuid.uuid4())


def history_texts(turns: Sequence[TurnLike]) -> list[str]:
    return [f"{'相手' if t.speaker == 'other' else '自分'}: {t.text}" for t in turns]


@dataclass(frozen=True)
class MeetingContext:
    scenario: str = "会議"
    user_role: str = "会議メンバー"
    counterpart_role: str = ""
    objective: str = "目的未設定"
    background: str = ""
    tone: str = "簡潔で自然"
    constraints: str = ""
    custom_instructions: str = ""


@dataclass(frozen=True)
class ReferenceDocument:
    id: str
    name: str
    mime_type: str
    size_bytes: int
    status: Literal["parsed", "failed"]
    text: str = ""
    error: str = ""


@dataclass(frozen=True)
class ReplySuggestion:
    id: str
    target_turn_id: str
    agent_id: str
    agent_label: str
    text: str
    mode: SuggestionMode = "normal"


@dataclass(frozen=True)
class MeetingSession:
    id: str
    started_at: datetime
    title: str | None = None
    ended_at: datetime | None = None
    turns: tuple[Turn, ...] = field(default_factory=tuple)
    reply_suggestions: tuple[ReplySuggestion, ...] = field(default_factory=tuple)
    ai_note: str = ""
    is_active: bool = True
    meeting_context: MeetingContext = field(default_factory=MeetingContext)
    references: tuple[ReferenceDocument, ...] = field(default_factory=tuple)

    def with_turn(self, turn: Turn) -> "MeetingSession":
        return replace(self, turns=self.turns + (turn,))

    def with_reply_suggestion(self, suggestion: ReplySuggestion) -> "MeetingSession":
        return replace(self, reply_suggestions=self.reply_suggestions + (suggestion,))

    def with_ai_note(self, text: str) -> "MeetingSession":
        return replace(self, ai_note=text)

    def ended(self) -> "MeetingSession":
        return replace(self, ended_at=datetime.now(UTC), is_active=False)


def session_info_msg(session: MeetingSession) -> SessionInfoMsg:
    return SessionInfoMsg(
        id=session.id,
        started_at=session.started_at.isoformat(),
        title=session.title,
        ended_at=session.ended_at.isoformat() if session.ended_at else None,
        is_active=session.is_active,
    )


__all__ = [
    "MeetingContext",
    "MeetingSession",
    "ReferenceDocument",
    "ReplySuggestion",
    "SuggestionMode",
    "Turn",
    "_new_utterance_id",
    "history_texts",
    "session_info_msg",
]
