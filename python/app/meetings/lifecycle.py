"""MeetingLifecycleCoordinator — thin coordination for WebSocket start/stop.

Phase 4: recording lifecycle integrated into start/stop sequence.

Sequence (start):
  1. If already running, delegate to STT (no new draft/recording).
  2. Create session, set in AppState.
  3. Persist draft record.
  4. **Start recording** (WAV pipelines).
  5. Start STT.
  6. On recording failure — log, continue (non-fatal).
  7. On draft failure — clear session, broadcast error (no STT/recording).
  8. On STT failure — abort draft, stop recording, clear session.

Sequence (stop):
  1. Cancel in-flight reply generations.
  2. Stop STT, then cancel any generation raced by its final event.
  3. **Stop recording**, persist ``RecordingAsset`` rows.
  4. Flush pending turn / suggestion persistence tasks.
  5. Persist meeting completion.
  6. Broadcast final session info, clear state.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from app.core.messages import (
    ErrorMsg,
    MeetingContextPayload,
    MeetingStateMsg,
    OutgoingBroadcastFn,
    ReferenceDocumentPayload,
)
from app.core.protocols import ConversationState, WebSocketLike
from app.meetings.context_storage import (
    context_from_payload,
    parse_reference_payloads,
    persist_meeting_context,
    persist_reference_documents,
)
from app.meetings.models import MeetingSession, _new_utterance_id, session_info_msg
from app.meetings.recording_retention import RecordingCleanupError, _remove_recording_directory
from app.meetings.service import MeetingHistoryService
from app.services.stt_controller import SttController

if TYPE_CHECKING:
    from app.meetings.recording import RecordingService

logger = logging.getLogger(__name__)


class MeetingLifecycleCoordinator:
    """Coordinates the start/stop sequence of a meeting.

    Dependencies:
        state          — AppState (mutable, holds current session)
        stt_controller — owns STT stream lifecycle
        broadcast      — sends typed messages to all connected clients
        history        — persists meeting records
        recording      — manages WAV recording lifecycle (optional)
    """

    def __init__(
        self,
        state: ConversationState,
        stt_controller: SttController,
        broadcast: OutgoingBroadcastFn,
        history: MeetingHistoryService,
        cancel_replies: Callable[[], Awaitable[object]],
        reset_reply_cancel_results: Callable[[], None],
        reset_info_note_updater: Callable[[], Awaitable[None]],
        recording: RecordingService | None = None,
        user_data_dir: Path | None = None,
    ) -> None:
        self._state: ConversationState = state
        self._audio_lifecycle_lock: asyncio.Lock = cast(asyncio.Lock, getattr(state, "audio_lifecycle_lock"))
        self._stt_controller: SttController = stt_controller
        self._broadcast: OutgoingBroadcastFn = broadcast
        self._history: MeetingHistoryService = history
        self._cancel_replies: Callable[[], Awaitable[object]] = cancel_replies
        self._reset_reply_cancel_results: Callable[[], None] = reset_reply_cancel_results
        self._reset_info_note_updater: Callable[[], Awaitable[None]] = reset_info_note_updater
        self._recording: RecordingService | None = recording
        self._user_data_dir: Path | None = user_data_dir

    async def start_meeting(
        self,
        ws: WebSocketLike,
        *,
        meeting_context_payload: MeetingContextPayload | None = None,
        reference_payloads: list[ReferenceDocumentPayload] | None = None,
    ) -> None:
        """Serialize and start a new meeting."""
        async with self._audio_lifecycle_lock:
            await self._start_meeting_locked(
                ws,
                meeting_context_payload=meeting_context_payload,
                reference_payloads=reference_payloads,
            )

    async def _start_meeting_locked(
        self,
        ws: WebSocketLike,
        *,
        meeting_context_payload: MeetingContextPayload | None = None,
        reference_payloads: list[ReferenceDocumentPayload] | None = None,
    ) -> None:
        """Start a new meeting following ADR-003 Phase 4 order.

        1. If already running, delegate to STT controller (no new draft).
        2. Apply any pending audio reload, refusing the start if it still fails.
        3. Create a new ``MeetingSession`` and set it in ``AppState``.
        4. Persist a draft record via ``MeetingHistoryService``.
        5. Start recording (WAV) — non-fatal if it fails.
        6. Start STT via ``SttController.start_meeting(…, session_already_started=True)``.
        7. On draft failure — clear session, broadcast error (STT/recording skipped).
        8. On STT failure — abort draft, stop recording cleanup, clear session.
        9. Broadcast ``SessionInfoMsg``.
        """
        # Phase 1a — already running → delegate without creating a new draft.
        if self._state.is_running:
            _ = await self._stt_controller.start_meeting(ws)
            return
        # Phase 1b — this gate must precede draft creation.  A failed audio
        # replacement may have restarted a pipeline with a new queue while a
        # preserved prewarmed STT stream still references the old queue.
        if not await self._stt_controller.apply_pending_audio_reload():
            logger.error("Pending audio subsystem reload failed — refusing to start a new meeting")
            return

        await self._reset_info_note_updater()
        self._reset_reply_cancel_results()
        # Phase 1c — create session and set in AppState.
        meeting_context = context_from_payload(meeting_context_payload)
        references = parse_reference_payloads(reference_payloads or [])
        session = MeetingSession(
            id=_new_utterance_id(),
            started_at=datetime.now(UTC),
            meeting_context=meeting_context,
            references=references,
        )
        self._state.current_session = session

        # Phase 1d — persist draft record and meeting-scoped context/reference files.
        try:
            await self._history.create_draft_meeting(session)
            if self._user_data_dir is not None:
                persist_meeting_context(self._user_data_dir, session.id, meeting_context)
                persist_reference_documents(self._user_data_dir, session.id, references)
        except Exception:
            logger.exception("Failed to persist draft meeting %s — aborting", session.id)
            self._state.current_session = None
            await self._broadcast(ErrorMsg(text="会議の開始に失敗しました（データベースエラー）"))
            return

        # Phase 1e — start recording (non-fatal).
        if self._recording is not None:
            try:
                await self._recording.start_recording(
                    meeting_id=session.id,
                    audio_other=self._stt_controller.audio_other,
                    audio_self=self._stt_controller.audio_self,
                )
            except Exception:
                logger.exception("Recording start failed (non-fatal) for meeting %s", session.id)

        # Phase 1f — start STT (session is already set, skip is_running guard).
        stt_ok = await self._stt_controller.start_meeting(ws, session_already_started=True)
        if not stt_ok:
            logger.error("STT start 失敗 after draft creation — aborting meeting %s", session.id)
            assets_persisted = False
            cleanup_failed = False
            if self._recording is not None:
                try:
                    assets = await self._recording.stop_recording(
                        meeting_id=session.id,
                        audio_other=self._stt_controller.audio_other,
                        audio_self=self._stt_controller.audio_self,
                    )
                    if assets:
                        await self._history.persist_recording_assets(assets)
                        assets_persisted = True
                except Exception:
                    logger.exception("Recording finalisation during STT-start abort failed for %s", session.id)

                # An aborted meeting may retain only assets whose metadata was
                # durably saved.  Otherwise remove the entire meeting directory
                # before changing its DB state.
                if not assets_persisted:
                    if self._user_data_dir is None:
                        cleanup_failed = True
                        logger.error("Cannot clean recording files without a user data directory")
                    else:
                        try:
                            await asyncio.to_thread(_remove_recording_directory, self._user_data_dir, session.id)
                        except RecordingCleanupError:
                            cleanup_failed = True
                            logger.exception("Recording cleanup during STT-start abort failed for %s", session.id)
            if not cleanup_failed:
                try:
                    await self._history.abort_meeting(session.id)
                except Exception:
                    logger.exception("Failed to abort meeting %s after STT start failure", session.id)
            else:
                await self._broadcast(
                    ErrorMsg(text="録音の保存または削除に失敗しました。会議履歴から削除して再試行してください。")
                )
            self._state.current_session = None
            await self._broadcast(ErrorMsg(text="会議の開始に失敗しました（STTエラー）"))
            await self._broadcast(MeetingStateMsg(running=False))
            return

        # Phase 1g — broadcast session info.
        await self._broadcast(session_info_msg(session))

    async def stop_meeting(self) -> None:
        """Serialize and stop the active meeting."""
        async with self._audio_lifecycle_lock:
            await self._stop_meeting_locked()

    async def _stop_meeting_locked(self) -> None:
        """Stop STT, finalise recording, persist completion, broadcast, clear state."""
        _ = await self._cancel_replies()
        await self._reset_info_note_updater()
        # Phase 2a — stop STT first (no new audio frames being processed).
        await self._stt_controller.stop_meeting()
        _ = await self._cancel_replies()
        await self._reset_info_note_updater()

        # Phase 2b — stop recording and persist asset rows.
        session = self._state.current_session
        session_obj = cast(MeetingSession, session) if session is not None else None
        session_id: str | None = session_obj.id if session_obj is not None else None

        recording_compensated = False
        recording_integrity_failed = False
        if self._recording is not None and session_id is not None:
            try:
                assets = await self._recording.stop_recording(
                    meeting_id=session_id,
                    audio_other=self._stt_controller.audio_other,
                    audio_self=self._stt_controller.audio_self,
                )
                if assets:
                    await self._history.persist_recording_assets(assets)
                elif self._user_data_dir is not None:
                    # Starting a recorder creates its directory even when no
                    # pipeline produced an asset. Do not leave that untracked
                    # directory behind after an otherwise normal completion.
                    await asyncio.to_thread(_remove_recording_directory, self._user_data_dir, session_id)
                else:
                    recording_integrity_failed = True
                    logger.error("Cannot verify an empty recording directory without a user data directory")
            except Exception:
                logger.exception("Recording finalisation failed for meeting %s", session_id)
                if self._user_data_dir is None:
                    recording_integrity_failed = True
                    logger.error("Cannot compensate recording failure without a user data directory")
                else:
                    try:
                        await asyncio.to_thread(_remove_recording_directory, self._user_data_dir, session_id)
                        recording_compensated = True
                    except RecordingCleanupError:
                        recording_integrity_failed = True
                        logger.exception("Recording cleanup compensation failed for meeting %s", session_id)

        # Phase 2c — ensure real-time turn/suggestion writes finish before completion.
        await self._history.flush_pending()

        # Phase 2d — persist completion only when final recording state is
        # consistent.  If compensation could not remove untracked audio, leave
        # the draft row and files together for the existing manual deletion
        # flow rather than silently publishing a completed mismatch.
        if session is not None:
            ended_session = cast(MeetingSession, session).ended()
            if recording_integrity_failed:
                await self._broadcast(
                    ErrorMsg(text="録音の保存または削除に失敗しました。会議履歴から削除して再試行してください。")
                )
            else:
                try:
                    await self._history.complete_meeting(ended_session)
                except Exception:
                    logger.exception("Failed to persist completion for meeting %s", ended_session.id)
                    await self._broadcast(ErrorMsg(text="会議の保存に失敗しました"))
                if recording_compensated:
                    await self._broadcast(ErrorMsg(text="録音を保存できなかったため、録音ファイルを削除しました。"))

            # Broadcast ended-session info before clearing — the frontend
            # needs the final is_active=False / ended_at in this message.
            await self._broadcast(session_info_msg(ended_session))

        self._state.current_session = None
        _ = await self._stt_controller.apply_pending_audio_reload()


__all__ = ["MeetingLifecycleCoordinator"]
