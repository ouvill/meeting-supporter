"""Tests for app.core.pipeline — Stage and Pipeline lifecycle."""

import queue
import threading
import time
import unittest
from typing import override

from app.core.pipeline import Pipeline, SentinelDrainingStage, Stage


class MockStage(Stage):
    """A Stage that counts loop iterations and respects _stop_event."""

    def __init__(self, *, name: str = "mock", delay_s: float = 0.01) -> None:
        super().__init__()
        self._name: str = name
        self._delay_s: float = delay_s
        self.iterations: int = 0
        self.stopped: bool = False
        self.started_event: threading.Event = threading.Event()
        self.stopped_event: threading.Event = threading.Event()

    @override
    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.iterations += 1
            self.started_event.set()
            time.sleep(self._delay_s)
        self.stopped = True
        self.stopped_event.set()


class CountingStage(SentinelDrainingStage[int | None]):
    """A SentinelDrainingStage that counts items until None sentinel."""

    def __init__(self, in_q: queue.Queue[int | None]) -> None:
        super().__init__(in_q)
        self.items: list[int] = []

    @override
    def _run(self) -> None:
        while True:
            try:
                item: int | None = self._in_q.get(timeout=0.5)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            if item is None:
                break
            self.items.append(item)


class StageTest(unittest.TestCase):
    def test_start_spawns_thread_and_sets_running(self) -> None:
        stage = MockStage()
        self.assertFalse(stage.running)
        stage.start()
        self.assertTrue(stage.running)
        stage.stop()
        self.assertFalse(stage.running)
        self.assertTrue(stage.stopped)

    def test_idempotent_start(self) -> None:
        stage = MockStage()
        stage.start()
        first_thread = stage._thread  # pyright: ignore[reportPrivateUsage]
        stage.start()  # should be no-op
        self.assertIs(stage._thread, first_thread)  # pyright: ignore[reportPrivateUsage]
        stage.stop()

    def test_join_without_start_is_safe(self) -> None:
        stage = MockStage()
        stage.join()  # should not raise
        self.assertIsNone(stage._thread)  # pyright: ignore[reportPrivateUsage]

    def test_stop_sets_stop_event_and_joins(self) -> None:
        stage = MockStage(delay_s=0.001)
        stage.start()
        self.assertTrue(stage.started_event.wait(timeout=1))
        self.assertGreater(stage.iterations, 0)
        stage.stop()
        self.assertFalse(stage.running)
        self.assertTrue(stage.stopped)
        self.assertTrue(stage.stopped_event.is_set())


class PipelineTest(unittest.TestCase):
    def test_starts_stages_in_order(self) -> None:
        order: list[str] = []
        lock = threading.Lock()

        class OrderStage(Stage):
            def __init__(self, name: str) -> None:
                super().__init__()
                self._name: str = name
                self.started: threading.Event = threading.Event()

            @override
            def _run(self) -> None:
                self.started.set()
                with lock:
                    order.append(self._name)
                while not self._stop_event.is_set():
                    time.sleep(0.01)

        s1 = OrderStage("a")
        s2 = OrderStage("b")
        pipeline = Pipeline[object]([s1, s2])
        pipeline.start()
        self.assertTrue(s1.started.wait(timeout=1))
        self.assertTrue(s2.started.wait(timeout=1))
        pipeline.stop()

        self.assertEqual(order, ["a", "b"])

    def test_stops_stages_in_reverse_order(self) -> None:
        order: list[str] = []
        lock = threading.Lock()

        class OrderStage(Stage):
            def __init__(self, name: str) -> None:
                super().__init__()
                self._name: str = name
                self.started: threading.Event = threading.Event()
                self.stopped: threading.Event = threading.Event()

            @override
            def _run(self) -> None:
                self.started.set()
                while not self._stop_event.is_set():
                    time.sleep(0.01)
                with lock:
                    order.append(self._name)
                self.stopped.set()

        s1 = OrderStage("a")
        s2 = OrderStage("b")
        pipeline = Pipeline[object]([s1, s2])
        pipeline.start()
        self.assertTrue(s1.started.wait(timeout=1))
        self.assertTrue(s2.started.wait(timeout=1))
        pipeline.stop()

        self.assertEqual(order, ["b", "a"])
        self.assertTrue(s1.stopped.is_set())
        self.assertTrue(s2.stopped.is_set())

    def test_inject_sentinels_drains_queues(self) -> None:
        q: queue.Queue[int | None] = queue.Queue()
        q.put(1)
        q.put(2)
        q.put(3)

        stage = CountingStage(q)
        pipeline = Pipeline[int | None]([stage], input_queues=[q])
        pipeline.start()
        pipeline.stop(inject_sentinels=True)

        self.assertEqual(stage.items, [1, 2, 3])
        self.assertTrue(q.empty())

    def test_no_sentinels_drains_queue_after_stop(self) -> None:
        """Even without sentinel injection, Pipeline.stop() drains input queues."""
        q: queue.Queue[int | None] = queue.Queue()
        q.put(1)
        q.put(2)

        stage = CountingStage(q)
        pipeline = Pipeline[int | None]([stage], input_queues=[q])
        pipeline.start()
        pipeline.stop(inject_sentinels=False)

        # Queue is drained unconditionally after stopping stages.
        self.assertTrue(q.empty())

    def test_drain_removes_all_items(self) -> None:
        q: queue.Queue[int] = queue.Queue()
        for i in range(5):
            q.put(i)
        Pipeline[int]._drain(q)  # pyright: ignore[reportPrivateUsage]
        self.assertTrue(q.empty())

    def test_stages_property_returns_copy(self) -> None:
        s = MockStage()
        pipeline = Pipeline[object]([s])
        first = pipeline.stages
        second = pipeline.stages
        self.assertEqual(first, [s])
        self.assertIsNot(first, second)


if __name__ == "__main__":
    _ = unittest.main()
