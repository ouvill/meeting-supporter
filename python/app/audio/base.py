"""Shared types and base class for audio pipeline stages.

.. deprecated::
    ``PipelineStage`` lives in ``app.core.pipeline`` now; this module re-exports
    it for backward compatibility.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.pipeline import Pipeline as Pipeline
from app.core.pipeline import SentinelDrainingStage as SentinelDrainingStage
from app.core.pipeline import Stage as PipelineStage


@dataclass
class AudioFrame:
    pcm: bytes
    is_speech: bool
    timestamp_ms: float


@dataclass(frozen=True)
class RecordingResult:
    """Result returned by ``AudioPipeline.stop_recording()``."""

    path: Path
    size_bytes: int
    started_at: datetime
    ended_at: datetime


def put_latest(q: queue.Queue[AudioFrame | None], item: AudioFrame | None) -> None:
    """Non-blocking put; drops the oldest entry when queue is full."""
    try:
        q.put_nowait(item)
    except queue.Full:
        try:
            _ = q.get_nowait()
        except queue.Empty:
            return
        try:
            q.put_nowait(item)
        except queue.Full:
            # Another producer refilled the queue between get and put.
            # Dropping this frame is safer than terminating the capture thread.
            pass


def put_or_drop(q: queue.Queue[AudioFrame | None], item: AudioFrame | None) -> None:
    """Non-blocking put; drops the *new* item when queue is full (drop-new policy).

    Unlike ``put_latest``, this does NOT discard an existing entry — it simply
    discards the incoming item.  Useful for lower-priority fan-out queues where
    losing a frame is preferable to overwriting a buffered one.
    """
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


__all__ = [
    "AudioFrame",
    "PipelineStage",
    "RecordingResult",
    "SentinelDrainingStage",
    "put_latest",
    "put_or_drop",
]
