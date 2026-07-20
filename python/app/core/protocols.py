"""Protocol classes for structural typing across the application."""

import asyncio
import queue
from collections.abc import AsyncIterator, Callable, Coroutine, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Never, Protocol, Self

if TYPE_CHECKING:
    from pathlib import Path

    from app.audio.base import AudioFrame, RecordingResult
    from app.core.config import SttConfig

# ── Conversation Orchestrator protocols ──────────────────────────────────────


class TurnLike(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def speaker(self) -> str: ...

    @property
    def text(self) -> str: ...

    @property
    def speaker_id(self) -> str | None: ...


class StreamLike(Protocol):
    def stream_text(self, *, delta: bool) -> AsyncIterator[str]: ...


class AgentLike(Protocol):
    def run_stream(self, user_prompt: str) -> AbstractAsyncContextManager[StreamLike]: ...


class LifecycledAgentLike(AgentLike, Protocol):
    """AgentLike with async context-manager semantics for MCP toolset init."""

    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, *exc_info: object) -> bool | None: ...


class ConversationState(Protocol):
    @property
    def turns(self) -> Sequence[TurnLike]: ...

    active_suggestion_target_id: str | None

    @property
    def is_running(self) -> bool: ...

    context_text: str

    @property
    def ai_note(self) -> str: ...

    current_session: object | None


# ── Audio pipeline protocol ───────────────────────────────────────────────────


class AudioPipelineLike(Protocol):
    """Protocol for the audio capture + volume + recording layer (app/audio/)."""

    @property
    def stt_queue(self) -> "queue.Queue[AudioFrame | None]": ...

    @property
    def recording_queue(self) -> "queue.Queue[AudioFrame | None]": ...

    def flush_stt_queue(self) -> None: ...

    def start(self, loop: asyncio.AbstractEventLoop) -> None: ...

    def stop(self) -> None: ...

    def start_recording(self, path: "Path") -> None: ...

    def stop_recording(self) -> "RecordingResult | None": ...


# ── STT pipeline protocol ─────────────────────────────────────────────────────


class SttStreamLike(Protocol):
    """Protocol for the VAD + STT layer (app/stt/)."""

    on_ready: Callable[[], Coroutine[Never, Never, None]] | None
    on_error: Callable[[Exception], Coroutine[Never, Never, None]] | None

    def supports_prewarm(self) -> bool: ...

    def initialize(self, loop: asyncio.AbstractEventLoop) -> None: ...

    def start(self, loop: asyncio.AbstractEventLoop) -> None: ...

    def stop(self) -> None: ...

    def shutdown(self) -> None: ...

    def apply_config(self, cfg: "SttConfig") -> None: ...


class SttState(Protocol):
    @property
    def is_running(self) -> bool: ...

    stt_other: SttStreamLike | None
    stt_self: SttStreamLike | None
    stt_initialized: bool
    stt_initializing: bool
    device_other: int | str | None
    device_self: int | str | None


class WebSocketLike(Protocol):
    """Minimal WebSocket interface used by SttController."""

    async def send_json(self, data: object) -> None: ...


# ── Secret store protocol ─────────────────────────────────────────────────────


class SecretStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set_secrets(self, updates: dict[str, str]) -> None: ...
    def delete(self, key: str) -> None: ...
    def status(self, key: str) -> bool: ...
    def status_all(self) -> dict[str, bool]: ...
    def apply_secrets_to_env(self, keys: Iterable[str] | None = None) -> None: ...


__all__ = [
    "AgentLike",
    "AudioPipelineLike",
    "ConversationState",
    "LifecycledAgentLike",
    "SecretStore",
    "StreamLike",
    "SttState",
    "SttStreamLike",
    "TurnLike",
    "WebSocketLike",
]
