"""Pinned ReazonSpeech K2-v2 model boundary backed by sherpa-onnx."""

from __future__ import annotations

import ctypes
import importlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Final, Protocol, cast

import numpy as np
from numpy.typing import NDArray

REAZONSPEECH_MODEL_ID: Final = "reazonspeech-k2-v2-int8"
REAZONSPEECH_REPOSITORY: Final = "reazon-research/reazonspeech-k2-v2"
REAZONSPEECH_SAMPLE_RATE: Final = 16_000
REAZONSPEECH_PAD_SECONDS: Final = 0.9
REAZONSPEECH_MAX_AUDIO_SECONDS: Final = 30.0
REAZONSPEECH_MODEL_FILES: Final[dict[str, str]] = {
    "tokens": "tokens.txt",
    "encoder": "encoder-epoch-99-avg-1.int8.onnx",
    "decoder": "decoder-epoch-99-avg-1.int8.onnx",
    "joiner": "joiner-epoch-99-avg-1.int8.onnx",
}
REAZONSPEECH_ALLOW_PATTERNS: Final[list[str]] = list(REAZONSPEECH_MODEL_FILES.values())
REAZONSPEECH_DOWNLOAD_BYTES: Final = 160_372_200
_windows_onnxruntime_handle: object | None = None


class OfflineRecognizerResult(Protocol):
    text: str


class OfflineRecognizerStream(Protocol):
    result: OfflineRecognizerResult

    def accept_waveform(self, sample_rate: int, samples: NDArray[np.float32]) -> None: ...


class OfflineRecognizer(Protocol):
    def create_stream(self) -> OfflineRecognizerStream: ...

    def decode_stream(self, stream: OfflineRecognizerStream) -> None: ...


class _OfflineRecognizerFactory(Protocol):
    @staticmethod
    def from_transducer(
        *,
        tokens: str,
        encoder: str,
        decoder: str,
        joiner: str,
        num_threads: int,
        sample_rate: int,
        feature_dim: int,
        decoding_method: str,
        provider: str,
    ) -> OfflineRecognizer: ...


def _validate_snapshot(path: str) -> str:
    snapshot = Path(path)
    missing = [name for name in REAZONSPEECH_ALLOW_PATTERNS if not (snapshot / name).is_file()]
    if missing:
        raise FileNotFoundError("ReazonSpeechモデルのキャッシュが不完全です。")
    return str(snapshot)


def download_reazonspeech_snapshot(*, local_files_only: bool = False) -> str:
    """Resolve the pinned model files in Hugging Face's shared cache."""
    from huggingface_hub import snapshot_download  # pyright: ignore[reportUnknownVariableType]

    snapshot = snapshot_download(
        repo_id=REAZONSPEECH_REPOSITORY,
        allow_patterns=REAZONSPEECH_ALLOW_PATTERNS,
        local_files_only=local_files_only,
    )
    return _validate_snapshot(snapshot)


def cached_reazonspeech_snapshot() -> str | None:
    """Return the complete cached snapshot without performing network I/O."""
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        return download_reazonspeech_snapshot(local_files_only=True)
    except (LocalEntryNotFoundError, FileNotFoundError):
        return None


def _load_windows_onnxruntime() -> None:
    """Preload the wheel-owned runtime before Windows can select a stale system DLL."""
    global _windows_onnxruntime_handle
    if os.name != "nt" or _windows_onnxruntime_handle is not None:
        return

    runtime_module = importlib.import_module("onnxruntime")
    module_file = getattr(runtime_module, "__file__", None)
    if not isinstance(module_file, str):
        raise RuntimeError("ONNX Runtimeを準備できませんでした。")
    runtime_dll = Path(module_file).parent / "capi" / "onnxruntime.dll"
    if not runtime_dll.is_file():
        raise RuntimeError("ONNX Runtimeを準備できませんでした。")
    load_library = cast(Callable[[str], object], getattr(ctypes, "WinDLL"))
    _windows_onnxruntime_handle = load_library(str(runtime_dll))


def load_reazonspeech_recognizer(snapshot_path: str) -> OfflineRecognizer:
    """Create the official K2-v2 int8 transducer through sherpa-onnx."""
    _load_windows_onnxruntime()
    try:
        module = importlib.import_module("sherpa_onnx")
    except ImportError as exc:
        raise RuntimeError(
            "ReazonSpeech backend requires the `sherpa-onnx` Python package. Install dependencies with `uv sync`."
        ) from exc

    factory = cast(_OfflineRecognizerFactory, getattr(module, "OfflineRecognizer"))
    snapshot = Path(_validate_snapshot(snapshot_path))
    return factory.from_transducer(
        tokens=str(snapshot / REAZONSPEECH_MODEL_FILES["tokens"]),
        encoder=str(snapshot / REAZONSPEECH_MODEL_FILES["encoder"]),
        decoder=str(snapshot / REAZONSPEECH_MODEL_FILES["decoder"]),
        joiner=str(snapshot / REAZONSPEECH_MODEL_FILES["joiner"]),
        num_threads=1,
        sample_rate=REAZONSPEECH_SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
        provider="cpu",
    )


def transcribe_reazonspeech(
    recognizer: OfflineRecognizer,
    audio: NDArray[np.float32],
) -> str:
    """Transcribe one mono 16 kHz segment using the official padding contract."""
    padding = int(REAZONSPEECH_PAD_SECONDS * REAZONSPEECH_SAMPLE_RATE)
    padded = np.pad(audio, pad_width=padding, mode="constant")
    stream = recognizer.create_stream()
    stream.accept_waveform(REAZONSPEECH_SAMPLE_RATE, padded)
    recognizer.decode_stream(stream)
    return stream.result.text.strip()


__all__ = [
    "OfflineRecognizer",
    "REAZONSPEECH_ALLOW_PATTERNS",
    "REAZONSPEECH_DOWNLOAD_BYTES",
    "REAZONSPEECH_MAX_AUDIO_SECONDS",
    "REAZONSPEECH_MODEL_ID",
    "REAZONSPEECH_REPOSITORY",
    "REAZONSPEECH_SAMPLE_RATE",
    "cached_reazonspeech_snapshot",
    "download_reazonspeech_snapshot",
    "load_reazonspeech_recognizer",
    "transcribe_reazonspeech",
]
