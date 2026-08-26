"""Focused contracts for the local ReazonSpeech K2-v2 backend."""

from __future__ import annotations

import asyncio
import ctypes
import importlib
import queue
import tempfile
import unittest
from collections.abc import Coroutine
from concurrent.futures import Future as ConcurrentFuture
from pathlib import Path
from types import SimpleNamespace
from typing import final
from unittest.mock import patch

import numpy as np

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import OutgoingMessage
from app.stt import reazonspeech_model
from app.stt.reazonspeech_model import (
    REAZONSPEECH_MODEL_FILES,
    REAZONSPEECH_SAMPLE_RATE,
    load_reazonspeech_recognizer,
    transcribe_reazonspeech,
)
from app.stt.stages.stt_reazonspeech import (
    ReazonSpeechStage,
    should_drop_reazonspeech_transcript,
)

_LOUD_PCM = (12000).to_bytes(2, "little", signed=True) * 480


def _config() -> SttConfig:
    return SttConfig(
        backend="reazonspeech",
        whisper_model="tiny",
        deepgram_model="nova-2",
        language="ja",
        vad_sensitivity=0.5,
        silence_duration=0.06,
        vad_aggressiveness=2,
        device="auto",
        remote_url="",
        remote_token="",
        sample_rate=REAZONSPEECH_SAMPLE_RATE,
        chunk_size=1600,
        min_voiced_ms=60,
        min_voiced_ratio=0.35,
        min_rms_dbfs=-45.0,
    )


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
        self.result: SimpleNamespace = SimpleNamespace(text="  日本語の認識結果  ")
        self.sample_rate: int | None = None
        self.samples: np.ndarray[tuple[int], np.dtype[np.float32]] | None = None

    def accept_waveform(
        self,
        sample_rate: int,
        samples: np.ndarray[tuple[int], np.dtype[np.float32]],
    ) -> None:
        self.sample_rate = sample_rate
        self.samples = samples


@final
class _FakeRecognizer:
    def __init__(self) -> None:
        self.stream: _FakeStream = _FakeStream()
        self.decoded: bool = False

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

    def test_load_recognizer_uses_only_the_pinned_int8_model_files(self) -> None:
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
                patch.object(
                    reazonspeech_model,
                    "os",
                    SimpleNamespace(name="nt"),
                ),
                patch.object(
                    reazonspeech_model,
                    "_windows_onnxruntime_handle",
                    None,
                ),
                patch.object(
                    importlib,
                    "import_module",
                    return_value=module,
                ),
                patch.object(
                    ctypes,
                    "WinDLL",
                    create=True,
                ) as load_library,
            ):
                reazonspeech_model._load_windows_onnxruntime()  # pyright: ignore[reportPrivateUsage]

        load_library.assert_called_once_with(str(runtime_dll))


class ReazonSpeechStageTest(unittest.IsolatedAsyncioTestCase):
    async def test_vad_confirmed_segment_is_enqueued_for_shared_inference(self) -> None:
        in_q = queue.Queue[AudioFrame | None]()
        publisher = _RecordingPublisher(asyncio.get_running_loop())

        async def handle_speech(_role: str, _text: str) -> None:
            return None

        stage = ReazonSpeechStage(in_q, _config(), "other", publisher, handle_speech)
        stage.start()
        for index in range(3):
            in_q.put(AudioFrame(pcm=_LOUD_PCM, is_speech=True, timestamp_ms=index * 30.0))
        in_q.put(None)

        with patch("app.stt.stages.stt_reazonspeech.ReazonSpeechEngine.enqueue") as enqueue:
            stage.join(timeout=1.0)

        assert stage.running is False
        enqueue.assert_called_once()

    def test_suspicious_phrase_filter_is_preserved_for_reazonspeech(self) -> None:
        cfg = _config()
        assert should_drop_reazonspeech_transcript(cfg, "ありがとうございました") is True
        assert should_drop_reazonspeech_transcript(cfg, "次の議題を確認します") is False


if __name__ == "__main__":
    _ = unittest.main()
