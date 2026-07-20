"""xAI streaming STT stage consuming VAD-annotated PCM16 frames."""

from __future__ import annotations

import asyncio
import collections
import json
import os
import queue
import threading
from typing import cast, override
from urllib.parse import urlencode

import websockets
from websockets.asyncio.client import ClientConnection

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import ErrorMsg, SttInterimMsg
from app.core.publisher import OutgoingPublisher
from app.core.types import HandleSpeechFn
from app.stt.stages.base_websocket import WebSocketSttStage

_ENDPOINT = "wss://api.x.ai/v1/stt"
_FRAME_MS = 30
_PREROLL_MS = 300
_READY_TIMEOUT_SECONDS = 10.0


class XaiStage(WebSocketSttStage[tuple[bytes, bool]]):
    """Stream VAD-gated PCM16 to the fixed xAI STT WebSocket endpoint."""

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
        # Do not expose connection details or credentials from websocket errors.
        _ = exc
        self._publisher.publish(ErrorMsg(text=f"xAI STT接続エラー({self._role})"))

    @override
    async def _session(
        self,
        loop: asyncio.AbstractEventLoop,
        audio_queue: queue.Queue[tuple[bytes, bool] | None],
        session_active: threading.Event,
    ) -> None:
        _ = (loop, session_active)
        api_key = os.environ.get("XAI_API_KEY", "")
        if not api_key:
            self._publisher.publish(ErrorMsg(text="XAI_API_KEY が未設定です"))
            while not self._stop_event.is_set():
                await asyncio.sleep(0.1)
            return

        cfg = self._cfg
        query = urlencode(
            {
                "sample_rate": cfg.sample_rate,
                "encoding": "pcm",
                "interim_results": "true",
                "endpointing": max(0, min(5000, int(float(cfg.silence_duration) * 1000))),
                "language": cfg.language,
            }
        )
        async with websockets.connect(
            f"{_ENDPOINT}?{query}", additional_headers={"Authorization": f"Bearer {api_key}"}
        ) as ws:
            await self._wait_for_ready(ws)
            await self._run_send_recv(self._send_loop(ws, audio_queue, cfg), self._recv_loop(ws))

    async def _wait_for_ready(self, ws: ClientConnection) -> None:
        message = await asyncio.wait_for(ws.recv(), timeout=_READY_TIMEOUT_SECONDS)
        if not isinstance(message, str):
            raise RuntimeError("xAI STT準備応答が不正です")
        try:
            event = cast(dict[str, object], json.loads(message))
        except json.JSONDecodeError as exc:
            raise RuntimeError("xAI STT準備応答が不正です") from exc
        if event.get("type") != "transcript.created":
            raise RuntimeError("xAI STT準備応答が不正です")

    async def _send_loop(
        self,
        ws: ClientConnection,
        audio_queue: queue.Queue[tuple[bytes, bool] | None],
        cfg: SttConfig,
    ) -> None:
        silence_threshold = max(1, int(float(cfg.silence_duration) * 1000 / _FRAME_MS))
        preroll: collections.deque[bytes] = collections.deque(maxlen=max(1, _PREROLL_MS // _FRAME_MS))
        in_speech = False
        silence_frames = 0

        try:
            while not self._stop_event.is_set():
                try:
                    item = audio_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.005)
                    continue
                if item is None:
                    if in_speech:
                        await ws.send(json.dumps({"type": "Finalize"}))
                    return

                pcm, is_speech = item
                if is_speech:
                    if not in_speech:
                        for preroll_frame in preroll:
                            await ws.send(preroll_frame)
                        preroll.clear()
                        in_speech = True
                    await ws.send(pcm)
                    silence_frames = 0
                elif in_speech:
                    await ws.send(pcm)
                    silence_frames += 1
                    if silence_frames >= silence_threshold:
                        await ws.send(json.dumps({"type": "Finalize"}))
                        in_speech = False
                        silence_frames = 0
                else:
                    preroll.append(pcm)
        finally:
            # audio.done is required by xAI to flush the final transcript before close.
            await ws.send(json.dumps({"type": "audio.done"}))

    async def _recv_loop(self, ws: ClientConnection) -> None:
        chunk_buf: list[str] = []
        emitted_final = False

        async for message in ws:
            if not isinstance(message, str):
                continue
            try:
                event = cast(dict[str, object], json.loads(message))
            except json.JSONDecodeError:
                self._publisher.publish(ErrorMsg(text=f"xAI STT応答エラー({self._role})"))
                continue

            event_type = event.get("type")
            if event_type == "transcript.partial":
                text_value = event.get("text")
                response_text = text_value if isinstance(text_value, str) else ""
                is_final = bool(event.get("is_final", False))
                speech_final = bool(event.get("speech_final", False))
                if not is_final:
                    if response_text.strip():
                        self._publisher.publish(SttInterimMsg(role=self._role, text=response_text.strip()))
                    continue
                if response_text.strip():
                    chunk_buf.append(response_text)
                if speech_final:
                    full_text = self._join_chunks(chunk_buf)
                    chunk_buf.clear()
                    if full_text:
                        self._publisher.schedule(self._handle_speech(self._role, full_text))
                        emitted_final = True
            elif event_type == "transcript.done":
                text_value = event.get("text")
                done_text = text_value.strip() if isinstance(text_value, str) else ""
                if chunk_buf:
                    # transcript.done is the authoritative final text for a still-open utterance.
                    full_text = done_text or "".join(chunk_buf).strip()
                    chunk_buf.clear()
                    if full_text:
                        self._publisher.schedule(self._handle_speech(self._role, full_text))
                elif done_text and not emitted_final:
                    self._publisher.schedule(self._handle_speech(self._role, done_text))
                return
            elif event_type == "error":
                # Server payloads are intentionally not surfaced because they may contain request details.
                self._publisher.publish(ErrorMsg(text=f"xAI STTサーバーエラー({self._role})"))

    @staticmethod
    def _join_chunks(chunks: list[str]) -> str:
        """Join finalized chunks with exactly one boundary space when needed."""
        joined = ""
        for chunk in chunks:
            if not chunk.strip():
                continue
            if joined and not joined[-1].isspace() and not chunk[0].isspace():
                joined += " "
            joined += chunk
        return joined.strip()


__all__ = ["XaiStage"]
