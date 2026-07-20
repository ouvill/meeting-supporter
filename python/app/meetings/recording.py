"""RecordingService — manages WAV recording lifecycle across audio pipelines.

Coordinates start/stop of recording on ``AudioPipelineLike`` instances,
constructs file paths under ``<user_data_dir>/recordings/<meeting_id>/``, and
returns ``RecordingAsset`` records for DB persistence.

Recording failures are logged but not raised — the meeting flow continues.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.meetings.history_models import RecordingAsset, RecordingRole
from app.meetings.models import _new_utterance_id

if TYPE_CHECKING:
    from app.core.protocols import AudioPipelineLike

logger = logging.getLogger(__name__)


class RecordingFinalizationError(RuntimeError):
    """One or more recording pipelines could not be finalised safely."""


class RecordingService:
    """Manages WAV recording across one or two audio pipelines.

    Usage::

        service = RecordingService(user_data_dir)
        await service.start_recording(meeting_id, audio_other, audio_self)
        # ... meeting runs ...
        assets = await service.stop_recording(meeting_id, audio_other, audio_self)
        await history.persist_recording_assets(assets)
    """

    def __init__(self, user_data_dir: Path) -> None:
        self._user_data_dir: Path = user_data_dir

    async def start_recording(
        self,
        meeting_id: str,
        audio_other: AudioPipelineLike | None,
        audio_self: AudioPipelineLike | None,
    ) -> None:
        """Begin WAV recording on all available audio pipelines.

        Creates ``<user_data_dir>/recordings/<meeting_id>/`` and starts
        writing ``other.wav`` and/or ``self.wav``.

        If a pipeline is ``None`` (e.g. self device not configured), it is
        silently skipped.
        """
        recordings_dir = self._user_data_dir / "recordings" / meeting_id
        recordings_dir.mkdir(parents=True, exist_ok=True)

        pairs: list[tuple[RecordingRole, AudioPipelineLike | None]] = [
            ("other", audio_other),
            ("self", audio_self),
        ]
        for role, pipeline in pairs:
            if pipeline is None:
                logger.info("Recording skipped for %s — no pipeline available", role)
                continue
            path = recordings_dir / f"{role}.wav"
            try:
                pipeline.start_recording(path)
                logger.info("Recording started for %s → %s", role, path)
            except Exception:
                logger.exception("Failed to start recording for %s", role)

    async def stop_recording(
        self,
        meeting_id: str,
        audio_other: AudioPipelineLike | None,
        audio_self: AudioPipelineLike | None,
    ) -> list[RecordingAsset]:
        """Finalise WAV recordings and return metadata only on full success.

        Any pipeline stop error is raised after every pipeline has been asked
        to stop. Callers must then persist all returned metadata or compensate
        by removing the meeting recording directory; partial results are never
        silently treated as a completed recording.
        """
        assets: list[RecordingAsset] = []
        failed_roles: list[RecordingRole] = []

        pairs: list[tuple[RecordingRole, AudioPipelineLike | None]] = [
            ("other", audio_other),
            ("self", audio_self),
        ]
        for role, pipeline in pairs:
            if pipeline is None:
                continue
            try:
                result = pipeline.stop_recording()
            except Exception:
                failed_roles.append(role)
                logger.exception("Failed to stop recording for %s", role)
                continue

            if result is None:
                logger.warning("No recording result for %s (was recording started?)", role)
                continue

            asset = RecordingAsset(
                id=_new_utterance_id(),
                meeting_id=meeting_id,
                role=role,
                relative_path=f"recordings/{meeting_id}/{role}.wav",
                format="wav",
                sample_rate=16000,
                channels=1,
                started_at=result.started_at,
                ended_at=result.ended_at,
                size_bytes=result.size_bytes,
            )
            assets.append(asset)
            logger.info(
                "Recording finalised for %s — %d bytes, %s",
                role,
                result.size_bytes,
                result.path,
            )

        if failed_roles:
            raise RecordingFinalizationError(f"Failed to finalise recording roles: {', '.join(failed_roles)}")

        return assets


__all__ = ["RecordingFinalizationError", "RecordingService"]
