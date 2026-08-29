"""Focused contracts for the local ReazonSpeech K2-v2 backend."""

from __future__ import annotations

import asyncio
import ctypes
import importlib
import queue
import tempfile
import threading
import unittest
from collections.abc import Coroutine
from concurrent.futures import Future as ConcurrentFuture
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, cast, final
from unittest.mock import patch

import numpy as np
from numpy.typing import NDArray

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import OutgoingMessage
from app.stt import reazonspeech_model
from app.stt.reazonspeech_model import (
    REAZONSPEECH_MAX_AUDIO_SECONDS,
    REAZONSPEECH_MODEL_FILES,
    REAZONSPEECH_PAD_SECONDS,
    REAZONSPEECH_SAMPLE_RATE,
    OfflineRecognizer,
    load_reazonspeech_recognizer,
    transcribe_reazonspeech,
)
from app.stt.stages.stt_reazonspeech import (
    ReazonSpeechEngine,
    ReazonSpeechStage,
    _ReazonSpeechJob,  # pyright: ignore[reportPrivateUsage]
    should_drop_reazonspeech_transcript,
)

_LOUD_PCM = (12000).to_bytes(2, "little", signed=True) * 480
_SILENT_PCM = b"\x00\x00" * 480


class _QueuedJob(Protocol):
    audio: NDArray[np.float32]


def _config(**changes: object) -> SttConfig:
    values: dict[str, object] = {
        "backend": "reazonspeech",
        "whisper_model": "tiny",
        "deepgram_model": "nova-3",
        "language": "ja",
        "vad_sensitivity": 0.5,
        "silence_duration": 0.06,
        "vad_aggressiveness": 2,
        "device": "auto",
        "remote_url": "",
        "remote_token": "",
        "sample_rate": REAZONSPEECH_SAMPLE_RATE,
        "chunk_size": 1600,
        "min_voiced_ms": 60,
        "min_voiced_ratio": 0.35,
        "min_rms_dbfs": -45.0,
    }
    values.update(changes)
    return SttConfig(**values)  # pyright: ignore[reportArgumentType]


@final
class _RecordingPublisher:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.messages: list[dict[str, object]] = []
        self.futures: list[asyncio.Future[object] | ConcurrentFuture[object]] = []

    def publish(self, msg: OutgoingMessage) -> None:
        self.messages.append(msg.model_dump())

    def schedule(self, coro: Coroutine[object, object, object]) -> None:
        self.futures.append(asyncio.run_coroutine_threadsafe(coro, self._loop))


@final
class _FakeStream:
    def __init__(self) -> None:
        self.result = SimpleNamespace(text="  日本語の認識結果  ")
        self.sample_rate: int | None = None
        self.samples: NDArray[np.float32] | None = None

    def accept_waveform(self, sample_rate: int, samples: NDArray[np.float32]) -> None:
        self.sample_rate = sample_rate
        self.samples = samples


@final
class _FakeRecognizer:
    def __init__(self) -> None:
        self.stream = _FakeStream()
        self.decoded = False

    def create_stream(self) -> _FakeStream:
        return self.stream

    def decode_stream(self, stream: _FakeStream) -> None:
        assert stream is self.stream
        self.decoded = True


