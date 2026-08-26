"""VolumeStage: computes peak audio level and broadcasts audio_level events."""

import queue
import time
from typing import cast, override

import numpy as np

from app.audio.base import AudioFrame, PipelineStage
from app.core.messages import AudioLevelMsg
from app.core.publisher import OutgoingPublisher

_DEFAULT_LEVEL_INTERVAL_MS = 120


class VolumeStage(PipelineStage):
    """Emits peak audio levels at a wall-clock interval."""

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
        self._level_interval_seconds: float = max(1, level_interval_ms) / 1000.0

    @override
    def _run(self) -> None:
        last_published_at = time.monotonic()
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

            now = time.monotonic()
            if now - last_published_at >= self._level_interval_seconds:
                self._publisher.publish(AudioLevelMsg(role=self._role, level=window_peak))
                window_peak = 0.0
                last_published_at = now


__all__ = ["VolumeStage"]
