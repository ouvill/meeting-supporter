# pyright: reportPrivateUsage=false, reportAny=false
"""Tests for faster-whisper CUDA load fallback behavior."""

import queue
import unittest
from collections.abc import Coroutine
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock, call, patch

import numpy as np

from app.core.config import SttConfig
from app.core.messages import ErrorMsg, OutgoingMessage, StatusMsg
from app.stt.stages.stt_whisper import WhisperEngine, WhisperStage, _WhisperJob
from app.stt.transcript_judge import AudioEvidence

if TYPE_CHECKING:
    from faster_whisper import WhisperModel


class _FakePublisher:
    """Captures published StatusMsg texts for assertions."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.errors: list[str] = []

    def publish(self, msg: OutgoingMessage) -> None:
        if isinstance(msg, StatusMsg):
            self.messages.append(msg.text)
        elif isinstance(msg, ErrorMsg):
            self.errors.append(msg.text)

    def schedule(self, coro: Coroutine[object, object, object]) -> None:
        coro.close()


def _make_config(*, device: str = "auto") -> SttConfig:
    return SttConfig(
        backend="whisper",
        whisper_model="tiny",
        deepgram_model="nova-3",
        language="ja",
        vad_sensitivity=0.4,
        silence_duration=0.4,
        vad_aggressiveness=2,
        device=device,
        remote_url="",
        remote_token="",
        sample_rate=16000,
        chunk_size=1600,
    )


class _CudaFailureSegments:
    def __iter__(self) -> "_CudaFailureSegments":
        return self

    def __next__(self) -> object:
        raise RuntimeError("CUDA failed with CUDA driver version is insufficient for CUDA runtime version")


class WhisperCudaFallbackTest(unittest.TestCase):
    def test_cuda_driver_runtime_failure_retries_on_cpu_int8(self) -> None:
        """CUDA driver/runtime load failure falls back to CPU int8 and returns the CPU model."""
        fake_model_path = "/fake/cache/models/tiny"
        cuda_error = RuntimeError("CUDA failed with CUDA driver version is insufficient for CUDA runtime version")

        for device in ("auto", "cuda"):
            with self.subTest(device=device):
                publisher = _FakePublisher()
                cpu_model = object()

                with (
                    patch("os.path.isdir", return_value=False),
                    patch("app.stt.stages.stt_whisper.os.path.isfile", return_value=True),
                    patch("faster_whisper.utils.download_model", return_value=fake_model_path) as mock_download,
                    patch("ctranslate2.get_supported_compute_types", return_value={"float16"}) as mock_compute_types,
                    patch("faster_whisper.WhisperModel", side_effect=[cuda_error, cpu_model]) as mock_wm,
                ):
                    model = WhisperEngine._load_model(_make_config(device=device), publisher=publisher)

                self.assertIs(model, cpu_model)
                mock_download.assert_called_once_with("tiny", local_files_only=True)
                if device == "auto":
                    mock_compute_types.assert_called_once_with("cuda")
                else:
                    mock_compute_types.assert_not_called()
                self.assertEqual(
                    mock_wm.mock_calls,
                    [
                        call(fake_model_path, device="cuda", compute_type="float16"),
                        call(fake_model_path, device="cpu", compute_type="int8"),
                    ],
                )
                self.assertTrue(
                    any("CUDA" in message and "CPU" in message for message in publisher.messages),
                    publisher.messages,
                )
                self.assertEqual(publisher.messages[-1], "Whisperモデルの準備が完了しました")

    def test_non_cuda_runtime_error_from_cuda_load_propagates(self) -> None:
        """Non-CUDA load errors are not swallowed or retried on CPU."""
        fake_model_path = "/fake/cache/models/tiny"
        model_error = RuntimeError("model.bin is corrupt")
        publisher = _FakePublisher()

        with (
            patch("os.path.isdir", return_value=False),
            patch("app.stt.stages.stt_whisper.os.path.isfile", return_value=True),
            patch("faster_whisper.utils.download_model", return_value=fake_model_path),
            patch("ctranslate2.get_supported_compute_types", return_value={"float16"}) as mock_compute_types,
            patch("faster_whisper.WhisperModel", side_effect=model_error) as mock_wm,
        ):
            with self.assertRaisesRegex(RuntimeError, "model\\.bin is corrupt"):
                _ = WhisperEngine._load_model(_make_config(device="auto"), publisher=publisher)

        mock_compute_types.assert_called_once_with("cuda")
        mock_wm.assert_called_once_with(fake_model_path, device="cuda", compute_type="float16")
        self.assertNotIn("Whisperモデルの準備が完了しました", publisher.messages)
        self.assertFalse(any("CPU" in message for message in publisher.messages), publisher.messages)

    def test_worker_retries_cpu_when_cuda_segment_iteration_fails(self) -> None:
        """CUDA RuntimeError raised while materializing segments swaps in CPU model and delivers text."""
        fake_model_path = "/fake/cache/models/tiny"
        publisher = _FakePublisher()
        handled_speech: list[tuple[str, str]] = []

        async def _noop() -> None:
            return None

        def handle_speech(role: str, text: str) -> Coroutine[object, object, object]:
            handled_speech.append((role, text))
            return _noop()

        stage = SimpleNamespace(
            cfg=_make_config(device="cuda"),
            publisher=publisher,
            role="participant",
            is_stopped=False,
            current_run_token=7,
            handle_speech=handle_speech,
        )
        cuda_transcribe = Mock(return_value=(_CudaFailureSegments(), object()))
        cuda_model = SimpleNamespace(transcribe=cuda_transcribe)
        cpu_segment = SimpleNamespace(
            text="こんにちは",
            avg_logprob=-0.1,
            no_speech_prob=0.01,
            compression_ratio=1.0,
        )
        cpu_transcribe = Mock(return_value=([cpu_segment], object()))
        cpu_model = SimpleNamespace(transcribe=cpu_transcribe)
        work_queue: queue.Queue[_WhisperJob] = queue.Queue()
        work_queue.put(
            _WhisperJob(
                stage=cast(WhisperStage, cast(object, stage)),
                run_token=7,
                audio_np=np.zeros(1600, dtype=np.float32),
                audio=AudioEvidence(480, 0.8, -20.0, 600),
            )
        )

        with WhisperEngine._lock:
            WhisperEngine._model = cast("WhisperModel", cast(object, cuda_model))
            WhisperEngine._queue = work_queue
            WhisperEngine._running = False
            WhisperEngine._thread = None
            WhisperEngine._ref_count = 0
            WhisperEngine._active_device = "cuda"
            WhisperEngine._model_path = fake_model_path

        try:
            with patch("faster_whisper.WhisperModel", return_value=cpu_model) as mock_wm:
                WhisperEngine._engine_worker()
        finally:
            with WhisperEngine._lock:
                WhisperEngine._model = None
                WhisperEngine._queue = None
                WhisperEngine._running = False
                WhisperEngine._thread = None
                WhisperEngine._ref_count = 0
                WhisperEngine._active_device = None
                WhisperEngine._model_path = None

        mock_wm.assert_called_once_with(fake_model_path, device="cpu", compute_type="int8")
        cuda_transcribe.assert_called_once()
        cpu_transcribe.assert_called_once()
        self.assertEqual(handled_speech, [("participant", "こんにちは")])
        self.assertEqual(publisher.errors, [])
        self.assertTrue(
            any("CUDA" in message and "CPU" in message for message in publisher.messages),
            publisher.messages,
        )

    def test_worker_drops_suspicious_phrase_with_weak_model_evidence(self) -> None:
        publisher = _FakePublisher()
        handled_speech: list[tuple[str, str]] = []

        async def _noop() -> None:
            return None

        def handle_speech(role: str, text: str) -> Coroutine[object, object, object]:
            handled_speech.append((role, text))
            return _noop()

        stage = SimpleNamespace(
            cfg=_make_config(),
            publisher=publisher,
            role="participant",
            is_stopped=False,
            current_run_token=11,
            handle_speech=handle_speech,
        )
        segment = SimpleNamespace(
            text="ご視聴ありがとうございました",
            avg_logprob=-0.1,
            no_speech_prob=0.65,
            compression_ratio=1.1,
        )
        transcribe = Mock(return_value=([segment], object()))
        model = SimpleNamespace(transcribe=transcribe)
        work_queue: queue.Queue[_WhisperJob] = queue.Queue()
        work_queue.put(
            _WhisperJob(
                stage=cast(WhisperStage, cast(object, stage)),
                run_token=11,
                audio_np=np.zeros(1600, dtype=np.float32),
                audio=AudioEvidence(480, 0.8, -20.0, 600),
            )
        )

        with WhisperEngine._lock:
            WhisperEngine._model = cast("WhisperModel", cast(object, model))
            WhisperEngine._queue = work_queue
            WhisperEngine._running = False
            WhisperEngine._thread = None
            WhisperEngine._ref_count = 0
            WhisperEngine._active_device = "cpu"
            WhisperEngine._model_path = "/fake/cache/models/tiny"

        try:
            WhisperEngine._engine_worker()
        finally:
            with WhisperEngine._lock:
                WhisperEngine._model = None
                WhisperEngine._queue = None
                WhisperEngine._running = False
                WhisperEngine._thread = None
                WhisperEngine._ref_count = 0
                WhisperEngine._active_device = None
                WhisperEngine._model_path = None

        transcribe.assert_called_once()
        self.assertEqual(handled_speech, [])
        self.assertEqual(publisher.errors, [])


if __name__ == "__main__":
    _ = unittest.main()
