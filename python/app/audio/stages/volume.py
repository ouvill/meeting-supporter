"""VolumeStage: computes peak audio level and broadcasts audio_level events."""

import queue
from typing import cast, override

import numpy as np

from app.audio.base import AudioFrame, PipelineStage
from app.core.messages import AudioLevelMsg
from app.core.publisher import OutgoingPublisher

_FRAME_MS = 30
_DEFAULT_LEVEL_INTERVAL_MS = 120


class VolumeStage(PipelineStage):
    """Consumes frames from Qa and emits audio_level WebSocket events."""

    def __init__(
        self,
        in_q: "queue.Queue[AudioFrame | None]",
        role: str,
        publisher: OutgoingPublisher,
        level_interval_ms: int = _DEFAULT_LEVEL_INTERVAL_MS,
    ) -> None:
        super().__init__()
        self._in_q: queue.Queue[AudioFrame | None] = in_q
        self._role: str = role
        self._publisher: OutgoingPublisher = publisher
        self._ticks_per_level: int = max(1, level_interval_ms // _FRAME_MS)

    @override
    def _run(self) -> None:
        tick = 0
        window_peak = 0.0
        while not self._stop_event.is_set():
            try:
                frame = self._in_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if frame is None:
                break
            pcm_np = np.frombuffer(frame.pcm, dtype=np.int16).astype(np.float32) / 32767.0
            if pcm_np.size:
                abs_pcm: np.ndarray = np.abs(pcm_np)
                peak = cast(np.ndarray, abs_pcm.max())
                window_peak = max(window_peak, float(peak))
            tick += 1
            if tick % self._ticks_per_level == 0:
                self._publisher.publish(AudioLevelMsg(role=self._role, level=window_peak))
                window_peak = 0.0


__all__ = ["VolumeStage"]
