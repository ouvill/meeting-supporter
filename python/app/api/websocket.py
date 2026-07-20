"""WebSocket endpoint and message dispatcher."""

import asyncio
import logging
import traceback
from collections.abc import Callable
from typing import cast

logger = logging.getLogger(__name__)

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.core.config import _build_agent_settings_message
from app.core.local_auth import (
    find_authorized_websocket_subprotocol,
    get_backend_auth_token,
    is_origin_allowed,
)
from app.core.messages import (
    AiNoteUpdatedMsg,
    CancelReplyMsg,
    DevicesListMsg,
    ErrorMsg,
    GenerateReplyMsg,
    HistoryResetMsg,
    IncomingMessage,
    InitSttMsg,
    ManualSpeechMsg,
    MeetingStateMsg,
    ReloadContextMsg,
    ReplyAgentSettingsItem,
    RunInfoMsg,
    SetDeviceMsg,
    ShutdownSttMsg,
    StartMeetingMsg,
    StatusMsg,
    StopMeetingMsg,
    SttStateMsg,
    TurnItem,
    UserReplyMsg,
    _incoming_ta,
)
from app.core.state import AppState
from app.core.types import InputDevice
from app.meetings.lifecycle import MeetingLifecycleCoordinator
from app.meetings.models import session_info_msg
from app.services.broadcast import BroadcastManager
from app.services.conversation_orchestrator import ConversationOrchestrator
from app.services.stt_controller import SttController


