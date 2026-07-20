"""Authenticated managed speech-recognition relay stage."""

from __future__ import annotations

import asyncio
import json
import queue
import re
import threading
import time
from collections.abc import AsyncIterator, Callable
from typing import Protocol, cast, final, override
from urllib.parse import urlencode, urlsplit, urlunsplit

import websockets
from websockets.exceptions import ConnectionClosed

from app.audio.base import AudioFrame
from app.core.messages import ErrorMsg, SttInterimMsg
from app.core.publisher import OutgoingPublisher
from app.core.types import HandleSpeechFn
from app.services.managed_session import ManagedSessionStore
from app.stt.stages.base_websocket import WebSocketSttStage

_RELAY_FRAME_BYTES = 3_200


def _relay_url(api_base_url: str, session_id: str, role: str) -> str:
    base = urlsplit(api_base_url)
    if base.scheme != "https" or not base.netloc or base.username is not None or base.password is not None:
        raise ValueError("invalid managed service URL")
    query = urlencode({"session_id": session_id, "role": role})
    return urlunsplit(("wss", base.netloc, f"{base.path.rstrip('/')}/v1/stt", query, ""))


class _ManagedSocket(Protocol):
    async def send(self, message: bytes) -> None: ...

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


@final
class ManagedSttStage(WebSocketSttStage[bytes]):
    """Streams fixed-size PCM frames through the quota-enforcing Worker relay."""

    def __init__(
        self,
        in_q: queue.Queue[AudioFrame | None],
        role: str,
        publisher: OutgoingPublisher,
        handle_speech_fn: HandleSpeechFn,
        session_store: ManagedSessionStore,
        get_session_id: Callable[[], str | None],
    ) -> None:
        super().__init__(in_q, role, publisher, handle_speech_fn)
        self._session_store: ManagedSessionStore = session_store
        self._get_session_id: Callable[[], str | None] = get_session_id

    @override
    def _transform_frame(self, frame: AudioFrame) -> bytes:
        return frame.pcm

    @override
    def _on_session_error(self, exc: Exception) -> None:
        code = exc.code if isinstance(exc, ConnectionClosed) else None
        messages = {
            4401: "ログインを更新してください。",
            4402: "支払い方法を確認してください。",
            4403: "月額プランの契約が必要です。",
            4408: "今月の共通利用枠を使い切りました。",
            4429: "同時に利用できる音声認識の上限に達しました。",
            4503: "Meeting Supporter 音声認識を現在利用できません。",
        }
        message = (
            messages.get(code, "Meeting Supporter 音声認識へ接続できませんでした。")
            if code is not None
            else "Meeting Supporter 音声認識へ接続できませんでした。"
        )
        self._publisher.publish(ErrorMsg(text=message))

    @override
    async def _session(
        self,
        loop: asyncio.AbstractEventLoop,
        audio_queue: queue.Queue[bytes | None],
        session_active: threading.Event,
    ) -> None:
        del loop, session_active
        session = self._session_store.get()
        if session is None or session.expires_at <= int(time.time()) + 5:
            raise RuntimeError("managed sign-in required")
        session_id = self._get_session_id()
        if not isinstance(session_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{8,64}", session_id) is None:
            raise RuntimeError("invalid meeting session")
        url = _relay_url(session.api_base_url, session_id, self._role)
        headers = {"Authorization": f"Bearer {session.access_token}"}
        async with websockets.connect(url, additional_headers=headers) as ws:
            await self._run_send_recv(self._send_loop(ws, audio_queue), self._recv_loop(ws))

    async def _send_loop(self, ws: _ManagedSocket, audio_queue: queue.Queue[bytes | None]) -> None:
        buffer = bytearray()
        while not self._stop_event.is_set():
            try:
                pcm = audio_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.005)
                continue
            if pcm is None:
                return
            buffer.extend(pcm)
            while len(buffer) >= _RELAY_FRAME_BYTES:
                await ws.send(bytes(buffer[:_RELAY_FRAME_BYTES]))
                del buffer[:_RELAY_FRAME_BYTES]

    async def _recv_loop(self, ws: _ManagedSocket) -> None:
        chunks: list[str] = []
        async for message in ws:
            if not isinstance(message, str):
                continue
            try:
                data = cast(dict[str, object], json.loads(message))
            except json.JSONDecodeError:
                continue
            if data.get("type") != "Results":
                continue
            channel = data.get("channel")
            if not isinstance(channel, dict):
                continue
            alternatives_value = cast(dict[str, object], channel).get("alternatives")
            if not isinstance(alternatives_value, list) or not alternatives_value:
                continue
            alternatives = cast(list[object], alternatives_value)
            first = alternatives[0]
            if not isinstance(first, dict):
                continue
            transcript = cast(dict[str, object], first).get("transcript")
            text = transcript.strip() if isinstance(transcript, str) else ""
            if bool(data.get("is_final")):
                if text:
                    chunks.append(text)
                if bool(data.get("speech_final")):
                    full_text = "".join(chunks)
                    chunks.clear()
                    if full_text:
                        self._publisher.schedule(self._handle_speech(self._role, full_text))
            elif text:
                self._publisher.publish(SttInterimMsg(role=self._role, text=text))


__all__ = ["ManagedSttStage"]
