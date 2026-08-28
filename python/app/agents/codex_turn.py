"""Ephemeral Codex turn state, streaming, and interruption protocol."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from typing import Literal, Protocol

from app.agents.codex_models import CodexSafeError

_DELTA_QUEUE_LIMIT = 512
_DELTA_SIZE_LIMIT = 256 * 1024
REPLY_REASONING_EFFORT: Literal["low"] = "low"
_END = object()


class CodexTurnHost(Protocol):
    """Internal operations a turn needs from its shared app-server host."""

    def spawn_background(self, coroutine: Coroutine[object, object, None], name: str) -> None: ...

    async def interrupt_turn(self, turn: CodexTurn) -> None: ...

    async def interrupt_overflow_turn(self, turn: CodexTurn) -> None: ...

    def turn_finished(self, turn: CodexTurn) -> None: ...


class CodexTurn:
    """One ephemeral Codex turn dispatched through a shared app-server process."""

    _peer: CodexTurnHost
    thread_id: str
    requested_model: str
    effective_model: str
    effective_model_provider: str
    _finished: bool
    _subscription_cleanup_claimed: bool

    def __init__(
        self,
        peer: CodexTurnHost,
        thread_id: str,
        *,
        requested_model: str,
        effective_model: str,
        effective_model_provider: str,
    ) -> None:
        self._peer = peer
        self.thread_id = thread_id
        self.turn_id: str | None = None
        self.requested_model = requested_model
        self.effective_model = effective_model
        self.effective_model_provider = effective_model_provider
        self.reasoning_effort: Literal["low"] = REPLY_REASONING_EFFORT
        self._queue: asyncio.Queue[str | CodexSafeError | object] = asyncio.Queue(maxsize=_DELTA_QUEUE_LIMIT)
        self._finished = False
        self._subscription_cleanup_claimed = False

    @property
    def finished(self) -> bool:
        return self._finished

    def claim_subscription_cleanup(self) -> bool:
        if self._subscription_cleanup_claimed:
            return False
        self._subscription_cleanup_claimed = True
        return True

    def emit(self, delta: str) -> None:
        if self._finished or not delta:
            return
        if len(delta) > _DELTA_SIZE_LIMIT or self._queue.full():
            self._finished = True
            self._replace_queue_if_full(
                CodexSafeError(
                    "stream_backpressure",
                    "Codex の応答を安全に受信できませんでした。もう一度お試しください。",
                    retryable=True,
                )
            )
            self._peer.spawn_background(
                self._peer.interrupt_overflow_turn(self),
                "codex-backpressure-interrupt",
            )
            return
        self._queue.put_nowait(delta)

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._replace_queue_if_full(_END)
        self._peer.turn_finished(self)

    def fail(self, error: CodexSafeError) -> None:
        if self._finished:
            return
        self._finished = True
        self._replace_queue_if_full(error)
        self._peer.turn_finished(self)

    def _replace_queue_if_full(self, item: object) -> None:
        if self._queue.full():
            while not self._queue.empty():
                _ = self._queue.get_nowait()
        self._queue.put_nowait(item)

    async def interrupt(self) -> None:
        await self._peer.interrupt_turn(self)

    async def deltas(self) -> AsyncIterator[str]:
        try:
            while True:
                item = await self._queue.get()
                if item is _END:
                    return
                if isinstance(item, CodexSafeError):
                    raise item
                if isinstance(item, str):
                    yield item
        finally:
            if not self.finished:
                await self.interrupt()
