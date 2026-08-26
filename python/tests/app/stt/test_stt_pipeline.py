"""Tests for app.stt.pipeline — SttPipeline lifecycle and hot-swap."""

import asyncio
import queue
import threading
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast
from unittest.mock import patch

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import OutgoingMessage, StatusMsg
from app.core.publisher import OutgoingPublisher
from app.stt.pipeline import SttPipeline


# Minimal stand-in for AudioFrame to avoid importing numpy-dependent modules.
@dataclass
class _AudioFrame:
    pcm: bytes
    is_speech: bool
    timestamp_ms: float


class FakeBroadcast:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def __call__(self, msg: OutgoingMessage) -> None:
        self.messages.append(msg.model_dump())


async def _noop_handle_speech(_role: str, _text: str) -> None:
    pass


_SPEECH_PCM = b"\x01\x00" * 480
_LOUD_SPEECH_PCM = (12000).to_bytes(2, "little", signed=True) * 480
_SILENCE_PCM = b"\x00\x00" * 480
_DUMMY_TRANSCRIPT = "これはダミーSTTのテスト発話です"


class _MarkedVadEngine:
    def __init__(self, _aggressiveness: int) -> None:
        pass

    def is_speech(self, frame: bytes, _sample_rate: int) -> bool:
        return frame in {_SPEECH_PCM, _LOUD_SPEECH_PCM}


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0.01)


class SttPipelineLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def _make_config(self, backend: str) -> SttConfig:
        return SttConfig(
            backend=backend,
            whisper_model="tiny",
            deepgram_model="nova-2",
            language="ja",
            vad_sensitivity=0.5,
            vad_engine="webrtc",
            silence_duration=0.5,
            vad_aggressiveness=2,
            device="default",
            remote_url="",
            remote_token="",
            sample_rate=16000,
            chunk_size=960,
        )

    def _make_pipeline(self, backend: str = "deepgram") -> SttPipeline:
        stt_q = queue.Queue[_AudioFrame | None]()
        return SttPipeline(
            stt_queue=cast("queue.Queue[AudioFrame | None]", stt_q),
            cfg=self._make_config(backend),
            role="other",
            broadcast_fn=FakeBroadcast(),
            handle_speech_fn=_noop_handle_speech,
        )

    def test_supports_prewarm_true_for_whisper(self) -> None:
        p = self._make_pipeline("whisper")
        self.assertTrue(p.supports_prewarm())

    def test_supports_prewarm_true_for_vosk(self) -> None:
        p = self._make_pipeline("vosk")
        self.assertTrue(p.supports_prewarm())

    def test_supports_prewarm_false_for_deepgram(self) -> None:
        p = self._make_pipeline("deepgram")
        self.assertFalse(p.supports_prewarm())

    def test_supports_prewarm_false_for_remote(self) -> None:
        p = self._make_pipeline("remote")
        self.assertFalse(p.supports_prewarm())

    def test_supports_prewarm_false_for_dummy(self) -> None:
        p = self._make_pipeline("dummy")
        self.assertFalse(p.supports_prewarm())

    async def test_managed_rejects_non_16khz_audio(self) -> None:
        pipeline = self._make_pipeline("managed")
        pipeline._cfg.sample_rate = 48_000  # pyright: ignore[reportPrivateUsage]

        with self.assertRaisesRegex(ValueError, "16 kHz"):
            pipeline.start(asyncio.get_running_loop())

    async def test_initialize_whisper_returns_before_slow_acquire_and_publishes_progress(self) -> None:
        broadcast = FakeBroadcast()
        stt_q = queue.Queue[_AudioFrame | None]()
        p = SttPipeline(
            stt_queue=cast("queue.Queue[AudioFrame | None]", stt_q),
            cfg=self._make_config("whisper"),
            role="other",
            broadcast_fn=broadcast,
            handle_speech_fn=_noop_handle_speech,
        )
        loop = asyncio.get_running_loop()
        progress_text = "Whisperモデルをダウンロード中です。初回のみ時間がかかる場合があります..."
        acquire_started = threading.Event()
        acquire_can_finish = threading.Event()
        acquire_finished = threading.Event()
        initialize_returned = threading.Event()
        initialize_errors: list[BaseException] = []
        ready_called = False

        async def on_ready() -> None:
            nonlocal ready_called
            ready_called = True

        def acquire_with_progress(_cfg: SttConfig, *, publisher: OutgoingPublisher | None = None) -> object:
            acquire_started.set()
            if publisher is None:
                raise AssertionError("Whisper progress publisher was not provided")
            publisher.publish(StatusMsg(text=progress_text))
            _ = acquire_can_finish.wait()
            acquire_finished.set()
            return object()

        def call_initialize() -> None:
            try:
                p.initialize(loop)
            except BaseException as exc:
                initialize_errors.append(exc)
            finally:
                initialize_returned.set()

        p.on_ready = on_ready

        with patch("app.stt.pipeline.WhisperEngine.acquire", side_effect=acquire_with_progress):
            initialize_thread = threading.Thread(target=call_initialize, name="test-whisper-initialize", daemon=True)
            try:
                initialize_thread.start()
                self.assertTrue(
                    initialize_returned.wait(0.5),
                    "initialize() waited for slow WhisperEngine.acquire to finish",
                )
                self.assertEqual(initialize_errors, [])
                self.assertTrue(acquire_started.wait(0.5))
                await _wait_until(
                    lambda: any(msg["type"] == "status" and msg["text"] == progress_text for msg in broadcast.messages)
                )
                self.assertFalse(ready_called)
                self.assertFalse(acquire_finished.is_set())

                acquire_can_finish.set()
                await _wait_until(lambda: ready_called)
                self.assertTrue(acquire_finished.is_set())
                self.assertTrue(p._whisper_initialized)  # pyright: ignore[reportPrivateUsage]
            finally:
                acquire_can_finish.set()
                initialize_thread.join(timeout=1.0)

    async def test_initialize_vosk_deduplicates_inflight_load_and_calls_ready(self) -> None:
        p = self._make_pipeline("vosk")
        loop = asyncio.get_running_loop()
        acquire_started = threading.Event()
        acquire_can_finish = threading.Event()
        initialize_returned = threading.Event()
        initialize_errors: list[BaseException] = []
        ready_called = asyncio.Event()

        async def on_ready() -> None:
            ready_called.set()

        def acquire_slowly(_cfg: SttConfig, *, publisher: OutgoingPublisher | None = None) -> object:
            _ = publisher
            acquire_started.set()
            _ = acquire_can_finish.wait()
            return object()

        def call_initialize() -> None:
            try:
                p.initialize(loop)
            except BaseException as exc:
                initialize_errors.append(exc)
            finally:
                initialize_returned.set()

        p.on_ready = on_ready

        with patch("app.stt.pipeline.VoskEngine.acquire", side_effect=acquire_slowly) as acquire:
            initialize_thread = threading.Thread(target=call_initialize, name="test-vosk-initialize", daemon=True)
            try:
                initialize_thread.start()
                self.assertTrue(
                    initialize_returned.wait(0.5),
                    "initialize() waited for slow VoskEngine.acquire to finish",
                )
                self.assertEqual(initialize_errors, [])
                self.assertTrue(acquire_started.wait(0.5))

                p.initialize(loop)
                self.assertEqual(acquire.call_count, 1)
                self.assertFalse(ready_called.is_set())

                acquire_can_finish.set()
                _ = await asyncio.wait_for(ready_called.wait(), timeout=0.5)
                self.assertEqual(acquire.call_count, 1)
            finally:
                acquire_can_finish.set()
                initialize_thread.join(timeout=1.0)

    async def test_initialize_vosk_calls_error_without_ready_when_acquire_fails(self) -> None:
        p = self._make_pipeline("vosk")
        loop = asyncio.get_running_loop()
        ready_called = asyncio.Event()
        error_called = asyncio.Event()
        observed_errors: list[Exception] = []
        load_error = RuntimeError("Vosk model is unavailable")

        async def on_ready() -> None:
            ready_called.set()

        async def on_error(error: Exception) -> None:
            observed_errors.append(error)
            error_called.set()

        p.on_ready = on_ready
        p.on_error = on_error

        with patch("app.stt.pipeline.VoskEngine.acquire", side_effect=load_error) as acquire:
            p.initialize(loop)
            _ = await asyncio.wait_for(error_called.wait(), timeout=0.5)

        self.assertEqual(acquire.call_count, 1)
        self.assertEqual(len(observed_errors), 1)
        self.assertIs(observed_errors[0], load_error)
        self.assertFalse(ready_called.is_set())

    async def test_initialize_whisper_calls_on_ready(self) -> None:
        p = self._make_pipeline("whisper")
        loop = asyncio.get_running_loop()
        called = asyncio.Event()

        async def on_ready() -> None:
            called.set()

        p.on_ready = on_ready

        with patch("app.stt.pipeline.WhisperEngine"):
            p.initialize(loop)
            _ = await asyncio.wait_for(called.wait(), timeout=0.5)

        self.assertTrue(called.is_set())
        self.assertIsNone(p.on_ready)

    async def test_initialize_non_whisper_calls_on_ready_immediately(self) -> None:
        p = self._make_pipeline("deepgram")
        loop = asyncio.get_running_loop()
        called = asyncio.Event()

        async def on_ready() -> None:
            called.set()

        p.on_ready = on_ready
        p.initialize(loop)
        _ = await asyncio.wait_for(called.wait(), timeout=0.5)

        self.assertTrue(called.is_set())

    async def test_start_and_stop(self) -> None:
        p = self._make_pipeline("deepgram")
        loop = asyncio.get_running_loop()

        p.start(loop)
        self.assertTrue(p._started)  # pyright: ignore[reportPrivateUsage]
        self.assertIsNotNone(p._pipeline)  # pyright: ignore[reportPrivateUsage]

        p.stop()
        self.assertFalse(p._started)  # pyright: ignore[reportPrivateUsage]
        self.assertIsNone(p._pipeline)  # pyright: ignore[reportPrivateUsage]

    async def test_dummy_start_and_stop_does_not_acquire_whisper(self) -> None:
        p = self._make_pipeline("dummy")
        loop = asyncio.get_running_loop()

        with (
            patch("app.stt.pipeline.WebRtcVadEngine", _MarkedVadEngine),
            patch("app.stt.pipeline.WhisperEngine") as mock_engine,
        ):
            p.start(loop)
            self.assertTrue(p._started)  # pyright: ignore[reportPrivateUsage]
            self.assertIsNotNone(p._pipeline)  # pyright: ignore[reportPrivateUsage]

            p.stop()

            mock_engine.acquire.assert_not_called()  # pyright: ignore[reportAny]
            mock_engine.release.assert_not_called()  # pyright: ignore[reportAny]
            self.assertFalse(p._started)  # pyright: ignore[reportPrivateUsage]
            self.assertIsNone(p._pipeline)  # pyright: ignore[reportPrivateUsage]

    async def test_dummy_emits_one_deterministic_final_after_speech_segment(self) -> None:
        stt_q = queue.Queue[_AudioFrame | None]()
        cfg = self._make_config("dummy")
        cfg.silence_duration = 0.06
        cfg.min_voiced_ms = 60
        received: list[tuple[str, str]] = []

        async def handle_speech(role: str, text: str) -> None:
            received.append((role, text))

        p = SttPipeline(
            stt_queue=cast("queue.Queue[AudioFrame | None]", stt_q),
            cfg=cfg,
            role="other",
            broadcast_fn=FakeBroadcast(),
            handle_speech_fn=handle_speech,
        )
        loop = asyncio.get_running_loop()

        with (
            patch("app.stt.pipeline.WebRtcVadEngine", _MarkedVadEngine),
            patch("app.stt.pipeline.WhisperEngine") as mock_engine,
        ):
            try:
                p.start(loop)
                for index in range(3):
                    stt_q.put(_AudioFrame(pcm=_SPEECH_PCM, is_speech=False, timestamp_ms=index * 30.0))
                for index in range(2):
                    stt_q.put(_AudioFrame(pcm=_SILENCE_PCM, is_speech=False, timestamp_ms=(index + 3) * 30.0))

                await _wait_until(lambda: len(received) >= 1)

                for index in range(3):
                    stt_q.put(_AudioFrame(pcm=_SILENCE_PCM, is_speech=False, timestamp_ms=(index + 5) * 30.0))
                await _wait_until(lambda: stt_q.empty() and p._q2.empty())  # pyright: ignore[reportPrivateUsage]
                await asyncio.sleep(0)

                self.assertEqual(received, [("other", _DUMMY_TRANSCRIPT)])
                mock_engine.acquire.assert_not_called()  # pyright: ignore[reportAny]
                mock_engine.release.assert_not_called()  # pyright: ignore[reportAny]
            finally:
                p.stop()

    async def test_vosk_emits_final_text_after_vad_marked_speech_segment(self) -> None:
        stt_q = queue.Queue[_AudioFrame | None]()
        cfg = self._make_config("vosk")
        cfg.silence_duration = 0.06
        cfg.min_voiced_ms = 60
        received: list[tuple[str, str]] = []

        async def handle_speech(role: str, text: str) -> None:
            received.append((role, text))

        p = SttPipeline(
            stt_queue=cast("queue.Queue[AudioFrame | None]", stt_q),
            cfg=cfg,
            role="other",
            broadcast_fn=FakeBroadcast(),
            handle_speech_fn=handle_speech,
        )
        loop = asyncio.get_running_loop()

        with (
            patch("app.stt.pipeline.WebRtcVadEngine", _MarkedVadEngine),
            patch("app.stt.stages.stt_vosk.VoskEngine.acquire"),
            patch("app.stt.stages.stt_vosk.VoskEngine.transcribe", return_value="ローカル文字起こし"),
        ):
            try:
                p.start(loop)
                for index in range(3):
                    stt_q.put(_AudioFrame(pcm=_LOUD_SPEECH_PCM, is_speech=False, timestamp_ms=index * 30.0))
                for index in range(2):
                    stt_q.put(_AudioFrame(pcm=_SILENCE_PCM, is_speech=False, timestamp_ms=(index + 3) * 30.0))

                await _wait_until(lambda: received == [("other", "ローカル文字起こし")])
            finally:
                p.stop()

    async def test_idempotent_start(self) -> None:
        p = self._make_pipeline("deepgram")
        loop = asyncio.get_running_loop()
        p.start(loop)
        first_pipeline = p._pipeline  # pyright: ignore[reportPrivateUsage]
        p.start(loop)
        self.assertIs(p._pipeline, first_pipeline)  # pyright: ignore[reportPrivateUsage]
        p.stop()

    async def test_idempotent_stop(self) -> None:
        p = self._make_pipeline("deepgram")
        p.stop()
        self.assertFalse(p._started)  # pyright: ignore[reportPrivateUsage]
        self.assertIsNone(p._pipeline)  # pyright: ignore[reportPrivateUsage]

    async def test_shutdown_releases_whisper(self) -> None:
        p = self._make_pipeline("whisper")
        loop = asyncio.get_running_loop()

        with patch("app.stt.pipeline.WhisperEngine") as mock_engine:
            p.initialize(loop)
            p.start(loop)
            p.shutdown()
            mock_engine.release.assert_called_once()  # pyright: ignore[reportAny]
            self.assertFalse(p._whisper_initialized)  # pyright: ignore[reportPrivateUsage]

    async def test_shutdown_during_vosk_load_suppresses_callbacks_and_releases_engine(self) -> None:
        p = self._make_pipeline("vosk")
        loop = asyncio.get_running_loop()
        acquire_started = threading.Event()
        acquire_can_finish = threading.Event()
        release_called = threading.Event()
        ready_called = asyncio.Event()
        error_called = asyncio.Event()

        async def on_ready() -> None:
            ready_called.set()

        async def on_error(_error: Exception) -> None:
            error_called.set()

        def acquire_slowly(_cfg: SttConfig, *, publisher: OutgoingPublisher | None = None) -> object:
            _ = publisher
            acquire_started.set()
            _ = acquire_can_finish.wait()
            return object()

        def release() -> None:
            release_called.set()

        p.on_ready = on_ready
        p.on_error = on_error

        with (
            patch("app.stt.pipeline.VoskEngine.acquire", side_effect=acquire_slowly) as acquire,
            patch("app.stt.pipeline.VoskEngine.release", side_effect=release) as release_engine,
        ):
            try:
                p.initialize(loop)
                self.assertTrue(acquire_started.wait(0.5))
                loader = next(thread for thread in threading.enumerate() if thread.name == "vosk-init-other")
                p.shutdown()

                acquire_can_finish.set()
                await _wait_until(release_called.is_set)
                loader.join(timeout=0.5)
                self.assertFalse(loader.is_alive(), "Vosk loader did not finish after shutdown")
            finally:
                acquire_can_finish.set()

            release_engine.assert_called_once()

        self.assertEqual(acquire.call_count, 1)
        self.assertTrue(release_called.is_set())
        self.assertFalse(ready_called.is_set())
        self.assertFalse(error_called.is_set())


