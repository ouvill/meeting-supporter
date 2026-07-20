import unittest

from app.core.event_bus import EventBus
from app.core.events import ConfigChanged


class EventBusTest(unittest.IsolatedAsyncioTestCase):
    async def test_handler_receives_published_event(self) -> None:
        bus = EventBus()
        received: list[ConfigChanged] = []

        async def handler(_event: ConfigChanged) -> None:
            received.append(_event)

        bus.subscribe(ConfigChanged, handler)
        await bus.publish(ConfigChanged())

        self.assertEqual(1, len(received))

    async def test_multiple_handlers_all_called(self) -> None:
        bus = EventBus()
        calls: list[str] = []

        async def h1(_event: ConfigChanged) -> None:
            calls.append("h1")

        async def h2(_event: ConfigChanged) -> None:
            calls.append("h2")

        bus.subscribe(ConfigChanged, h1)
        bus.subscribe(ConfigChanged, h2)
        await bus.publish(ConfigChanged())

        self.assertIn("h1", calls)
        self.assertIn("h2", calls)

    async def test_failing_handler_does_not_affect_others(self) -> None:
        bus = EventBus()
        calls: list[str] = []

        async def failing(_event: ConfigChanged) -> None:
            raise RuntimeError("boom")

        async def ok(_event: ConfigChanged) -> None:
            calls.append("ok")

        bus.subscribe(ConfigChanged, failing)
        bus.subscribe(ConfigChanged, ok)
        await bus.publish(ConfigChanged())  # must not raise

        self.assertEqual(["ok"], calls)

    async def test_publish_with_no_subscribers_is_noop(self) -> None:
        bus = EventBus()
        await bus.publish(ConfigChanged())  # must not raise

    async def test_handlers_receive_correct_event_instance(self) -> None:
        bus = EventBus()
        received: list[ConfigChanged] = []

        async def handler(_event: ConfigChanged) -> None:
            received.append(_event)

        bus.subscribe(ConfigChanged, handler)
        ev = ConfigChanged()
        await bus.publish(ev)

        self.assertIs(ev, received[0])

    async def test_unsubscribe_removes_handler(self) -> None:
        bus = EventBus()
        calls: list[str] = []

        async def handler(_event: ConfigChanged) -> None:
            calls.append("called")

        bus.subscribe(ConfigChanged, handler)
        bus.unsubscribe(ConfigChanged, handler)
        await bus.publish(ConfigChanged())

        self.assertEqual([], calls)


if __name__ == "__main__":
    _ = unittest.main()
