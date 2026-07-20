"""Thread-safe publisher for outgoing WebSocket messages.

Stages running in worker threads use ThreadSafePublisher to bridge
run_coroutine_threadsafe calls into the main asyncio loop, instead of holding
a raw loop reference and calling run_coroutine_threadsafe themselves.
"""

import asyncio
import logging
import threading
from collections.abc import Coroutine
from concurrent.futures import Future as ConcurrentFuture
from typing import Protocol

from app.core.messages import OutgoingBroadcastFn, OutgoingMessage

logger = logging.getLogger(__name__)


class OutgoingPublisher(Protocol):
    """Sync interface for publishing from any execution context."""

    def publish(self, msg: OutgoingMessage) -> None:
        """Broadcast an outgoing WebSocket message. Fire-and-forget."""
        ...

    def schedule(self, coro: Coroutine[object, object, object]) -> None:
        """Schedule a coroutine on the main loop. Fire-and-forget."""
        ...


class ThreadSafePublisher:
    """Bridges worker threads → main asyncio loop.

    Both methods are safe to call from any thread, including the main loop thread.
    When called from the same loop they use create_task; otherwise
    run_coroutine_threadsafe is used.
    """

    def __init__(self, broadcast_fn: OutgoingBroadcastFn, loop: asyncio.AbstractEventLoop) -> None:
        self._broadcast: OutgoingBroadcastFn = broadcast_fn
        self._loop: asyncio.AbstractEventLoop = loop
        self._pending_futures: set[asyncio.Future[object] | ConcurrentFuture[object]] = set()
        self._pending_futures_lock: threading.Lock = threading.Lock()

    def _track_future(self, future: asyncio.Future[object] | ConcurrentFuture[object]) -> None:
        with self._pending_futures_lock:
            self._pending_futures.add(future)
        future.add_done_callback(self._log_future_result)

    def _log_future_result(self, future: asyncio.Future[object] | ConcurrentFuture[object]) -> None:
        with self._pending_futures_lock:
            self._pending_futures.discard(future)
        if future.cancelled():
            logger.warning("Published coroutine was cancelled")
            return
        exc = future.exception()
        if exc is not None:
            logger.error(
                "Published coroutine failed",
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    def publish(self, msg: OutgoingMessage) -> None:
        self.schedule(self._broadcast(msg))

    def schedule(self, coro: Coroutine[object, object, object]) -> None:
        if self._loop.is_closed():
            logger.warning("Loop closed, dropping coroutine")
            coro.close()
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if running is self._loop:
            task = self._loop.create_task(coro)
            self._track_future(task)
        else:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            self._track_future(future)


__all__ = ["OutgoingPublisher", "ThreadSafePublisher"]
