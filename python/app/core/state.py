"""Application state container."""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import override

from app.core.protocols import ConversationState, SecretStore, SttStreamLike, TurnLike
from app.meetings.models import MeetingSession
from app.services.config_loader import ConfigLoader

_INITIAL_AI_NOTE = """\
# 会議補助資料

## サマリー


## 用語解説


## 背景情報


## 参考資料
"""


def _parse_device(val: str | None) -> "int | str | None":
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        return val


@dataclass
class AppState(ConversationState):
    config: ConfigLoader
    secret_store: SecretStore
    current_session: MeetingSession | None = None  # pyright: ignore[reportIncompatibleVariableOverride]
    active_suggestion_target_id: str | None = None
    stt_other: SttStreamLike | None = None
    stt_self: SttStreamLike | None = None
    device_other: int | str | None = None
    device_self: int | str | None = None
    context_text: str = ""
    stt_initialized: bool = False
    stt_initializing: bool = False

    @property
    @override
    def is_running(self) -> bool:
        return self.current_session.is_active if self.current_session else False

    @property
    @override
    def turns(self) -> Sequence[TurnLike]:
        return self.current_session.turns if self.current_session else ()

    @property
    @override
    def ai_note(self) -> str:
        return self.current_session.ai_note if self.current_session else ""

    def __post_init__(self) -> None:
        if self.device_other is None:
            self.device_other = _parse_device(os.getenv("DEVICE_OTHER"))
        if self.device_self is None:
            self.device_self = _parse_device(os.getenv("DEVICE_SELF"))


__all__ = [
    "AppState",
    "_INITIAL_AI_NOTE",
    "_parse_device",
]
