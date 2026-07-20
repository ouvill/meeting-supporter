"""Tests for app.core.publisher — ThreadSafePublisher."""

import asyncio
import threading
import unittest

from app.core.messages import StatusMsg
from app.core.publisher import ThreadSafePublisher


class MockBroadcastFn:
    """Captures broadcast calls for assertion."""

    def __init__(self) -> None:
        self.messages: list[object] = []
        self.called: asyncio.Event = asyncio.Event()

    async def __call__(self, msg: object) -> None:
        self.messages.append(msg)
        self.called.set()


class ThreadSafePublisherTest(unittest.IsolatedAsyncioTestCase):
    async def test_publish_from_same_loop_uses_create_task(self) -> None:
        loop = asyncio.get_running_loop()
        broadcast = MockBroadcastFn()
        publisher = ThreadSafePublisher(broadcast, loop)

        publisher.publish(StatusMsg(text="hello"))
        # Allow the event loop to process the created task.
        await asyncio.sleep(0)

        self.assertEqual(len(broadcast.messages), 1)
        msg = broadcast.messages[0]
        assert isinstance(msg, StatusMsg)
        self.assertEqual(msg.text, "hello")

    async def test_publish_from_other_thread_uses_run_coroutine_threadsafe(self) -> None:
        loop = asyncio.get_running_loop()
        broadcast = MockBroadcastFn()
        publisher = ThreadSafePublisher(broadcast, loop)

        def target() -> None:
            publisher.publish(StatusMsg(text="from_thread"))

        threading.Thread(target=target).start()
        _ = await asyncio.wait_for(broadcast.called.wait(), timeout=1.0)

        self.assertEqual(len(broadcast.messages), 1)
        msg = broadcast.messages[0]
        assert isinstance(msg, StatusMsg)
        self.assertEqual(msg.text, "from_thread")

    async def test_schedule_drops_when_loop_closed(self) -> None:
        new_loop = asyncio.new_event_loop()
        broadcast = MockBroadcastFn()
        publisher = ThreadSafePublisher(broadcast, new_loop)
        new_loop.close()

        # Should not raise; message is silently dropped.
        publisher.publish(StatusMsg(text="dropped"))
        self.assertEqual(broadcast.messages, [])

    async def test_schedule_from_same_loop(self) -> None:
        loop = asyncio.get_running_loop()
        broadcast = MockBroadcastFn()
        publisher = ThreadSafePublisher(broadcast, loop)

        async def coro() -> None:
            broadcast.messages.append("scheduled")

        publisher.schedule(coro())
        await asyncio.sleep(0)

        self.assertEqual(broadcast.messages, ["scheduled"])


if __name__ == "__main__":
    _ = unittest.main()
