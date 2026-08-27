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
        for future in publisher.futures:
            if isinstance(future, asyncio.Future):
                _ = await future
            else:
                _ = await asyncio.wrap_future(future)
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

    async def test_prepends_last_150ms_when_speech_starts(self) -> None:
        pre_roll = [
            AudioFrame(
                pcm=value.to_bytes(2, "little", signed=True) * 480,
                is_speech=False,
                timestamp_ms=index * 30.0,
            )
            for index, value in enumerate(range(1, 7))
        ]
        speech = [AudioFrame(pcm=_LOUD_PCM, is_speech=True, timestamp_ms=(index + 6) * 30.0) for index in range(2)]

        with patch("app.stt.stages.stt_vosk.VoskEngine.transcribe", return_value="先頭を保持") as transcribe:
            received = await self._run_stage([*pre_roll, *speech])

        expected_pcm = b"".join([frame.pcm for frame in pre_roll[-5:]] + [frame.pcm for frame in speech])
        self.assertEqual(transcribe.call_args.args[1], expected_pcm)
        self.assertEqual(received, [("other", "先頭を保持")])

    async def test_each_close_utterance_gets_the_immediately_preceding_five_frames(self) -> None:
        def make_frame(value: int, is_speech: bool, index: int) -> AudioFrame:
            return AudioFrame(
                pcm=value.to_bytes(2, "little", signed=True) * 480,
                is_speech=is_speech,
                timestamp_ms=index * 30.0,
            )

        history = [make_frame(value, False, index) for index, value in enumerate(range(1, 6))]
        first_speech = [make_frame(value, True, index + 5) for index, value in enumerate(range(10001, 10003))]
        first_trailing = [make_frame(value, False, index + 7) for index, value in enumerate(range(2001, 2003))]
        second_speech = [make_frame(value, True, index + 9) for index, value in enumerate(range(13001, 13003))]
        second_trailing = [make_frame(value, False, index + 11) for index, value in enumerate(range(3001, 3003))]

        with patch(
            "app.stt.stages.stt_vosk.VoskEngine.transcribe",
            side_effect=["最初", "次"],
        ) as transcribe:
            received = await self._run_stage(
                [*history, *first_speech, *first_trailing, *second_speech, *second_trailing]
            )

        self.assertEqual(transcribe.call_count, 2)
        expected_segments = [
            [*history, *first_speech, *first_trailing],
            [history[-1], *first_speech, *first_trailing, *second_speech, *second_trailing],
        ]
        for call, expected_frames in zip(transcribe.call_args_list, expected_segments, strict=True):
            self.assertEqual(call.args[1], b"".join(frame.pcm for frame in expected_frames))
        self.assertEqual(received, [("other", "最初"), ("other", "次")])

    async def test_preroll_volume_does_not_change_rms_gate(self) -> None:
        loud_preroll = [AudioFrame(pcm=_LOUD_PCM, is_speech=False, timestamp_ms=index * 30.0) for index in range(5)]
        quiet_speech = [
            AudioFrame(pcm=_LOW_SIGNAL_PCM, is_speech=True, timestamp_ms=(index + 5) * 30.0) for index in range(3)
        ]
        with patch("app.stt.stages.stt_vosk.VoskEngine.transcribe", return_value="誤検出") as transcribe:
            received = await self._run_stage([*loud_preroll, *quiet_speech])

        transcribe.assert_not_called()
        self.assertEqual(received, [])

        quiet_preroll = [
            AudioFrame(pcm=_LOW_SIGNAL_PCM, is_speech=False, timestamp_ms=index * 30.0) for index in range(5)
        ]
        loud_speech = [AudioFrame(pcm=_LOUD_PCM, is_speech=True, timestamp_ms=(index + 5) * 30.0) for index in range(3)]
        with patch("app.stt.stages.stt_vosk.VoskEngine.transcribe", return_value="発話") as transcribe:
            received = await self._run_stage([*quiet_preroll, *loud_speech])

        transcribe.assert_called_once()
        self.assertEqual(received, [("other", "発話")])


if __name__ == "__main__":
    _ = unittest.main()
