"""Focused contract tests for the OpenAI STT stage."""

import asyncio
import io
import os
import queue
import unittest
import urllib.error
import urllib.request
import wave
from collections.abc import Coroutine
from email.message import Message
from unittest.mock import patch

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import ErrorMsg, OutgoingMessage
from app.stt.stages.stt_openai import OpenAIStage


class _Publisher:
    def __init__(self) -> None:
        self.messages: list[OutgoingMessage] = []

    def publish(self, msg: OutgoingMessage) -> None:
        self.messages.append(msg)

    def schedule(self, coro: Coroutine[object, object, object]) -> None:
        _ = asyncio.run(coro)


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._payload: bytes = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


def _config(*, silence_duration: float = 0.06) -> SttConfig:
    return SttConfig(
        backend="openai",
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


class OpenAIStageTest(unittest.TestCase):
    def test_vad_segment_is_multipart_wav_and_emits_one_final(self) -> None:
        in_q: queue.Queue[AudioFrame | None] = queue.Queue()
        publisher = _Publisher()
        received: list[tuple[str, str]] = []

        async def handle_speech(role: str, text: str) -> None:
            received.append((role, text))

        silence = b"\x00\x00" * 480
        speech = b"\x10\x00" * 480
        in_q.put(AudioFrame(pcm=silence, is_speech=False, timestamp_ms=0))
        in_q.put(AudioFrame(pcm=speech, is_speech=True, timestamp_ms=30))
        in_q.put(AudioFrame(pcm=silence, is_speech=False, timestamp_ms=60))
        in_q.put(AudioFrame(pcm=silence, is_speech=False, timestamp_ms=90))
        in_q.put(None)

        stage = OpenAIStage(in_q, _config(), "other", publisher, handle_speech)
        response = _Response(b'{"text":"  OpenAI transcript  "}')
        captured_requests: list[urllib.request.Request] = []

        def fake_urlopen(request: urllib.request.Request, *, timeout: float) -> _Response:
            self.assertEqual(30.0, timeout)
            captured_requests.append(request)
            return response

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-openai-secret"}, clear=False),
            patch("app.stt.stages.stt_openai.urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            stage._run()  # pyright: ignore[reportPrivateUsage]

        self.assertEqual(1, len(captured_requests))
        request = captured_requests[0]
        self.assertEqual("https://api.openai.com/v1/audio/transcriptions", request.full_url)
        self.assertEqual("POST", request.get_method())
        self.assertEqual("Bearer test-openai-secret", request.get_header("Authorization"))
        content_type = request.get_header("Content-type")
        assert isinstance(content_type, str)
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        body = request.data
        assert isinstance(body, bytes)
        self.assertIn(b'name="model"\r\n\r\ngpt-4o-transcribe', body)
        self.assertIn(b'name="language"\r\n\r\nja', body)
        self.assertIn(b'name="file"; filename="utterance.wav"', body)
        wav_start = body.index(b"RIFF")
        wav_end = body.index(b"\r\n--", wav_start)
        with wave.open(io.BytesIO(body[wav_start:wav_end]), "rb") as audio:
            self.assertEqual(1, audio.getnchannels())
            self.assertEqual(2, audio.getsampwidth())
            self.assertEqual(16000, audio.getframerate())
            self.assertEqual(silence + speech + silence + silence, audio.readframes(audio.getnframes()))
        self.assertEqual([("other", "OpenAI transcript")], received)
        self.assertEqual([], publisher.messages)

    def test_authentication_error_does_not_expose_api_key(self) -> None:
        publisher = _Publisher()
        in_q: queue.Queue[AudioFrame | None] = queue.Queue()
        stage = OpenAIStage(in_q, _config(), "self", publisher, self._noop_handle_speech)
        error = urllib.error.HTTPError(
            "https://api.openai.com/v1/audio/transcriptions", 401, "Unauthorized", Message(), None
        )

        with patch("app.stt.stages.stt_openai.urllib.request.urlopen", side_effect=error):
            stage._transcribe(b"\x00\x00", "must-not-leak")  # pyright: ignore[reportPrivateUsage]

        self.assertEqual(1, len(publisher.messages))
        message = publisher.messages[0]
        self.assertIsInstance(message, ErrorMsg)
        assert isinstance(message, ErrorMsg)
        self.assertIn("認証エラー", message.text)
        self.assertNotIn("must-not-leak", message.text)

    async def _noop_handle_speech(self, _role: str, _text: str) -> None:
        return None


if __name__ == "__main__":
    _ = unittest.main()
