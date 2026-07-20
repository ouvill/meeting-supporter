"""Typed WebSocket message models for the meeting-supporter protocol."""

from collections.abc import Callable, Coroutine
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from app.core.types import InputDevice

SuggestionMode = Literal["normal", "polite", "short", "clarify", "buy_time", "push_back", "summarize"]

# ── Shared payloads ────────────────────────────────────────────────────────────


class MeetingContextPayload(BaseModel):
    scenario: str = "会議"
    userRole: str = "会議メンバー"
    counterpartRole: str | None = None
    objective: str = "目的未設定"
    background: str | None = None
    tone: str | None = "簡潔で自然"
    constraints: str | None = None
    customInstructions: str | None = None


class ReferenceDocumentPayload(BaseModel):
    id: str
    name: str
    mimeType: str
    sizeBytes: int
    text: str | None = None
    contentBase64: str | None = None


# ── Incoming (client → server) ────────────────────────────────────────────────


class SetDeviceMsg(BaseModel):
    type: Literal["set_device"]
    role: str
    device: int | str | None = None


class InitSttMsg(BaseModel):
    type: Literal["init_stt"]


class ShutdownSttMsg(BaseModel):
    type: Literal["shutdown_stt"]


class StartMeetingMsg(BaseModel):
    type: Literal["start_meeting"]
    meeting_context: MeetingContextPayload | None = None
    references: list[ReferenceDocumentPayload] = Field(default_factory=list)


class StopMeetingMsg(BaseModel):
    type: Literal["stop_meeting"]


class ManualSpeechMsg(BaseModel):
    type: Literal["manual_speech"]
    text: str


class UserReplyMsg(BaseModel):
    type: Literal["user_reply"]
    text: str


class GenerateReplyMsg(BaseModel):
    type: Literal["generate_reply"]
    generation_id: str
    target_utterance_id: str | None = None
    mode: SuggestionMode = "normal"


class CancelReplyMsg(BaseModel):
    type: Literal["cancel_reply"]
    generation_id: str
    target_utterance_id: str


class RunInfoMsg(BaseModel):
    type: Literal["run_info"]


class ReloadContextMsg(BaseModel):
    type: Literal["reload_context"]


type IncomingMessage = Annotated[
    SetDeviceMsg
    | InitSttMsg
    | ShutdownSttMsg
    | StartMeetingMsg
    | StopMeetingMsg
    | ManualSpeechMsg
    | UserReplyMsg
    | GenerateReplyMsg
    | CancelReplyMsg
    | RunInfoMsg
    | ReloadContextMsg,
    Field(discriminator="type"),
]

_incoming_ta: TypeAdapter[IncomingMessage] = TypeAdapter(IncomingMessage)


# ── Outgoing (server → client) ────────────────────────────────────────────────


class StatusMsg(BaseModel):
    type: Literal["status"] = "status"
    text: str


class MeetingStateMsg(BaseModel):
    type: Literal["meeting_state"] = "meeting_state"
    running: bool


class SttStateMsg(BaseModel):
    type: Literal["stt_state"] = "stt_state"
    backend: str
    initialized: bool
    initializing: bool


class TurnItem(BaseModel):
    id: str
    speaker: str
    text: str
    speaker_id: str | None = None


class DevicesListMsg(BaseModel):
    type: Literal["devices_list"] = "devices_list"
    devices: list[InputDevice]
    current_other: int | str | None
    current_self: int | str | None


class ReplyAgentSettingsItem(BaseModel):
    id: str
    label: str
    enabled: bool
    priority: int
    model: str | None = None


class AgentSettingsMsg(BaseModel):
    type: Literal["agent_settings"] = "agent_settings"
    reply_enabled: bool
    reply_auto_generate: bool
    reply_agents: list[ReplyAgentSettingsItem]
    info_enabled: bool


class HistoryResetMsg(BaseModel):
    type: Literal["history_reset"] = "history_reset"
    items: list[TurnItem]


class AiNoteUpdatedMsg(BaseModel):
    type: Literal["ai_note_updated"] = "ai_note_updated"
    text: str


class ErrorMsg(BaseModel):
    type: Literal["error"] = "error"
    text: str


