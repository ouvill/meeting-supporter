"""Safe sherpa-onnx import boundary shared by local ONNX speech models."""

from __future__ import annotations

import ctypes
import importlib
import os
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

_windows_onnxruntime_handle: object | None = None


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


def import_sherpa_onnx() -> ModuleType:
    """Import sherpa-onnx after resolving its native runtime deterministically."""
    _load_windows_onnxruntime()
    try:
        return importlib.import_module("sherpa_onnx")
    except ImportError as error:
        raise RuntimeError("ローカルONNX音声処理には`sherpa-onnx`が必要です。`uv sync`を実行してください。") from error


__all__ = ["import_sherpa_onnx"]
