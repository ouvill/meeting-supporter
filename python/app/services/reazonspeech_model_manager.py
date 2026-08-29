"""Managed ReazonSpeech K2-v2 preparation in Hugging Face's shared cache."""

from __future__ import annotations

import asyncio
import errno
import threading
from dataclasses import replace
from typing import final

import httpx

from app.services.vosk_model_manager import ModelErrorCode, SpeechModelStatus
from app.stt.reazonspeech_model import (
    REAZONSPEECH_DOWNLOAD_BYTES,
    cached_reazonspeech_snapshot,
    download_reazonspeech_snapshot,
)


@final
class ReazonSpeechModelManager:
    """Prepare the fixed Japanese int8 model through one idempotent task."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._status: SpeechModelStatus | None = None
        self._task: asyncio.Task[None] | None = None

    def status(self) -> SpeechModelStatus:
        cached_path = cached_reazonspeech_snapshot()
        with self._lock:
            status = self._status
            if status is None:
                status = self._ready_status(cached_path) if cached_path is not None else self._missing_status()
                self._status = status
            elif (
                cached_path is not None
                and status.state != "downloading"
                and (status.state != "ready" or status.model_path != cached_path)
            ):
                status = self._ready_status(cached_path)
                self._status = status
            elif status.state == "ready" and cached_path is None:
                status = self._missing_status()
                self._status = status
            return status

    async def start(self) -> SpeechModelStatus:
        with self._lock:
            if self._task is not None and not self._task.done():
                return self.status()
            current = self.status()
            if current.state == "ready" and current.model_path is not None:
                return current
            self._status = replace(
                self._missing_status(),
                state="downloading",
                phase="downloading",
                message="ReazonSpeechモデルを取得しています。",
                retryable=False,
            )
            self._task = asyncio.create_task(self._run_download(), name="reazonspeech-model-download-ja")
            return self._status

    def cancel(self) -> SpeechModelStatus:
        """Downloads use Hugging Face's shared cache and are not interruptible."""
        return self.status()

    async def _run_download(self) -> None:
        try:
            model_path = await asyncio.to_thread(download_reazonspeech_snapshot)
            self._set_status(
                state="ready",
                phase="ready",
                downloaded_bytes=REAZONSPEECH_DOWNLOAD_BYTES,
                total_bytes=REAZONSPEECH_DOWNLOAD_BYTES,
                progress_percent=100,
                model_path=model_path,
                error_code=None,
                message="ReazonSpeechモデルを利用できます。",
                retryable=False,
            )
        except Exception as error:
            code, message = self._classify_error(error)
            self._set_status(
                state="failed",
                phase="idle",
                error_code=code,
                message=message,
                retryable=True,
            )
        finally:
            with self._lock:
                self._task = None

    @staticmethod
    def _cache_path() -> str:
        from huggingface_hub.constants import HF_HUB_CACHE

        return HF_HUB_CACHE

    def _missing_status(self) -> SpeechModelStatus:
        return SpeechModelStatus(
            state="missing",
            phase="idle",
            language="ja",
            downloaded_bytes=0,
            total_bytes=REAZONSPEECH_DOWNLOAD_BYTES,
            progress_percent=None,
            model_path=None,
            storage_path=self._cache_path(),
            error_code=None,
            message="ReazonSpeechモデルはまだありません。",
            retryable=True,
            cancelable=False,
        )

    def _ready_status(self, model_path: str) -> SpeechModelStatus:
        return SpeechModelStatus(
            state="ready",
            phase="ready",
            language="ja",
            downloaded_bytes=REAZONSPEECH_DOWNLOAD_BYTES,
            total_bytes=REAZONSPEECH_DOWNLOAD_BYTES,
            progress_percent=100,
            model_path=model_path,
            storage_path=self._cache_path(),
            error_code=None,
            message="ReazonSpeechモデルを利用できます。",
            retryable=False,
            cancelable=False,
        )

    def _set_status(self, **changes: object) -> None:
        with self._lock:
            status = self._status or self._missing_status()
            self._status = replace(status, **changes)

    @staticmethod
    def _classify_error(error: Exception) -> tuple[ModelErrorCode, str]:
        if isinstance(error, OSError):
            if error.errno == errno.ENOSPC:
                return "disk_full", "空き容量が不足しています。"
            if error.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
                return "permission", "Hugging Faceキャッシュに書き込む権限がありません。"
        if isinstance(error, (httpx.HTTPError, ConnectionError, TimeoutError)):
            return "network", "ReazonSpeechモデルを取得できませんでした。ネットワーク接続を確認してください。"
        return "unknown", "ReazonSpeechモデルを取得できませんでした。"


__all__ = ["ReazonSpeechModelManager"]
