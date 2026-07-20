"""Base class for WebSocket-based STT stages.

Eliminates the ``asyncio.run()`` anti-pattern (which conflicts with a running
asyncio loop) by creating a fresh event loop inside the worker thread.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Coroutine
from typing import override

from app.audio.base import AudioFrame, PipelineStage
from app.core.publisher import OutgoingPublisher
from app.core.types import HandleSpeechFn


class WebSocketSttStage[T](PipelineStage, ABC):
    """Threading-based stage that runs an asyncio WebSocket session.

    Subclasses implement:

    * ``_transform_frame`` – how to put an AudioFrame onto the internal queue.
    * ``_session``         – the actual WebSocket coroutine.
    * ``_on_session_error`` – error reporting.
    """

    _AUDIO_QUEUE_SIZE: int = 200

    def __init__(
        self,
        in_q: queue.Queue[AudioFrame | None],
        role: str,
        publisher: OutgoingPublisher,
        handle_speech_fn: HandleSpeechFn,
    ) -> None:
        super().__init__()
        self._in_q: queue.Queue[AudioFrame | None] = in_q
        self._role: str = role
        self._publisher: OutgoingPublisher = publisher
        self._handle_speech: HandleSpeechFn = handle_speech_fn

    # ── Threading entry point (shared) ──────────────────────────────────────

    @override
    def _run(self) -> None:
        while not self._stop_event.is_set():
            session_active = threading.Event()
            session_active.set()
            audio_queue: queue.Queue[T | None] = queue.Queue(maxsize=self._AUDIO_QUEUE_SIZE)
            drain_thread = self._start_drain_thread(audio_queue, session_active)

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self._session(loop, audio_queue, session_active))
                finally:
                    try:
                        loop.run_until_complete(loop.shutdown_asyncgens())
                    finally:
                        loop.close()
            except Exception as exc:
                self._on_session_error(exc)
                if not self._stop_event.is_set():
                    time.sleep(2)
            finally:
                session_active.clear()
                drain_thread.join(timeout=2)

    @abstractmethod
    async def _session(
        self,
        loop: asyncio.AbstractEventLoop,
        audio_queue: queue.Queue[T | None],
        session_active: threading.Event,
    ) -> None:
        """Run a single WebSocket session."""

    @abstractmethod
    def _on_session_error(self, exc: Exception) -> None:
        """Report a session-level error."""

    @abstractmethod
    def _transform_frame(self, frame: AudioFrame) -> T:
        """Convert an AudioFrame into the payload placed on *audio_queue*."""

    # ── Shared helpers ──────────────────────────────────────────────────────

    def _start_drain_thread(
        self,
        audio_queue: queue.Queue[T | None],
        session_active: threading.Event,
    ) -> threading.Thread:
        def _drain() -> None:
            while not self._stop_event.is_set() and session_active.is_set():
                try:
                    frame = self._in_q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if frame is None:
                    audio_queue.put(None)
                    break
                try:
                    audio_queue.put_nowait(self._transform_frame(frame))
                except queue.Full:
                    pass

        t = threading.Thread(target=_drain, daemon=True)
        t.start()
        return t

    @staticmethod
    async def _run_send_recv(
        send_coro: Coroutine[None, None, None],
        recv_coro: Coroutine[None, None, None],
    ) -> None:
        send_task = asyncio.create_task(send_coro)
        recv_task = asyncio.create_task(recv_coro)
        done, pending = await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            _ = task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        for task in done:
            task.result()


__all__ = ["WebSocketSttStage"]
