"""CaptureStage: reads PCM frames from AudioSource into Q1."""

import queue
import time
from typing import TYPE_CHECKING, override

import numpy as np

from app.audio.base import AudioFrame, PipelineStage, put_latest

if TYPE_CHECKING:
    from app.audio.audio_source import AudioSource

_FRAME_MS = 30


class CaptureStage(PipelineStage):
    """Reads realtime PCM frames from AudioSource and puts them into out_q.

    ``AudioReader.read()`` is not required to block for the requested frame
    duration.  The capture boundary therefore enforces realtime cadence so a
    non-blocking or discontinuous device cannot spin the pipeline at CPU speed.

    stop() exits the read loop without emitting a sentinel.
    AudioPipeline injects the sentinel explicitly for full shutdowns.
    """

    def __init__(
        self,
        source: "AudioSource",
        out_q: "queue.Queue[AudioFrame | None]",
    ) -> None:
        super().__init__()
        self._source: AudioSource = source
        self._out_q: queue.Queue[AudioFrame | None] = out_q
        if source.sample_rate <= 0:
            raise ValueError("audio source sample_rate must be positive")
        self._frame_samples: int = max(1, int(source.sample_rate * _FRAME_MS / 1000))
        self._frame_seconds: float = self._frame_samples / source.sample_rate

    @override
    def _run(self) -> None:
        next_frame_at = time.monotonic()
        with self._source.open() as reader:
            while not self._stop_event.is_set():
                mono = reader.read(self._frame_samples)

                remaining = next_frame_at - time.monotonic()
                if remaining > 0 and self._stop_event.wait(remaining):
                    break
                if self._stop_event.is_set():
                    break

                captured_at = time.monotonic()
                pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                put_latest(
                    self._out_q,
                    AudioFrame(
                        pcm=pcm,
                        is_speech=False,
                        timestamp_ms=captured_at * 1000.0,
                    ),
                )
                next_frame_at = captured_at + self._frame_seconds


__all__ = ["CaptureStage"]
