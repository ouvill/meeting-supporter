"""DiarizationStage: pass-through stub for future speaker diarization."""

import queue
from typing import override

from app.audio.base import AudioFrame, PipelineStage, put_latest


class DiarizationStage(PipelineStage):
    """Placeholder — forwards frames unchanged until a diarization engine is integrated."""

    def __init__(
        self,
        in_q: queue.Queue[AudioFrame | None],
        out_q: queue.Queue[AudioFrame | None],
    ) -> None:
        super().__init__()
        self._in_q: queue.Queue[AudioFrame | None] = in_q
        self._out_q: queue.Queue[AudioFrame | None] = out_q

    @override
    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                frame = self._in_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if frame is None:
                self._out_q.put(None)
                break
            put_latest(self._out_q, frame)


__all__ = ["DiarizationStage"]
