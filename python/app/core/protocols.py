"""Protocol classes for structural typing across the application."""

import asyncio
import queue
from collections.abc import AsyncIterator, Callable, Coroutine, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Never, Protocol, Self, final, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

    from app.audio.base import AudioFrame, RecordingResult

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


class SecretSnapshotError(RuntimeError):
    """A transactional secret snapshot could not determine prior state."""


class SecretRollbackError(RuntimeError):
    """One or more secret-store compensation steps failed."""

    failures: tuple[Exception, ...]

    def __init__(self, failures: Iterable[Exception]) -> None:
        self.failures = tuple(failures)
        details = "; ".join(str(failure) for failure in self.failures)
        super().__init__(f"secret rollback failed: {details}")


@final
@dataclass(frozen=True, slots=True)
class SecretSnapshot:
    """Opaque, backend-typed state captured before a mutation.

    A payload must retain the exact durable representation needed to reverse
    its backend without including secret values in rollback diagnostics.
    """

    value: object


class SecretStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set_secrets(self, updates: dict[str, str]) -> None: ...
    def delete(self, key: str) -> None: ...
    def status(self, key: str) -> bool: ...
    def status_all(self) -> dict[str, bool]: ...
    def apply_secrets_to_env(self, keys: Iterable[str] | None = None) -> None: ...


@runtime_checkable
class TransactionalSecretStore(SecretStore, Protocol):
    """Secret store supporting exact pre-mutation snapshot restoration."""

    def snapshot(self, keys: Iterable[str]) -> SecretSnapshot: ...
    def restore(self, snapshot: SecretSnapshot) -> None: ...


__all__ = [
    "AgentLike",
    "AudioPipelineLike",
    "ConversationState",
    "LifecycledAgentLike",
    "SecretRollbackError",
    "SecretSnapshotError",
    "SecretSnapshot",
    "SecretStore",
    "TransactionalSecretStore",
    "StreamLike",
    "SttState",
    "SttStreamLike",
    "TurnLike",
    "WebSocketLike",
]