class AudioLevelMsg(BaseModel):
    type: Literal["audio_level"] = "audio_level"
    role: str
    level: float


class SttInterimMsg(BaseModel):
    type: Literal["stt_interim"] = "stt_interim"
    role: str
    text: str


class StreamInfoMsg(BaseModel):
    type: Literal["stream_info"] = "stream_info"
    role: str
    device: str
    rate: int


class SessionInfoMsg(BaseModel):
    type: Literal["session_info"] = "session_info"
    id: str
    started_at: str
    title: str | None = None
    ended_at: str | None = None
    is_active: bool


class InfoResearchingMsg(BaseModel):
    type: Literal["info_researching"] = "info_researching"


class InfoResearchingFinishedMsg(BaseModel):
    type: Literal["info_researching_finished"] = "info_researching_finished"


class SttFinalMsg(BaseModel):
    type: Literal["stt_final"] = "stt_final"
    role: str
    text: str
    speaker_id: str | None
    utterance_id: str


class SuggestionsStartMsg(BaseModel):
    type: Literal["suggestions_start"] = "suggestions_start"
    agent_id: str
    agent_label: str
    agent_priority: int
    generation_id: str
    suggestion_id: str
    target_utterance_id: str
    target_role: str
    mode: SuggestionMode = "normal"


class ReplyChunkMsg(BaseModel):
    type: Literal["reply_chunk"] = "reply_chunk"
    text: str
    final: bool
    agent_id: str
    agent_label: str
    agent_priority: int
    generation_id: str
    suggestion_id: str
    target_utterance_id: str
    target_role: str
    mode: SuggestionMode = "normal"


class SuggestionErrorMsg(BaseModel):
    type: Literal["suggestion_error"] = "suggestion_error"
    text: str
    agent_id: str
    agent_label: str
    agent_priority: int
    generation_id: str
    suggestion_id: str
    target_utterance_id: str
    target_role: str
    mode: SuggestionMode = "normal"


class ReplyCancelResultMsg(BaseModel):
    type: Literal["reply_cancel_result"] = "reply_cancel_result"
    generation_id: str
    target_utterance_id: str
    status: Literal["applied", "not_applied"]
    cancelled_suggestion_ids: list[str]


type OutgoingMessage = (
    StatusMsg
    | MeetingStateMsg
    | SttStateMsg
    | DevicesListMsg
    | AgentSettingsMsg
    | HistoryResetMsg
    | AiNoteUpdatedMsg
    | ErrorMsg
    | AudioLevelMsg
    | SttInterimMsg
    | StreamInfoMsg
    | SessionInfoMsg
    | InfoResearchingMsg
    | InfoResearchingFinishedMsg
    | SttFinalMsg
    | SuggestionsStartMsg
    | ReplyChunkMsg
    | SuggestionErrorMsg
    | ReplyCancelResultMsg
)

type OutgoingBroadcastFn = Callable[[OutgoingMessage], Coroutine[object, object, None]]


__all__ = [
    "AgentSettingsMsg",
    "AiNoteUpdatedMsg",
    "AudioLevelMsg",
    "CancelReplyMsg",
    "DevicesListMsg",
    "ErrorMsg",
    "GenerateReplyMsg",
    "HistoryResetMsg",
    "IncomingMessage",
    "InitSttMsg",
    "ManualSpeechMsg",
    "MeetingContextPayload",
    "MeetingStateMsg",
    "OutgoingBroadcastFn",
    "OutgoingMessage",
    "ReferenceDocumentPayload",
    "ReloadContextMsg",
    "ReplyAgentSettingsItem",
    "ReplyCancelResultMsg",
    "ReplyChunkMsg",
    "SessionInfoMsg",
    "SetDeviceMsg",
    "RunInfoMsg",
    "ShutdownSttMsg",
    "StartMeetingMsg",
    "StatusMsg",
    "StopMeetingMsg",
    "StreamInfoMsg",
    "SttFinalMsg",
    "SttInterimMsg",
    "SttStateMsg",
    "SuggestionErrorMsg",
    "SuggestionMode",
    "SuggestionsStartMsg",
    "TurnItem",
    "UserReplyMsg",
    "_incoming_ta",
]
