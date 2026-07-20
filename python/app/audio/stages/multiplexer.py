"""Multiplexer: fan-out from Q1 to multiple downstream queues."""

import queue
from typing import override

from app.audio.base import AudioFrame, PipelineStage, put_latest, put_or_drop


class Multiplexer(PipelineStage):
    """Copies each AudioFrame from in_q into all out_queues.

    Sentinel (None) is forwarded to every out_queue before the stage exits.
    Hot-swap stop via _stop_event does not emit a sentinel.

    *drop_new_at_indices* — a set of output queue indices that should use the
    drop-new policy (``put_or_drop``) instead of the default drop-oldest
    (``put_latest``).  Sentinel forwarding always uses ``put_latest`` to
    guarantee it reaches every queue.
    """

    def __init__(
        self,
        in_q: "queue.Queue[AudioFrame | None]",
        *out_queues: "queue.Queue[AudioFrame | None]",
        drop_new_at_indices: set[int] | None = None,
    ) -> None:
        super().__init__()
        self._in_q: queue.Queue[AudioFrame | None] = in_q
        self._out_queues: tuple[queue.Queue[AudioFrame | None], ...] = out_queues
        self._drop_new_indices: set[int] = drop_new_at_indices or set()

    @override
    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                frame = self._in_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if frame is None:
                for q in self._out_queues:
                    put_latest(q, None)
                break
            for i, q in enumerate(self._out_queues):
                if i in self._drop_new_indices:
                    put_or_drop(q, frame)
                else:
                    put_latest(q, frame)


__all__ = ["Multiplexer"]
