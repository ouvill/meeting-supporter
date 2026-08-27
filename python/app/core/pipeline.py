"""Shared pipeline infrastructure for audio and STT stages.

Provides Stage (thread-backed unit), Pipeline (chain controller), and helpers
for building linear queue-driven pipelines without scattering queue logic.
"""

from __future__ import annotations

import queue
import threading
from abc import ABC, abstractmethod


class Stage(ABC):
    """Thread-backed pipeline stage with a clear lifecycle.

    Subclasses implement ``_run()``.  ``stop()`` sets an internal event and
    joins the thread.  It is safe to call ``start()`` / ``stop()`` multiple
    times.
    """

    def __init__(self) -> None:
        self._stop_event: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop_event.set()
        self.join(timeout=timeout)

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    @abstractmethod
    def _run(self) -> None: ...


class SentinelDrainingStage[T](Stage, ABC):
    """A stage that reads from a queue and treats ``None`` as a sentinel."""

    def __init__(self, in_q: queue.Queue[T]) -> None:
        super().__init__()
        self._in_q: queue.Queue[T] = in_q


class Pipeline[T]:
    """Manages a linear chain of :class:`Stage` instances.

    Stages are started in declaration order and stopped in reverse order.
    When stopping, ``None`` sentinels can be injected into *owned* input
    queues so that stages drain gracefully.  Callers must **not** inject
    sentinels into queues owned by another pipeline (e.g. the shared
    ``stt_queue`` between AudioPipeline and SttPipeline).
    """

    def __init__(
        self,
        stages: list[Stage],
        *,
        input_queues: list[queue.Queue[T]] | None = None,
    ) -> None:
        self._stages: list[Stage] = list(stages)
        self._input_queues: list[queue.Queue[T]] = list(input_queues) if input_queues else []

    def start(self) -> None:
        started: list[Stage] = []
        try:
            for stage in self._stages:
                stage.start()
                started.append(stage)
        except Exception:
            for stage in reversed(started):
                stage.stop()
            raise

    def stop(self, *, timeout: float | None = None, inject_sentinels: bool = True) -> None:
        if inject_sentinels:
            for q in self._input_queues:
                try:
                    q.put_nowait(None)  # pyright: ignore[reportArgumentType]
                except queue.Full:
                    pass
        for stage in reversed(self._stages):
            stage.stop(timeout=timeout)
        for q in self._input_queues:
            self._drain(q)

    @property
    def stages(self) -> list[Stage]:
        return list(self._stages)

    @staticmethod
    def _drain(q: queue.Queue[T]) -> None:
        while not q.empty():
            try:
                _ = q.get_nowait()
            except queue.Empty:
                break


__all__ = ["Pipeline", "SentinelDrainingStage", "Stage"]
