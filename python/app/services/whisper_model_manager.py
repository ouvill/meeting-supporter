"""Managed Whisper preparation using Hugging Face's shared cache.

Whisper models deliberately remain in Hugging Face's standard cache.  This
service records preparation state only; it never creates, owns, or persists a
model path in application settings.
"""

from __future__ import annotations

import asyncio
import errno
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Literal, cast, final

import httpx

from app.services.vosk_model_manager import ModelErrorCode, ModelLanguage, SpeechModelStatus

WhisperModelAlias = Literal["tiny", "base", "small", "medium", "large-v2", "large-v3-turbo"]

DEFAULT_WHISPER_MODEL: Final[WhisperModelAlias] = "large-v3-turbo"
WHISPER_MODEL_REPOSITORIES: Final[dict[WhisperModelAlias, str]] = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
WHISPER_ALLOW_PATTERNS: Final[list[str]] = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]


@dataclass(frozen=True)
class _ModelKey:
    language: ModelLanguage
    model: WhisperModelAlias


@final
class WhisperModelManager:
    """Prepare selected Whisper aliases in the shared Hugging Face cache.

    Each ``(language, model)`` preparation has one idempotent background task.
    Hugging Face owns the cache and its file locks, so concurrent preparation of
    different aliases is safe.  Downloads are intentionally not cancellable.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._statuses: dict[_ModelKey, SpeechModelStatus] = {}
        self._tasks: dict[_ModelKey, asyncio.Task[None]] = {}

    def status(self, language: ModelLanguage, model: WhisperModelAlias) -> SpeechModelStatus:
        """Return a fresh shared-cache status, recognizing externally cached models."""
        key = _ModelKey(language=language, model=model)
        cached_path = self._cached_model_path(model)
        with self._lock:
            status = self._statuses.get(key)
            if status is None:
                status = (
                    self._ready_status(language, cached_path)
                    if cached_path is not None
                    else self._missing_status(language)
                )
                self._statuses[key] = status
            elif status.state == "ready" and cached_path is None:
                status = self._missing_status(language)
                self._statuses[key] = status
            return status

    async def start(self, language: ModelLanguage, model: WhisperModelAlias) -> SpeechModelStatus:
        """Start preparation once, returning the current state for repeated requests."""
        key = _ModelKey(language=language, model=model)
        with self._lock:
            active_task = self._tasks.get(key)
            if active_task is not None and not active_task.done():
                return self.status(language, model)

            current = self.status(language, model)
            if current.state == "ready" and current.model_path is not None:
                return current

            initial = replace(
                self._missing_status(language),
                state="downloading",
                phase="downloading",
                message="Whisperモデルを取得しています。",
                retryable=False,
                cancelable=False,
            )
            self._statuses[key] = initial
            self._tasks[key] = asyncio.create_task(
                self._run_download(key), name=f"whisper-model-download-{model}-{language}"
            )
            return initial

    def cancel(self, language: ModelLanguage, model: WhisperModelAlias) -> SpeechModelStatus:
        """Return the current state without interrupting a non-cancellable download."""
        return self.status(language, model)

    async def _run_download(self, key: _ModelKey) -> None:
        try:
            model_path = await asyncio.to_thread(self._download_model, key)
            self._set_status(
                key,
                state="ready",
                phase="ready",
                downloaded_bytes=self._status_downloaded_bytes(key),
                total_bytes=self._status_total_bytes(key),
                progress_percent=100,
                model_path=model_path,
                error_code=None,
                message="Whisperモデルを利用できます。",
                retryable=False,
                cancelable=False,
            )
        except Exception as error:
            code, message = self._classify_error(error)
            self._set_status(
                key,
                state="failed",
                phase="idle",
                error_code=code,
                message=message,
                retryable=True,
                cancelable=False,
            )
        finally:
            with self._lock:
                _ = self._tasks.pop(key, None)

    def _download_model(self, key: _ModelKey) -> str:
        """Download through snapshot_download without leaving the shared HF cache."""
        from huggingface_hub import snapshot_download  # pyright: ignore[reportUnknownVariableType]

        manager = self

        @final
        class _AggregateByteProgress:
            """Minimal tqdm-compatible aggregate byte progress adapter."""

            def __init__(self, *_args: object, **kwargs: object) -> None:
                raw_total = kwargs.get("total")
                raw_initial = kwargs.get("initial", 0)
                self.total = float(raw_total) if isinstance(raw_total, (int, float)) else None
                self.n = float(raw_initial) if isinstance(raw_initial, (int, float)) else 0.0
                self._lock = threading.Lock()
                self._publish()

            def update(self, n: int | float | None = 1) -> None:
                with self._lock:
                    self.n += 1 if n is None else n
                    self._publish()

            def refresh(self, *_args: object, **_kwargs: object) -> None:
                with self._lock:
                    self._publish()

            def close(self) -> None:
                with self._lock:
                    self._publish()

            def _publish(self) -> None:
                downloaded = int(self.n)
                total = int(self.total) if self.total is not None and self.total > 0 else None
                progress = min(int(downloaded * 100 / total), 100) if total else None
                manager._set_status(
                    key,
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    progress_percent=progress,
                )

        model_path = snapshot_download(  # pyright: ignore[reportCallIssue, reportUnknownVariableType]
            WHISPER_MODEL_REPOSITORIES[key.model],
            allow_patterns=WHISPER_ALLOW_PATTERNS,
            tqdm_class=_AggregateByteProgress,  # pyright: ignore[reportArgumentType]
        )
        return cast(str, model_path)

    @staticmethod
    def _cached_model_path(model: WhisperModelAlias) -> str | None:
        """Ask faster-whisper itself whether its exact cache snapshot is ready."""
        from faster_whisper.utils import download_model  # pyright: ignore[reportMissingTypeStubs]
        from huggingface_hub.errors import LocalEntryNotFoundError

        try:
            model_path = Path(cast(str, download_model(model, local_files_only=True)))
            return str(model_path) if (model_path / "model.bin").is_file() else None
        except LocalEntryNotFoundError:
            return None

    @staticmethod
    def _cache_path() -> str:
        from huggingface_hub.constants import HF_HUB_CACHE

        return HF_HUB_CACHE

    def _missing_status(self, language: ModelLanguage) -> SpeechModelStatus:
        return SpeechModelStatus(
            state="missing",
            phase="idle",
            language=language,
            downloaded_bytes=0,
            total_bytes=None,
            progress_percent=None,
            model_path=None,
            storage_path=self._cache_path(),
            error_code=None,
            message="Whisperモデルはまだありません。",
            retryable=True,
            cancelable=False,
        )

    def _ready_status(self, language: ModelLanguage, model_path: str) -> SpeechModelStatus:
        return SpeechModelStatus(
            state="ready",
            phase="ready",
            language=language,
            downloaded_bytes=0,
            total_bytes=None,
            progress_percent=100,
            model_path=model_path,
            storage_path=self._cache_path(),
            error_code=None,
            message="Whisperモデルを利用できます。",
            retryable=False,
            cancelable=False,
        )

    def _set_status(self, key: _ModelKey, **changes: object) -> None:
        with self._lock:
            status = self._statuses.get(key) or self._missing_status(key.language)
            self._statuses[key] = replace(status, **changes)

    def _status_downloaded_bytes(self, key: _ModelKey) -> int:
        with self._lock:
            status = self._statuses.get(key)
            return status.downloaded_bytes if status is not None else 0

    def _status_total_bytes(self, key: _ModelKey) -> int | None:
        with self._lock:
            status = self._statuses.get(key)
            return status.total_bytes if status is not None else None

    @staticmethod
    def _classify_error(error: Exception) -> tuple[ModelErrorCode, str]:
        if isinstance(error, OSError):
            if error.errno == errno.ENOSPC:
                return "disk_full", "空き容量が不足しています。"
            if error.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
                return "permission", "Hugging Faceキャッシュに書き込む権限がありません。"
        if isinstance(error, (httpx.HTTPError, ConnectionError, TimeoutError)):
            return "network", "Whisperモデルを取得できませんでした。ネットワーク接続を確認してください。"
        return "unknown", "Whisperモデルを取得できませんでした。"


__all__ = [
    "DEFAULT_WHISPER_MODEL",
    "WHISPER_ALLOW_PATTERNS",
    "WHISPER_MODEL_REPOSITORIES",
    "WhisperModelAlias",
    "WhisperModelManager",
]
