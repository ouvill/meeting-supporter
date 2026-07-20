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
    """Reads PCM from AudioSource and puts AudioFrame into out_q.

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
        self._frame_samples: int = int(source.sample_rate * _FRAME_MS / 1000)

    @override
    def _run(self) -> None:
        with self._source.open() as reader:
            while not self._stop_event.is_set():
                mono = reader.read(self._frame_samples)
                pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                put_latest(
                    self._out_q,
                    AudioFrame(
                        pcm=pcm,
                        is_speech=False,
                        timestamp_ms=time.monotonic() * 1000.0,
                    ),
                )


__all__ = ["CaptureStage"]
