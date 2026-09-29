"""Execute Rust lifecycle effects using existing audio, AI, and UI adapters."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import override

from app.core.messages import ErrorMsg, MeetingContextPayload, MeetingStateMsg, ReferenceDocumentPayload, StatusMsg
from app.core.protocols import WebSocketLike
from app.meetings.context_storage import (
    context_from_payload,
    parse_reference_payloads,
    persist_meeting_context,
    persist_reference_documents,
)
from app.meetings.lifecycle import MeetingLifecycleCoordinator
from app.meetings.models import MeetingSession, session_info_msg
from app.meetings.native_session import Notice, Outcome, SessionWorker, SessionWorkerError, Snapshot
from app.meetings.recording_retention import _remove_recording_directory

logger = logging.getLogger(__name__)

_MESSAGES: dict[Notice, str] = {
    "prepare_failed": "音声入力の準備に失敗しました。設定を確認してください。",
    "draft_failed": "会議の開始に失敗しました（保存エラー）。",
    "recording_start_failed": "録音を開始できませんでした。文字起こしは継続します。",
    "speech_failed": "会議の開始に失敗しました（音声認識エラー）。",
    "stop_failed": "会議の停止処理に失敗しました。保存状態を会議履歴で確認してください。",
    "recording_removed": "録音を保存できなかったため、録音ファイルを削除しました。",
    "recording_integrity_failed": "録音の保存または削除に失敗しました。会議履歴から削除して再試行してください。",
    "history_flush_failed": "未保存の会議データがあります。会議履歴を確認してください。",
    "save_failed": "会議の保存に失敗しました。会議履歴を確認してください。",
    "reload_failed": "音声設定を反映できませんでした。設定を確認してください。",
}


class NativeMeetingLifecycleCoordinator(MeetingLifecycleCoordinator):
    """Rust owns transitions and identity; current_session is a compatibility view."""

    _worker: SessionWorker | None = None

    def _session_worker(self) -> SessionWorker:
        if self._worker is None:
            self._worker = SessionWorker()
        return self._worker

    @override
    async def start_meeting(
        self,
        ws: WebSocketLike,
        *,
        meeting_context_payload: MeetingContextPayload | None = None,
        reference_payloads: list[ReferenceDocumentPayload] | None = None,
    ) -> None:
        async with self._audio_lifecycle_lock:
            try:
                snapshot = await self._session_worker().request({"op": "snapshot"})
                if snapshot.phase == "active":
                    await ws.send_json(StatusMsg(text="すでに会議中です").model_dump())
                    return
                snapshot = await self._session_worker().request({"op": "start"})
                await self._drive(snapshot, ws, meeting_context_payload, reference_payloads)
            except BaseException as error:
                await self._connection_failed()
                if isinstance(error, asyncio.CancelledError):
                    raise
                if not isinstance(error, Exception):
                    raise

    @override
    async def stop_meeting(self) -> None:
        async with self._audio_lifecycle_lock:
            if self._worker is None:
                return
            try:
                snapshot = await self._worker.request({"op": "stop"})
                await self._drive(snapshot, None, None, None)
            except BaseException as error:
                await self._connection_failed()
                if isinstance(error, asyncio.CancelledError):
                    raise
                if not isinstance(error, Exception):
                    raise

    @override
    async def close(self) -> None:
        if self._worker is not None:
            await self._worker.close()

    async def _connection_failed(self) -> None:
        # Unknown effects are not replayed. Stop input and preserve the draft/files
        # for inspection; a fresh lifecycle owner must not adopt this session.
        await self.close()
        for cleanup in (self._cancel_replies, self._stt_controller.stop_meeting, self._stt_controller.shutdown_stt):
            try:
                async with asyncio.timeout(10):
                    _ = await cleanup()
            except Exception:
                logger.error("Meeting emergency cleanup failed")
        session = self._state.current_session
        if self._recording is not None and isinstance(session, MeetingSession):
            try:
                async with asyncio.timeout(15):
                    assets = await self._recording.stop_recording(
                        meeting_id=session.id,
                        audio_other=self._stt_controller.audio_other,
                        audio_self=self._stt_controller.audio_self,
                    )
                    if assets:
                        await self._history.persist_recording_assets(assets)
            except Exception:
                logger.error("Meeting emergency recording finalisation failed")
        self._state.current_session = None
        await self._broadcast(ErrorMsg(text=str(SessionWorkerError())))
        await self._broadcast(MeetingStateMsg(running=False))

    async def _drive(
        self,
        snapshot: Snapshot,
        ws: WebSocketLike | None,
        context: MeetingContextPayload | None,
        references: list[ReferenceDocumentPayload] | None,
    ) -> None:
        while snapshot.effect is not None:
            try:
                # Stop/flush can wait for inference, whose own requests are bounded.
                async with asyncio.timeout(150):
                    outcome = await self._effect(snapshot, ws, context, references)
            except Exception:
                logger.error("Meeting lifecycle effect failed: %s", snapshot.effect)
                outcome = "failed"
            snapshot = await self._session_worker().request(
                {
                    "op": "acknowledge",
                    "generation": snapshot.generation,
                    "step": snapshot.step,
                    "outcome": outcome,
                }
            )
        for notice in snapshot.notices:
            await self._broadcast(ErrorMsg(text=_MESSAGES[notice]))
        if snapshot.phase == "faulted":
            await self._connection_failed()
            return
        session = self._state.current_session
        if snapshot.phase == "active":
            if isinstance(session, MeetingSession):
                await self._broadcast(session_info_msg(session))
        elif snapshot.phase == "idle":
            if isinstance(session, MeetingSession) and snapshot.session is not None:
                ended_at = snapshot.session.ended_at
                if ended_at is not None:
                    await self._broadcast(session_info_msg(replace(session, ended_at=ended_at, is_active=False)))
            self._state.current_session = None
            await self._broadcast(MeetingStateMsg(running=False))

    async def _effect(
        self,
        snapshot: Snapshot,
        ws: WebSocketLike | None,
        context: MeetingContextPayload | None,
        references: list[ReferenceDocumentPayload] | None,
    ) -> Outcome:
        metadata = snapshot.session
        if metadata is None:
            raise SessionWorkerError()
        match snapshot.effect:
            case "prepare":
                if not await self._stt_controller.apply_pending_audio_reload():
                    return "failed"
                await self._reset_info_note_updater()
                self._reset_reply_cancel_results()
            case "create_draft":
                session = MeetingSession(
                    id=metadata.id,
                    started_at=metadata.started_at,
                    meeting_context=context_from_payload(context),
                    references=parse_reference_payloads(references or []),
                )
                self._state.current_session = session
                await self._history.create_draft_meeting(session)
                if self._user_data_dir is not None:
                    persist_meeting_context(self._user_data_dir, session.id, session.meeting_context)
                    persist_reference_documents(self._user_data_dir, session.id, session.references)
            case "start_recording":
                if self._recording is not None:
                    await self._recording.start_recording(
                        meeting_id=metadata.id,
                        audio_other=self._stt_controller.audio_other,
                        audio_self=self._stt_controller.audio_self,
                    )
            case "start_speech":
                if ws is None:
                    raise SessionWorkerError()
                return "ok" if await self._stt_controller.start_meeting(ws, session_already_started=True) else "failed"
            case "cancel_replies" | "cancel_final_replies":
                _ = await self._cancel_replies()
                await self._reset_info_note_updater()
            case "stop_speech":
                await self._stt_controller.stop_meeting(require_complete=True, publish_state=False)
            case "finalize_recording":
                if self._recording is None:
                    return "recording_disabled"
                assets = await self._recording.stop_recording(
                    meeting_id=metadata.id,
                    audio_other=self._stt_controller.audio_other,
                    audio_self=self._stt_controller.audio_self,
                )
                if not assets:
                    return "recording_empty"
                await self._history.persist_recording_assets(assets)
                return "recording_saved"
            case "remove_recording":
                if self._user_data_dir is None:
                    return "failed"
                await asyncio.to_thread(_remove_recording_directory, self._user_data_dir, metadata.id)
            case "flush_history":
                await self._history.flush_pending(require_complete=True, meeting_id=metadata.id)
            case "complete_draft":
                session = self._state.current_session
                if not isinstance(session, MeetingSession) or metadata.ended_at is None:
                    raise SessionWorkerError()
                await self._history.complete_meeting(replace(session, ended_at=metadata.ended_at, is_active=False))
            case "abort_draft":
                await self._history.abort_meeting(metadata.id)
            case "reload_audio":
                # Keep the ended view until final publication, but release the
                # existing controller's is_running gate while applying settings.
                session = self._state.current_session
                if isinstance(session, MeetingSession):
                    self._state.current_session = replace(session, ended_at=metadata.ended_at, is_active=False)
                return "ok" if await self._stt_controller.apply_pending_audio_reload() else "failed"
            case _:
                raise SessionWorkerError()
        return "ok"
