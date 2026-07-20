"""DeepgramStage: Deepgram streaming STT consuming VAD-annotated frames from Q2."""

from __future__ import annotations

import asyncio
import collections
import json
import os
import queue
import threading
import time
from typing import cast, override

import websockets
from websockets.asyncio.client import ClientConnection

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import ErrorMsg, SttInterimMsg
from app.core.publisher import OutgoingPublisher
from app.core.types import HandleSpeechFn
from app.stt.stages.base_websocket import WebSocketSttStage

_FRAME_MS = 30
_KEEPALIVE_INTERVAL = 3.0
_PREROLL_MS = 300


class DeepgramStage(WebSocketSttStage[tuple[bytes, bool]]):
    """Streams VAD-gated PCM to Deepgram over WebSocket.

    Uses ``is_speech`` from AudioFrame instead of running its own VAD.
    KeepAlive is sent during silence to avoid billing interruptions.
    """

    _AUDIO_QUEUE_SIZE: int = 300

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
    def _transform_frame(self, frame: AudioFrame) -> tuple[bytes, bool]:
        return (frame.pcm, frame.is_speech)

    @override
    def _on_session_error(self, exc: Exception) -> None:
        self._publisher.publish(ErrorMsg(text=f"DeepgramSTTエラー({self._role}): {exc}"))

    @override
    async def _session(
        self,
        loop: asyncio.AbstractEventLoop,
        audio_queue: queue.Queue[tuple[bytes, bool] | None],
        session_active: threading.Event,
    ) -> None:
        api_key = os.environ.get("DEEPGRAM_API_KEY", "")
        if not api_key:
            self._publisher.publish(ErrorMsg(text="DEEPGRAM_API_KEY が未設定です"))
            return

        cfg = self._cfg
        model = cfg.deepgram_model or "nova-3"
        params = "&".join(
            [
                f"model={model}",
                f"language={cfg.language}",
                "encoding=linear16",
                f"sample_rate={cfg.sample_rate}",
                "channels=1",
                "interim_results=true",
                "endpointing=300",
            ]
        )
        url = f"wss://api.deepgram.com/v1/listen?{params}"
        headers = {"Authorization": f"Token {api_key}"}

        async with websockets.connect(url, additional_headers=headers) as ws:
            finalize_queue: asyncio.Queue[None] = asyncio.Queue()

            await self._run_send_recv(
                self._send_loop(ws, audio_queue, cfg, finalize_queue),
                self._recv_loop(ws, finalize_queue),
            )

    async def _send_loop(
        self,
        ws: ClientConnection,
        audio_queue: queue.Queue[tuple[bytes, bool] | None],
        cfg: SttConfig,
        finalize_queue: asyncio.Queue[None],
    ) -> None:
        last_keepalive = time.monotonic()
        in_speech = False
        silence_frames = 0
        silence_threshold = max(1, int(float(cfg.silence_duration) * 1000 / _FRAME_MS))
        preroll_size = max(1, _PREROLL_MS // _FRAME_MS)
        preroll: collections.deque[bytes] = collections.deque(maxlen=preroll_size)

        while not self._stop_event.is_set():
            now = time.monotonic()
            try:
                item = audio_queue.get_nowait()
                if item is None:
                    return
                pcm_bytes, is_speech = item
                if is_speech:
                    if not in_speech:
                        for frame in preroll:
                            await ws.send(frame)
                        preroll.clear()
                        in_speech = True
                        silence_frames = 0
                    await ws.send(pcm_bytes)
                    last_keepalive = now
                else:
                    if in_speech:
                        silence_frames += 1
                        await ws.send(pcm_bytes)
                        last_keepalive = now
                        if silence_frames >= silence_threshold:
                            await ws.send(json.dumps({"type": "Finalize"}))
                            finalize_queue.put_nowait(None)
                            in_speech = False
                        silence_frames = 0
                    else:
                        preroll.append(pcm_bytes)
                        if now - last_keepalive >= _KEEPALIVE_INTERVAL:
                            await ws.send(json.dumps({"type": "KeepAlive"}))
                            last_keepalive = now
            except queue.Empty:
                if now - last_keepalive >= _KEEPALIVE_INTERVAL:
                    await ws.send(json.dumps({"type": "KeepAlive"}))
                    last_keepalive = now
                await asyncio.sleep(0.005)

    async def _recv_loop(self, ws: ClientConnection, finalize_queue: asyncio.Queue[None]) -> None:
        chunk_buf: list[str] = []
        pending_finalize = 0

        async for message in ws:
            while not finalize_queue.empty():
                _ = finalize_queue.get_nowait()
                pending_finalize += 1

            if not isinstance(message, str):
                continue
            data = cast(dict[str, object], json.loads(message))
            type_val = data.get("type")
            if type_val != "Results":
                continue
            channel_val = data.get("channel")
            if not isinstance(channel_val, dict):
                continue
            channel = cast(dict[str, object], channel_val)
            alts = channel.get("alternatives")
            if not isinstance(alts, list) or not alts:
                continue
            alts_list = cast(list[object], alts)
            first_alt = alts_list[0]
            if not isinstance(first_alt, dict):
                continue
            first_alt_dict = cast(dict[str, object], first_alt)
            transcript = first_alt_dict.get("transcript")
            text = str(transcript).strip() if transcript is not None else ""
            is_final = bool(data.get("is_final", False))
            speech_final = bool(data.get("speech_final", False))
            if is_final:
                if text:
                    chunk_buf.append(text)
                if speech_final or pending_finalize > 0:
                    pending_finalize = max(0, pending_finalize - 1)
                    full_text = "".join(chunk_buf)
                    chunk_buf.clear()
                    if full_text:
                        self._publisher.schedule(self._handle_speech(self._role, full_text))
            elif text:
                self._publisher.publish(SttInterimMsg(role=self._role, text=text))


__all__ = ["DeepgramStage"]
