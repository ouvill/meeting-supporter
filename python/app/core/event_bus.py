"""Async typed event bus for decoupled inter-service communication."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from app.core.events import AppEvent

logger = logging.getLogger(__name__)


class EventBus:
    """Publish/subscribe hub for typed async events.

    Handlers registered for a given event type are called concurrently via
    asyncio.gather.  Exceptions from individual handlers are logged and do not
    propagate, so one failing handler cannot block others.
    """

    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable[..., Awaitable[None]]]] = defaultdict(list)

    def subscribe[E: AppEvent](self, event_type: type[E], handler: Callable[[E], Awaitable[None]]) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe[E: AppEvent](self, event_type: type[E], handler: Callable[[E], Awaitable[None]]) -> None:
        try:
            self._handlers[event_type].remove(handler)
        except ValueError:
            pass

    async def publish(self, event: AppEvent) -> None:
        handlers = self._handlers.get(type(event), [])
        if not handlers:
            return
        results = await asyncio.gather(*(h(event) for h in handlers), return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.error("Event handler error [%s]: %s", type(event).__name__, r)


__all__ = ["EventBus"]
