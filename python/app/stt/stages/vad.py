"""VadStage: annotates each frame with is_speech without filtering any frames."""

import queue
from typing import Protocol, override

from app.audio.base import AudioFrame, PipelineStage, put_latest


class VadEngine(Protocol):
    def is_speech(self, frame: bytes, sample_rate: int) -> bool: ...


class WebRtcVadEngine:
    """webrtcvad-backed VAD engine (MVP default)."""

    def __init__(self, aggressiveness: int) -> None:
        import webrtcvad

        self._vad: webrtcvad.Vad = webrtcvad.Vad(aggressiveness)

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        try:
            return self._vad.is_speech(frame, sample_rate)
        except Exception:
            return False


class VadStage(PipelineStage):
    """Reads frames from in_q, stamps is_speech, and forwards all frames to out_q.

    The engine is swappable at construction time (WebRtcVadEngine or future alternatives).
    All frames pass through — STT stages decide how to use the is_speech flag.
    """

    def __init__(
        self,
        in_q: queue.Queue[AudioFrame | None],
        out_q: queue.Queue[AudioFrame | None],
        engine: VadEngine,
        sample_rate: int,
    ) -> None:
        super().__init__()
        self._in_q: queue.Queue[AudioFrame | None] = in_q
        self._out_q: queue.Queue[AudioFrame | None] = out_q
        self._engine: VadEngine = engine
        self._sample_rate: int = sample_rate

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
            put_latest(
                self._out_q,
                AudioFrame(
                    pcm=frame.pcm,
                    is_speech=self._engine.is_speech(frame.pcm, self._sample_rate),
                    timestamp_ms=frame.timestamp_ms,
                ),
            )


__all__ = ["VadEngine", "VadStage", "WebRtcVadEngine"]
