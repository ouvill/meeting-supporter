"""Focused tests for the local Vosk STT stage."""

import asyncio
import queue
import unittest
from collections.abc import Coroutine
from concurrent.futures import Future as ConcurrentFuture
from unittest.mock import patch

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import OutgoingMessage
from app.stt.stages.stt_vosk import VoskStage, parse_vosk_text

_LOUD_PCM = (12000).to_bytes(2, "little", signed=True) * 480
_LOW_SIGNAL_PCM = b"\x00\x00" * 480


class _RecordingPublisher:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop: asyncio.AbstractEventLoop = loop
        self.messages: list[dict[str, object]] = []
        self.futures: list[asyncio.Future[object] | ConcurrentFuture[object]] = []

    def publish(self, msg: OutgoingMessage) -> None:
        self.messages.append(msg.model_dump())

    def schedule(self, coro: Coroutine[object, object, object]) -> None:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        self.futures.append(future)


def _make_config() -> SttConfig:
    return SttConfig(
        backend="vosk",
        whisper_model="tiny",
        deepgram_model="nova-2",
        language="ja",
        vad_sensitivity=0.5,
        silence_duration=0.06,
        vad_aggressiveness=2,
        device="default",
        remote_url="",
        remote_token="",
        sample_rate=16000,
        chunk_size=960,
        min_voiced_ms=60,
        min_voiced_ratio=0.35,
        min_rms_dbfs=-45.0,
        vosk_model_path="/tmp/fake-vosk-model",
    )


class VoskJsonParsingTest(unittest.TestCase):
    def test_parse_vosk_text_returns_trimmed_final_text(self) -> None:
        self.assertEqual(parse_vosk_text('{"text": "  ローカル 音声  "}'), "ローカル 音声")

    def test_parse_vosk_text_maps_empty_or_malformed_payloads_to_empty_text(self) -> None:
        cases = {
            "empty payload": "",
            "invalid json": "{not-json",
            "missing text field": '{"partial": "途中"}',
            "empty text field": '{"text": "   "}',
            "non-string text field": '{"text": 42}',
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                self.assertEqual(parse_vosk_text(payload), "")


class VoskStageThresholdTest(unittest.IsolatedAsyncioTestCase):
    async def _run_stage(self, frames: list[AudioFrame]) -> list[tuple[str, str]]:
        in_q = queue.Queue[AudioFrame | None]()
        received: list[tuple[str, str]] = []

        async def handle_speech(role: str, text: str) -> None:
            received.append((role, text))

        publisher = _RecordingPublisher(asyncio.get_running_loop())
        stage = VoskStage(in_q, _make_config(), "other", publisher, handle_speech)
        stage.start()
        for frame in frames:
            in_q.put(frame)
        in_q.put(None)
        stage.join(timeout=1.0)
        self.assertFalse(stage.running)
        await asyncio.sleep(0)
        return received

    async def test_short_speech_segment_is_ignored_without_transcribing(self) -> None:
        frames = [AudioFrame(pcm=_LOUD_PCM, is_speech=True, timestamp_ms=0.0)]

        with patch("app.stt.stages.stt_vosk.VoskEngine.transcribe", return_value="短い発話") as transcribe:
            received = await self._run_stage(frames)

        transcribe.assert_not_called()
        self.assertEqual(received, [])

    async def test_low_signal_segment_is_ignored_without_transcribing(self) -> None:
        frames = [AudioFrame(pcm=_LOW_SIGNAL_PCM, is_speech=True, timestamp_ms=index * 30.0) for index in range(3)]

        with patch("app.stt.stages.stt_vosk.VoskEngine.transcribe", return_value="無音") as transcribe:
            received = await self._run_stage(frames)

        transcribe.assert_not_called()
        self.assertEqual(received, [])


if __name__ == "__main__":
    _ = unittest.main()
