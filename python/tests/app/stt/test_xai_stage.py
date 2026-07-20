"""Focused contract tests for the xAI streaming STT stage."""

import asyncio
import json
import os
import queue
import threading
import unittest
from collections.abc import AsyncIterator, Coroutine, Iterator
from typing import cast
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import ErrorMsg, OutgoingMessage, SttInterimMsg
from app.stt.stages.stt_xai import XaiStage


class _Publisher:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop: asyncio.AbstractEventLoop = loop
        self.messages: list[OutgoingMessage] = []
        self.published: threading.Event = threading.Event()
        self.finals: list[tuple[str, str]] = []

    def publish(self, msg: OutgoingMessage) -> None:
        self.messages.append(msg)
        _ = self.published.set()

    def schedule(self, coro: Coroutine[object, object, object]) -> None:
        _ = self._loop.create_task(coro)


class _SessionWebSocket:
    def __init__(self) -> None:
        self.sent: list[bytes | str] = []
        self._done: asyncio.Event = asyncio.Event()
        self._done_yielded: bool = False

    async def recv(self) -> str:
        return json.dumps({"type": "transcript.created"})

    async def send(self, data: bytes | str) -> None:
        self.sent.append(data)
        if data == json.dumps({"type": "audio.done"}):
            _ = self._done.set()

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        if self._done_yielded:
            raise StopAsyncIteration
        _ = await self._done.wait()
        self._done_yielded = True
        return json.dumps({"type": "transcript.done", "text": ""})


class _Connect:
    def __init__(self, ws: _SessionWebSocket) -> None:
        self.ws: _SessionWebSocket = ws

    async def __aenter__(self) -> _SessionWebSocket:
        return self.ws

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        return False


class _EventWebSocket:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self._events: Iterator[dict[str, object]] = iter(events)

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        try:
            return json.dumps(next(self._events))
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def _config(*, silence_duration: float = 0.06) -> SttConfig:
    return SttConfig(
        backend="xai",
        whisper_model="tiny",
        deepgram_model="nova-3",
        openai_model="gpt-4o-transcribe",
        language="ja",
        vad_sensitivity=0.4,
        silence_duration=silence_duration,
        vad_aggressiveness=2,
        device="auto",
        remote_url="",
        remote_token="",
        sample_rate=16000,
        chunk_size=960,
    )


class XaiStageTest(unittest.IsolatedAsyncioTestCase):
    _publisher: _Publisher | None = None

    async def test_waits_for_created_then_sends_raw_pcm_finalize_and_audio_done(self) -> None:
        audio_q: queue.Queue[tuple[bytes, bool] | None] = queue.Queue()
        publisher = _Publisher(asyncio.get_running_loop())
        self._publisher = publisher
        stage = XaiStage(queue.Queue[AudioFrame | None](), _config(), "other", publisher, self._record_final)
        ws = _SessionWebSocket()
        silence = b"\x00\x00" * 480
        speech = b"\x10\x00" * 480
        audio_q.put((silence, False))
        audio_q.put((speech, True))
        audio_q.put((silence, False))
        audio_q.put((silence, False))
        audio_q.put(None)

        connected_urls: list[str] = []
        connected_headers: list[dict[str, str]] = []

        def fake_connect(url: str, *, additional_headers: object) -> _Connect:
            assert isinstance(additional_headers, dict)
            connected_urls.append(url)
            connected_headers.append(cast(dict[str, str], additional_headers))
            return _Connect(ws)

        with (
            patch.dict(os.environ, {"XAI_API_KEY": "test-xai-secret"}, clear=False),
            patch("app.stt.stages.stt_xai.websockets.connect", new=fake_connect),
        ):
            await stage._session(asyncio.get_running_loop(), audio_q, threading.Event())  # pyright: ignore[reportPrivateUsage]

        self.assertEqual(1, len(connected_urls))
        self.assertEqual(1, len(connected_headers))
        self.assertEqual("Bearer test-xai-secret", connected_headers[0]["Authorization"])
        parsed = urlparse(connected_urls[0])
        self.assertEqual("wss", parsed.scheme)
        self.assertEqual("api.x.ai", parsed.netloc)
        self.assertEqual("/v1/stt", parsed.path)
        self.assertEqual(
            {
                "sample_rate": ["16000"],
                "encoding": ["pcm"],
                "interim_results": ["true"],
                "endpointing": ["60"],
                "language": ["ja"],
            },
            parse_qs(parsed.query),
        )
        self.assertEqual(
            [
                silence,
                speech,
                silence,
                silence,
                json.dumps({"type": "Finalize"}),
                json.dumps({"type": "audio.done"}),
            ],
            ws.sent,
        )

    async def test_partial_chunk_and_speech_final_emit_one_final_text(self) -> None:
        publisher = _Publisher(asyncio.get_running_loop())
        self._publisher = publisher
        stage = XaiStage(queue.Queue[AudioFrame | None](), _config(), "self", publisher, self._record_final)
        ws = _EventWebSocket(
            [
                {"type": "transcript.partial", "text": "interim", "is_final": False, "speech_final": False},
                {"type": "transcript.partial", "text": "hello ", "is_final": True, "speech_final": False},
                {"type": "transcript.partial", "text": "world", "is_final": True, "speech_final": True},
                {"type": "transcript.done", "text": ""},
            ]
        )

        await stage._recv_loop(ws)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
        await asyncio.sleep(0)

        self.assertEqual(1, len(publisher.messages))
        self.assertIsInstance(publisher.messages[0], SttInterimMsg)
        message = publisher.messages[0]
        assert isinstance(message, SttInterimMsg)
        self.assertEqual("interim", message.text)
        self.assertEqual([("self", "hello world")], publisher.finals)

    async def test_missing_key_and_server_error_do_not_leak_secret(self) -> None:
        publisher = _Publisher(asyncio.get_running_loop())
        self._publisher = publisher
        stage = XaiStage(queue.Queue[AudioFrame | None](), _config(), "self", publisher, self._record_final)
        with patch.dict(os.environ, {}, clear=True):
            stage.start()
            try:
                reported = await asyncio.to_thread(publisher.published.wait, 0.5)
                self.assertTrue(reported)
            finally:
                stage.stop(timeout=1)
        ws = _EventWebSocket([{"type": "error", "message": "Bearer must-not-leak"}])
        await stage._recv_loop(ws)  # pyright: ignore[reportPrivateUsage, reportArgumentType]

        error_texts = [message.text for message in publisher.messages if isinstance(message, ErrorMsg)]
        self.assertEqual(["XAI_API_KEY が未設定です", "xAI STTサーバーエラー(self)"], error_texts)
        self.assertNotIn("must-not-leak", " ".join(error_texts))

    async def _record_final(self, role: str, text: str) -> None:
        publisher = self._publisher
        assert publisher is not None
        publisher.finals.append((role, text))


if __name__ == "__main__":
    _ = unittest.main()