class ReazonSpeechModelBoundaryTest(unittest.TestCase):
    def test_transcribe_pads_mono_16khz_audio_and_returns_trimmed_text(self) -> None:
        recognizer = _FakeRecognizer()
        audio = np.ones(1600, dtype=np.float32)

        text = transcribe_reazonspeech(recognizer, audio)  # pyright: ignore[reportArgumentType]

        assert text == "日本語の認識結果"
        assert recognizer.decoded is True
        assert recognizer.stream.sample_rate == 16000
        assert recognizer.stream.samples is not None
        assert recognizer.stream.samples.shape == (1600 + 2 * 14_400,)

    def test_load_recognizer_uses_only_the_official_int8_model_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp)
            for filename in REAZONSPEECH_MODEL_FILES.values():
                _ = (snapshot / filename).write_bytes(b"model")
            calls: list[dict[str, object]] = []

            class _Factory:
                @staticmethod
                def from_transducer(**kwargs: object) -> _FakeRecognizer:
                    calls.append(kwargs)
                    return _FakeRecognizer()

            with (
                patch("app.stt.reazonspeech_model._load_windows_onnxruntime") as load_windows_runtime,
                patch(
                    "app.stt.reazonspeech_model.importlib.import_module",
                    return_value=SimpleNamespace(OfflineRecognizer=_Factory),
                ),
            ):
                _ = load_reazonspeech_recognizer(str(snapshot))

        load_windows_runtime.assert_called_once()
        assert len(calls) == 1
        call = calls[0]
        assert call["provider"] == "cpu"
        assert call["sample_rate"] == 16000
        assert call["decoding_method"] == "greedy_search"
        assert str(call["encoder"]).endswith("encoder-epoch-99-avg-1.int8.onnx")
        assert str(call["decoder"]).endswith("decoder-epoch-99-avg-1.int8.onnx")
        assert str(call["joiner"]).endswith("joiner-epoch-99-avg-1.int8.onnx")

    def test_windows_runtime_loader_prefers_the_wheel_owned_dll(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package_dir = Path(tmp) / "onnxruntime"
            runtime_dll = package_dir / "capi" / "onnxruntime.dll"
            runtime_dll.parent.mkdir(parents=True)
            _ = runtime_dll.write_bytes(b"runtime")
            module = SimpleNamespace(__file__=str(package_dir / "__init__.py"))

            with (
                patch.object(reazonspeech_model, "os", SimpleNamespace(name="nt")),
                patch.object(reazonspeech_model, "_windows_onnxruntime_handle", None),
                patch.object(importlib, "import_module", return_value=module),
                patch.object(ctypes, "WinDLL", create=True) as load_library,
            ):
                reazonspeech_model._load_windows_onnxruntime()  # pyright: ignore[reportPrivateUsage]

        load_library.assert_called_once_with(str(runtime_dll))

    def test_engine_rejects_unsupported_audio_contract_before_loading(self) -> None:
        with self.assertRaisesRegex(ValueError, "日本語"):
            ReazonSpeechEngine.acquire(_config(language="en"))
        with self.assertRaisesRegex(ValueError, "16 kHz"):
            ReazonSpeechEngine.acquire(_config(sample_rate=48_000))

    def test_replacement_acquire_waits_for_retiring_worker(self) -> None:
        decode_started = threading.Event()
        allow_decode = threading.Event()
        replacement_loading = threading.Event()
        replacement_acquired = threading.Event()
        load_calls = 0

        def load_recognizer(_snapshot: str) -> _FakeRecognizer:
            nonlocal load_calls
            load_calls += 1
            if load_calls == 2:
                replacement_loading.set()
            return _FakeRecognizer()

        def transcribe(_model: object, _audio: NDArray[np.float32]) -> str:
            decode_started.set()
            assert allow_decode.wait(timeout=2.0)
            return ""

        def acquire_replacement() -> None:
            ReazonSpeechEngine.acquire(_config())
            replacement_acquired.set()

        with (
            patch(
                "app.stt.stages.stt_reazonspeech.cached_reazonspeech_snapshot",
                return_value="/shared/huggingface-cache/snapshots/reazonspeech",
            ),
            patch(
                "app.stt.stages.stt_reazonspeech.load_reazonspeech_recognizer",
                side_effect=load_recognizer,
            ),
            patch(
                "app.stt.stages.stt_reazonspeech.transcribe_reazonspeech",
                side_effect=transcribe,
            ),
        ):
            ReazonSpeechEngine.acquire(_config())
            stage = cast(
                ReazonSpeechStage,
                cast(object, SimpleNamespace(is_stopped=False, current_run_token=1)),
            )
            ReazonSpeechEngine.enqueue(_ReazonSpeechJob(stage, 1, np.ones(1600, dtype=np.float32)))
            assert decode_started.wait(timeout=1.0)
            stop_event = ReazonSpeechEngine._stop_event  # pyright: ignore[reportPrivateUsage]
            assert stop_event is not None

            release_thread = threading.Thread(target=ReazonSpeechEngine.release, daemon=True)
            release_thread.start()
            assert stop_event.wait(timeout=1.0)

            replacement_thread = threading.Thread(target=acquire_replacement, daemon=True)
            replacement_thread.start()
            assert not replacement_loading.wait(timeout=0.1)

            allow_decode.set()
            assert replacement_loading.wait(timeout=1.0)
            assert replacement_acquired.wait(timeout=1.0)
            release_thread.join(timeout=1.0)
            replacement_thread.join(timeout=1.0)
            ReazonSpeechEngine.release()

        assert load_calls == 2
        assert not release_thread.is_alive()
        assert not replacement_thread.is_alive()


class ReazonSpeechStageTest(unittest.IsolatedAsyncioTestCase):
    async def test_vad_segment_preserves_preroll_and_is_enqueued(self) -> None:
        in_q = queue.Queue[AudioFrame | None]()
        publisher = _RecordingPublisher(asyncio.get_running_loop())

        async def handle_speech(_role: str, _text: str) -> None:
            return None

        stage = ReazonSpeechStage(in_q, _config(), "other", publisher, handle_speech)
        frames = [
            *[AudioFrame(pcm=_SILENT_PCM, is_speech=False, timestamp_ms=index * 30.0) for index in range(5)],
            *[AudioFrame(pcm=_LOUD_PCM, is_speech=True, timestamp_ms=(index + 5) * 30.0) for index in range(3)],
        ]

        with patch("app.stt.stages.stt_reazonspeech.ReazonSpeechEngine.enqueue") as enqueue:
            stage.start()
            for frame in frames:
                in_q.put(frame)
            in_q.put(None)
            stage.join(timeout=1.0)

        assert stage.running is False
        enqueue.assert_called_once()
        job = cast(_QueuedJob, enqueue.call_args.args[0])
        assert job.audio.shape == (8 * 480,)
        assert not bool(job.audio[: 5 * 480].any())

    async def test_long_speech_is_split_below_the_model_limit(self) -> None:
        in_q = queue.Queue[AudioFrame | None]()
        publisher = _RecordingPublisher(asyncio.get_running_loop())

        async def handle_speech(_role: str, _text: str) -> None:
            return None

        stage = ReazonSpeechStage(in_q, _config(min_voiced_ms=30), "other", publisher, handle_speech)
        max_unpadded_samples = int(
            (REAZONSPEECH_MAX_AUDIO_SECONDS - 2 * REAZONSPEECH_PAD_SECONDS) * REAZONSPEECH_SAMPLE_RATE
        )

        with patch("app.stt.stages.stt_reazonspeech.ReazonSpeechEngine.enqueue") as enqueue:
            stage.start()
            for index in range(1000):
                in_q.put(AudioFrame(pcm=_LOUD_PCM, is_speech=True, timestamp_ms=index * 30.0))
            in_q.put(None)
            stage.join(timeout=2.0)

        assert enqueue.call_count == 2
        jobs = (cast(_QueuedJob, call.args[0]) for call in enqueue.call_args_list)
        assert all(job.audio.size <= max_unpadded_samples for job in jobs)

    async def test_retired_inference_error_is_not_published(self) -> None:
        publisher = _RecordingPublisher(asyncio.get_running_loop())

        async def handle_speech(_role: str, _text: str) -> None:
            return None

        stage = ReazonSpeechStage(
            queue.Queue[AudioFrame | None](),
            _config(),
            "other",
            publisher,
            handle_speech,
        )
        work_queue = queue.Queue[_ReazonSpeechJob]()
        work_queue.put(_ReazonSpeechJob(stage, stage.current_run_token, np.ones(1600, dtype=np.float32)))
        stop_event = threading.Event()
        stop_event.set()

        def stop_and_fail(_model: object, _audio: NDArray[np.float32]) -> str:
            stage.stop()
            raise RuntimeError("synthetic inference failure")

        with patch(
            "app.stt.stages.stt_reazonspeech.transcribe_reazonspeech",
            side_effect=stop_and_fail,
        ):
            ReazonSpeechEngine._engine_worker(  # pyright: ignore[reportPrivateUsage]
                cast(OfflineRecognizer, cast(object, _FakeRecognizer())),
                work_queue,
                stop_event,
            )

        assert publisher.messages == []

    def test_suspicious_phrase_filter_is_preserved(self) -> None:
        cfg = _config()
        assert should_drop_reazonspeech_transcript(cfg, "ありがとうございました") is True
        assert should_drop_reazonspeech_transcript(cfg, "次の議題を確認します") is False


if __name__ == "__main__":
    _ = unittest.main()
