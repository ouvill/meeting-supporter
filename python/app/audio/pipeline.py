"""AudioPipeline: audio capture, volume monitoring, and recording.

Runs continuously while a device is selected — independent of whether STT is active.
Exposes stt_queue (Qb) for SttPipeline to consume and recording_queue (Qc) for
RecordingStage.

    CaptureStage → Q1 → Multiplexer → Qa → VolumeStage (audio_level events)
                                      → Qb → SttPipeline (attached externally)
                                      → Qc → RecordingStage (WAV recording)
"""

from __future__ import annotations

import asyncio
import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from app.audio.base import AudioFrame, RecordingResult
from app.audio.stages.capture import CaptureStage
from app.audio.stages.multiplexer import Multiplexer
from app.audio.stages.recording import RecordingStage
from app.audio.stages.volume import VolumeStage
from app.core.messages import OutgoingBroadcastFn, StreamInfoMsg
from app.core.pipeline import Pipeline
from app.core.publisher import ThreadSafePublisher

if TYPE_CHECKING:
    from app.audio.audio_source import AudioSource

_Q1_SIZE = 50
_QA_SIZE = 50
_QB_SIZE = 50
_QC_SIZE = 500


class AudioPipeline:
    """CaptureStage + Multiplexer + VolumeStage + RecordingStage.

    stt_queue (Qb) is a persistent Queue that SttPipeline reads from.
    Call flush_stt_queue() before attaching a new SttPipeline to discard
    any stale frames from a previous session.

    recording_queue (Qc) uses a drop-new policy — if Qc is full, incoming
    frames are discarded rather than overwriting buffered ones.  This prevents
    recording from starving the STT or volume paths.
    """

    def __init__(
        self,
        source: AudioSource,
        role: str,
        broadcast_fn: OutgoingBroadcastFn,
    ) -> None:
        self._source: AudioSource = source
        self._role: str = role
        self._broadcast: OutgoingBroadcastFn = broadcast_fn
        self._lock: threading.Lock = threading.Lock()
        self._started: bool = False

        self._q1: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=_Q1_SIZE)
        self._qa: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=_QA_SIZE)
        self._qb: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=_QB_SIZE)
        self._qc: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=_QC_SIZE)
        self._recording_stage: RecordingStage | None = None

        self._pipeline: Pipeline[AudioFrame | None] | None = None

    @property
    def stt_queue(self) -> queue.Queue[AudioFrame | None]:
        return self._qb

    @property
    def recording_queue(self) -> queue.Queue[AudioFrame | None]:
        return self._qc

    @property
    def source(self) -> AudioSource:
        return self._source

    def flush_stt_queue(self) -> None:
        """Drain Qb before a new SttPipeline attaches."""
        while not self._qb.empty():
            try:
                _ = self._qb.get_nowait()
            except queue.Empty:
                break

    def start_recording(self, path: Path) -> None:
        """Begin writing PCM frames from Qc into *path* as WAV.

        Safe to call when already recording — the previous recording is
        finalised and a new one begins.
        """
        if self._recording_stage is not None:
            self._recording_stage.start_recording(path)

    def stop_recording(self) -> RecordingResult | None:
        """Finalise the active WAV recording.

        Returns metadata (path, size, timestamps) or ``None`` if no recording
        was active.
        """
        if self._recording_stage is not None:
            return self._recording_stage.stop_recording()
        return None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            publisher = ThreadSafePublisher(self._broadcast, loop)
            self._q1 = queue.Queue(maxsize=_Q1_SIZE)
            self._qa = queue.Queue(maxsize=_QA_SIZE)
            self._qb = queue.Queue(maxsize=_QB_SIZE)
            self._qc = queue.Queue(maxsize=_QC_SIZE)
            capture = CaptureStage(self._source, self._q1)
            mux = Multiplexer(
                self._q1,
                self._qa,
                self._qb,
                self._qc,
                drop_new_at_indices={2},
            )
            self._recording_stage = RecordingStage(self._qc)
            volume = VolumeStage(self._qa, self._role, publisher)
            self._pipeline = Pipeline[AudioFrame | None](
                [capture, mux, self._recording_stage, volume],
                input_queues=[self._q1],
            )
            self._pipeline.start()
        _ = publisher.publish(StreamInfoMsg(role=self._role, device=self._source.name, rate=self._source.sample_rate))

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
            if self._pipeline is not None:
                self._pipeline.stop(timeout=2)
                self._pipeline = None
                self._recording_stage = None


__all__ = ["AudioPipeline"]