def create_router(
    *,
    state: AppState,
    broadcast_manager: BroadcastManager,
    stt_controller: SttController,
    conversation_orchestrator: ConversationOrchestrator,
    get_stt_backend: Callable[[], str],
    get_input_devices: Callable[[], list[InputDevice]],
    load_context_files: Callable[[], str],
    meeting_lifecycle: MeetingLifecycleCoordinator,
) -> APIRouter:
    router = APIRouter()
    auth_token = get_backend_auth_token()
    background_tasks: set[asyncio.Task[None]] = set()

    def _track_background_task(task: asyncio.Task[None], *, label: str) -> None:
        background_tasks.add(task)

        def _on_done(done_task: asyncio.Task[None]) -> None:
            background_tasks.discard(done_task)
            if done_task.cancelled():
                logger.warning("WebSocket background task cancelled: %s", label)
                return
            exc = done_task.exception()
            if exc is not None:
                logger.error(
                    "WebSocket background task failed: %s",
                    label,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(_on_done)

    async def _handle_speech(role: str, text: str, speaker_id: str | None = None) -> None:
        await conversation_orchestrator.handle_speech(role, text, speaker_id)

    async def _dispatch(ws: WebSocket, msg: IncomingMessage) -> None:
        match msg:
            case SetDeviceMsg():
                await stt_controller.set_device(msg.role, msg.device)
            case InitSttMsg():
                await stt_controller.init_stt()
            case ShutdownSttMsg():
                await stt_controller.shutdown_stt()
            case StartMeetingMsg():
                await meeting_lifecycle.start_meeting(
                    ws,
                    meeting_context_payload=msg.meeting_context,
                    reference_payloads=msg.references,
                )
            case StopMeetingMsg():
                await meeting_lifecycle.stop_meeting()
            case ManualSpeechMsg():
                if text := msg.text.strip():
                    _track_background_task(
                        asyncio.create_task(_handle_speech("other", text)),
                        label="manual_speech",
                    )
            case UserReplyMsg():
                if text := msg.text.strip():
                    if state.current_session is None:
                        await broadcast_manager.reply(ws, ErrorMsg(text="会議が開始されていません"))
                        return
                    await conversation_orchestrator.handle_user_reply(text)
            case GenerateReplyMsg():
                target_utterance_id = msg.target_utterance_id.strip() if msg.target_utterance_id is not None else None
                await conversation_orchestrator.generate_reply(
                    target_utterance_id or None,
                    mode=msg.mode,
                    generation_id=msg.generation_id,
                )
            case CancelReplyMsg():
                _ = await conversation_orchestrator.cancel_replies(
                    generation_id=msg.generation_id,
                    target_utterance_id=msg.target_utterance_id,
                )
            case RunInfoMsg():
                await conversation_orchestrator.run_info_now()
            case ReloadContextMsg():
                state.context_text = load_context_files()
                lines = state.context_text.count("\n") + 1 if state.context_text else 0
                await broadcast_manager.reply(ws, StatusMsg(text=f"コンテキスト再読み込み完了 ({lines}行)"))

    @router.websocket("/ws")
    # Called by FastAPI via decorator — reportUnusedFunction is expected.
    async def websocket_endpoint(ws: WebSocket) -> None:  # pyright: ignore[reportUnusedFunction]
        accepted_subprotocol: str | None = None
        if auth_token is not None:
            if not is_origin_allowed(ws.headers.get("origin")):
                await ws.close(code=1008)
                return
            accepted_subprotocol = find_authorized_websocket_subprotocol(
                ws.headers.get("sec-websocket-protocol"),
                auth_token,
            )
            if accepted_subprotocol is None:
                await ws.close(code=1008)
                return

        await ws.accept(subprotocol=accepted_subprotocol)
        broadcast_manager.connections.add(ws)

        if len(broadcast_manager.connections) == 1:
            await stt_controller.start_level_monitors()

        await broadcast_manager.reply(ws, StatusMsg(text="接続済み — 待機中"))
        await broadcast_manager.reply(ws, MeetingStateMsg(running=state.is_running))
        await broadcast_manager.reply(
            ws,
            SttStateMsg(
                backend=get_stt_backend(),
                initialized=state.stt_initialized,
                initializing=state.stt_initializing,
            ),
        )
        try:
            devices = get_input_devices()
        except Exception:
            logger.exception("Failed to enumerate input devices during WebSocket initialization")
            devices = []
            await broadcast_manager.reply(ws, ErrorMsg(text="音声デバイス一覧の取得に失敗しました"))
        await broadcast_manager.reply(
            ws,
            DevicesListMsg(
                devices=devices,
                current_other=state.device_other,
                current_self=state.device_self,
            ),
        )
        await broadcast_manager.reply(
            ws,
            _build_agent_settings_message(
                state.config.agent_settings,
                [
                    ReplyAgentSettingsItem(
                        id=d.id,
                        label=d.label,
                        enabled=d.enabled,
                        priority=d.priority,
                    )
                    for d in state.config.reply_agent_definitions
                ],
            ),
        )
        session_turns = list(state.current_session.turns) if state.current_session else []
        if session_turns:
            await broadcast_manager.reply(
                ws,
                HistoryResetMsg(
                    items=[
                        TurnItem(
                            id=t.id,
                            speaker=t.speaker,
                            text=t.text,
                            speaker_id=t.speaker_id,
                        )
                        for t in session_turns
                    ]
                ),
            )
        session_ai_note = state.current_session.ai_note if state.current_session else ""
        await broadcast_manager.reply(ws, AiNoteUpdatedMsg(text=session_ai_note))
        if state.current_session is not None:
            await broadcast_manager.reply(ws, session_info_msg(state.current_session))

        try:
            while True:
                raw_data = cast(object, await ws.receive_json())
                try:
                    msg = _incoming_ta.validate_python(raw_data)
                except ValidationError as e:
                    await broadcast_manager.reply(
                        ws,
                        ErrorMsg(text=f"不正なメッセージ形式: {e.error_count()}件のエラー"),
                    )
                    continue
                try:
                    await _dispatch(ws, msg)
                except Exception as e:
                    logger.error("WS dispatch 失敗: %s", e)
                    traceback.print_exc()
                    await broadcast_manager.reply(ws, ErrorMsg(text=f"内部エラー: {e}"))

        except WebSocketDisconnect:
            pass
        finally:
            broadcast_manager.connections.discard(ws)

    return router


__all__ = ["create_router"]