class SttPipelineHotSwapTest(unittest.IsolatedAsyncioTestCase):
    def _make_config(self, backend: str, **kwargs: object) -> SttConfig:
        defaults: dict[str, object] = {
            "whisper_model": "tiny",
            "deepgram_model": "nova-2",
            "language": "ja",
            "vad_sensitivity": 0.5,
            "vad_engine": "webrtc",
            "silence_duration": 0.5,
            "vad_aggressiveness": 2,
            "device": "default",
            "remote_url": "",
            "remote_token": "",
            "sample_rate": 16000,
            "chunk_size": 960,
        }
        defaults.update(kwargs)
        return SttConfig(backend=backend, **defaults)  # pyright: ignore[reportArgumentType]

    def _make_pipeline(self, backend: str = "deepgram", **kwargs: object) -> SttPipeline:
        stt_q = queue.Queue[_AudioFrame | None]()
        return SttPipeline(
            stt_queue=cast("queue.Queue[AudioFrame | None]", stt_q),
            cfg=self._make_config(backend, **kwargs),
            role="other",
            broadcast_fn=FakeBroadcast(),
            handle_speech_fn=_noop_handle_speech,
        )

    async def test_apply_config_vad_swap(self) -> None:
        p = self._make_pipeline("deepgram")
        loop = asyncio.get_running_loop()
        p.start(loop)
        old_pipeline = p._pipeline  # pyright: ignore[reportPrivateUsage]
        assert old_pipeline is not None
        old_vad = old_pipeline.stages[0]

        new_cfg = self._make_config("deepgram", vad_aggressiveness=3)
        p.apply_config(new_cfg)

        new_pipeline = p._pipeline  # pyright: ignore[reportPrivateUsage]
        assert new_pipeline is not None
        new_vad = new_pipeline.stages[0]

        self.assertIs(new_pipeline, old_pipeline)
        self.assertIsNot(new_vad, old_vad)
        p.stop()

    async def test_apply_config_switches_from_webrtc_to_silero_threshold(self) -> None:
        p = self._make_pipeline("deepgram")
        loop = asyncio.get_running_loop()
        p.start(loop)

        new_cfg = self._make_config(
            "deepgram",
            vad_engine="silero",
            vad_sensitivity=0.65,
        )
        with patch(
            "app.stt.pipeline.SileroVadEngine",
            return_value=_MarkedVadEngine(2),
        ) as silero:
            p.apply_config(new_cfg)

        silero.assert_called_once_with(0.65)
        p.stop()

    async def test_apply_config_stt_backend_swap(self) -> None:
        p = self._make_pipeline("deepgram")
        loop = asyncio.get_running_loop()
        p.start(loop)
        old_pipeline = p._pipeline  # pyright: ignore[reportPrivateUsage]
        assert old_pipeline is not None
        old_stt = old_pipeline.stages[1]

        new_cfg = self._make_config("remote")
        p.apply_config(new_cfg)

        new_pipeline = p._pipeline  # pyright: ignore[reportPrivateUsage]
        assert new_pipeline is not None
        new_stt = new_pipeline.stages[1]

        self.assertIs(new_pipeline, old_pipeline)
        self.assertIsNot(new_stt, old_stt)
        p.stop()

    async def test_apply_config_openai_model_swap(self) -> None:
        p = self._make_pipeline("openai", openai_model="gpt-4o-transcribe")
        p.start(asyncio.get_running_loop())
        old_pipeline = p._pipeline  # pyright: ignore[reportPrivateUsage]
        assert old_pipeline is not None
        old_stt = old_pipeline.stages[1]

        p.apply_config(self._make_config("openai", openai_model="gpt-4o-mini-transcribe"))

        new_pipeline = p._pipeline  # pyright: ignore[reportPrivateUsage]
        assert new_pipeline is not None
        self.assertIsNot(new_pipeline.stages[1], old_stt)
        p.stop()

    async def test_apply_config_whisper_acquire_release(self) -> None:
        p = self._make_pipeline("whisper")
        loop = asyncio.get_running_loop()

        with patch("app.stt.pipeline.WhisperEngine") as mock_engine:
            p.initialize(loop)
            p.start(loop)
            acquire_calls = mock_engine.acquire.call_count  # pyright: ignore[reportAny]

            # swap to deepgram should release whisper
            new_cfg = self._make_config("deepgram")
            p.apply_config(new_cfg)
            mock_engine.release.assert_called_once()  # pyright: ignore[reportAny]
            self.assertFalse(p._whisper_initialized)  # pyright: ignore[reportPrivateUsage]

            # swap back to whisper should acquire again
            newer_cfg = self._make_config("whisper")
            p.apply_config(newer_cfg)
            self.assertEqual(mock_engine.acquire.call_count, acquire_calls + 1)  # pyright: ignore[reportAny]
            self.assertTrue(p._whisper_initialized)  # pyright: ignore[reportPrivateUsage]

            p.stop()

    async def test_apply_config_noop_when_not_started(self) -> None:
        p = self._make_pipeline("deepgram")
        new_cfg = self._make_config("remote")
        p.apply_config(new_cfg)
        self.assertIsNone(p._pipeline)  # pyright: ignore[reportPrivateUsage]

    async def test_apply_config_noop_when_same_config(self) -> None:
        p = self._make_pipeline("deepgram")
        loop = asyncio.get_running_loop()
        p.start(loop)
        old_pipeline = p._pipeline  # pyright: ignore[reportPrivateUsage]

        p.apply_config(self._make_config("deepgram"))
        self.assertIs(p._pipeline, old_pipeline)  # pyright: ignore[reportPrivateUsage]
        p.stop()


if __name__ == "__main__":
    _ = unittest.main()
