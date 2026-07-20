"""RemoteStage: remote WebSocket STT consuming frames from Q2."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import cast, override

import websockets
from websockets.asyncio.client import ClientConnection

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import ErrorMsg, SttInterimMsg
from app.core.publisher import OutgoingPublisher
from app.core.types import HandleSpeechFn
from app.stt.stages.base_websocket import WebSocketSttStage


class RemoteStage(WebSocketSttStage[bytes]):
    """Streams PCM to a remote STT server over WebSocket."""

    def __init__(
        self,
        in_q: queue.Queue[AudioFrame | None],
        cfg: SttConfig,
        role: str,
        publisher: OutgoingPublisher,
        handle_speech_fn: HandleSpeechFn,
    ) -> None:
        super().__init__(in_q, role, publisher, handle_speech_fn)
        self._cfg: SttConfig = cfg

    @override
    def _transform_frame(self, frame: AudioFrame) -> bytes:
        return frame.pcm

    @override
    def _on_session_error(self, exc: Exception) -> None:
        self._publisher.publish(ErrorMsg(text=f"RemoteSTTエラー({self._role}): {exc}"))

    @override
    async def _session(
        self,
        loop: asyncio.AbstractEventLoop,
        audio_queue: queue.Queue[bytes | None],
        session_active: threading.Event,
    ) -> None:
        cfg = self._cfg
        extra_headers = {"Authorization": f"Bearer {cfg.remote_token}"} if cfg.remote_token else {}
        async with websockets.connect(cfg.remote_url, additional_headers=extra_headers) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "config",
                        "sample_rate": cfg.sample_rate,
                        "language": cfg.language,
                    }
                )
            )
            msg_raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            if not isinstance(msg_raw, str):
                raise RuntimeError(f"予期しないレスポンス型: {type(msg_raw)}")
            msg = cast(dict[str, object], json.loads(msg_raw))
            if msg.get("type") != "ready":
                raise RuntimeError(f"予期しないレスポンス: {msg}")

            await self._run_send_recv(
                self._send_loop(ws, audio_queue),
                self._recv_loop(ws),
            )

    async def _send_loop(self, ws: ClientConnection, audio_queue: queue.Queue[bytes | None]) -> None:
        while not self._stop_event.is_set():
            try:
                item = audio_queue.get_nowait()
                if item is None:
                    return
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue
            await ws.send(item)

    async def _recv_loop(self, ws: ClientConnection) -> None:
        async for message in ws:
            if not isinstance(message, str):
                continue
            data = cast(dict[str, object], json.loads(message))
            t = data.get("type")
            if t == "interim":
                text = data.get("text")
                if isinstance(text, str):
                    self._publisher.publish(SttInterimMsg(role=self._role, text=text))
            elif t == "final":
                text = data.get("text")
                if isinstance(text, str):
                    self._publisher.schedule(self._handle_speech(self._role, text))
            elif t == "error":
                err_text = data.get("text", "")
                if not isinstance(err_text, str):
                    err_text = str(err_text)
                self._publisher.publish(ErrorMsg(text=f"STTサーバーエラー({self._role}): {err_text}"))


__all__ = ["RemoteStage"]
