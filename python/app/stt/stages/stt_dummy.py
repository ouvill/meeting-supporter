"""DummyStage: deterministic no-op STT for local smoke tests."""

from __future__ import annotations

import queue
from typing import override

from app.audio.base import AudioFrame, PipelineStage
from app.core.config import SttConfig
from app.core.messages import SttInterimMsg
from app.core.publisher import OutgoingPublisher
from app.core.types import HandleSpeechFn

_FRAME_MS = 30
_DUMMY_TRANSCRIPT = "これはダミーSTTのテスト発話です"


class DummyStage(PipelineStage):
    """Consumes VAD frames and emits a deterministic transcript per speech segment.

    This backend exists for development and VM smoke tests. It never loads an STT
    model and never contacts a network service, so it can exercise meeting
    lifecycle, WebSocket fan-out, history writes, and assistant-window behavior
    without Whisper/Deepgram/remote-STT prerequisites.
    """

    def __init__(
        self,
        in_q: queue.Queue[AudioFrame | None],
        cfg: SttConfig,
        role: str,
        publisher: OutgoingPublisher,
        handle_speech_fn: HandleSpeechFn,
    ) -> None:
        super().__init__()
        self._in_q: queue.Queue[AudioFrame | None] = in_q
        self._cfg: SttConfig = cfg
        self._role: str = role
        self._publisher: OutgoingPublisher = publisher
        self._handle_speech: HandleSpeechFn = handle_speech_fn

    @override
    def _run(self) -> None:
        silence_threshold = max(1, int(float(self._cfg.silence_duration) * 1000 / _FRAME_MS))
        min_voiced_frames = max(1, int(self._cfg.min_voiced_ms / _FRAME_MS))

        in_speech = False
        voiced_frames = 0
        silence_frames = 0

        while not self._stop_event.is_set():
            try:
                frame = self._in_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if frame is None:
                if in_speech and voiced_frames >= min_voiced_frames:
                    self._emit_final()
                break

            if frame.is_speech:
                voiced_frames += 1
                silence_frames = 0
                if not in_speech:
                    in_speech = True
                    self._publisher.publish(SttInterimMsg(role=self._role, text="…"))
            elif in_speech:
                silence_frames += 1
                if silence_frames >= silence_threshold:
                    if voiced_frames >= min_voiced_frames:
                        self._emit_final()
                    self._publisher.publish(SttInterimMsg(role=self._role, text=""))
                    in_speech = False
                    voiced_frames = 0
                    silence_frames = 0

    def _emit_final(self) -> None:
        self._publisher.schedule(self._handle_speech(self._role, _DUMMY_TRANSCRIPT))


__all__ = ["DummyStage"]
