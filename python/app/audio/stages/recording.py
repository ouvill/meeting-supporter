"""RecordingStage: drains Qc and writes PCM frames to WAV when recording is active.

    CaptureStage → Q1 → Multiplexer → Qa → VolumeStage
                                     → Qb → SttPipeline
                                     → Qc → RecordingStage  (this stage)

When recording is active, PCM data is written to a WAV file.  When idle, frames
are discarded.  The stage always drains its input queue so Qc does not
accumulate stale frames during non-meeting periods.
"""

from __future__ import annotations

import logging
import queue
import threading
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import override

from app.audio.base import AudioFrame, RecordingResult
from app.core.pipeline import SentinelDrainingStage

logger = logging.getLogger(__name__)

_WAV_SAMPLE_WIDTH = 2  # 16-bit PCM
_WAV_CHANNELS = 1
_WAV_FRAMERATE = 16000


class RecordingStage(SentinelDrainingStage[AudioFrame | None]):
    """Reads from Qc; writes PCM to WAV when recording, discards otherwise.

    Thread-safe: ``start_recording()`` / ``stop_recording()`` can be called
    from any thread (e.g. the async event loop) while ``_run()`` is executing
    on the pipeline thread.
    """

    def __init__(self, in_q: queue.Queue[AudioFrame | None]) -> None:
        super().__init__(in_q)
        self._lock: threading.Lock = threading.Lock()
        self._recording: bool = False
        self._wav_file: wave.Wave_write | None = None
        self._wav_path: Path | None = None
        self._started_at: datetime | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def start_recording(self, path: Path) -> None:
        """Open *path* for WAV writing and begin recording.

        Safe to call when already recording — the previous recording is
        finalised and a new one begins.
        """
        with self._lock:
            self._close_wav_locked()
            path.parent.mkdir(parents=True, exist_ok=True)
            wav = wave.open(str(path), "wb")
            wav.setnchannels(_WAV_CHANNELS)
            wav.setsampwidth(_WAV_SAMPLE_WIDTH)
            wav.setframerate(_WAV_FRAMERATE)
            self._wav_file = wav
            self._wav_path = path
            self._started_at = datetime.now(UTC)
            self._recording = True

    def stop_recording(self) -> RecordingResult | None:
        """Finalise the active WAV file and return recording metadata.

        Returns ``None`` if no recording was active.
        """
        with self._lock:
            if not self._recording or self._wav_file is None or self._wav_path is None or self._started_at is None:
                return None
            self._recording = False
            self._close_wav_locked()
            size_bytes = self._wav_path.stat().st_size
            now = datetime.now(UTC)
            result = RecordingResult(
                path=self._wav_path,
                size_bytes=size_bytes,
                started_at=self._started_at,
                ended_at=now,
            )
            self._wav_file = None
            self._wav_path = None
            self._started_at = None
            return result

    @override
    def stop(self, timeout: float | None = None) -> None:
        """Override to finalise any active recording before stopping the thread."""
        with self._lock:
            self._close_wav_locked()
            self._recording = False
        super().stop(timeout=timeout)

    # ── Internal ───────────────────────────────────────────────────────────────

    @override
    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                frame = self._in_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if frame is None:
                break
            with self._lock:
                if self._recording and self._wav_file is not None:
                    try:
                        self._wav_file.writeframes(frame.pcm)
                    except Exception:
                        logger.exception(
                            "Error writing PCM to WAV %s — disabling recording",
                            self._wav_path,
                        )
                        self._close_wav_locked()
                        self._recording = False

    def _close_wav_locked(self) -> None:
        """Close the WAV file (caller must hold ``_lock``)."""
        if self._wav_file is not None:
            try:
                self._wav_file.close()
            except Exception:
                logger.exception("Error closing WAV file %s", self._wav_path)
            self._wav_file = None


__all__ = ["RecordingStage"]
