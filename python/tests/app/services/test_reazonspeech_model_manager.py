"""Contracts for ReazonSpeech model preparation in the shared cache."""

from __future__ import annotations

import errno
import unittest
from unittest.mock import patch

from app.services.reazonspeech_model_manager import ReazonSpeechModelManager
from app.stt.reazonspeech_model import REAZONSPEECH_DOWNLOAD_BYTES


class ReazonSpeechModelManagerTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_is_idempotent_and_recognizes_completed_shared_cache_download(self) -> None:
        manager = ReazonSpeechModelManager()
        downloaded = False
        calls = 0

        def cached() -> str | None:
            return "/shared/huggingface-cache/snapshots/reazonspeech" if downloaded else None

        def download() -> str:
            nonlocal calls, downloaded
            calls += 1
            downloaded = True
            return "/shared/huggingface-cache/snapshots/reazonspeech"

        with (
            patch(
                "app.services.reazonspeech_model_manager.cached_reazonspeech_snapshot",
                side_effect=cached,
            ),
            patch(
                "app.services.reazonspeech_model_manager.download_reazonspeech_snapshot",
                side_effect=download,
            ),
            patch.object(
                ReazonSpeechModelManager,
                "_cache_path",
                return_value="/shared/huggingface-cache",
            ),
        ):
            first = await manager.start()
            second = await manager.start()
            task = manager._task  # pyright: ignore[reportPrivateUsage]
            assert task is not None
            await task
            ready = manager.status()

        assert first.state == "downloading"
        assert second.state == "downloading"
        assert calls == 1
        assert ready.state == "ready"
        assert ready.language == "ja"
        assert ready.model_path == "/shared/huggingface-cache/snapshots/reazonspeech"
        assert ready.storage_path == "/shared/huggingface-cache"
        assert ready.total_bytes == REAZONSPEECH_DOWNLOAD_BYTES
        assert ready.progress_percent == 100
        assert ready.cancelable is False

    def test_status_recognizes_complete_cached_model(self) -> None:
        with (
            patch(
                "app.services.reazonspeech_model_manager.cached_reazonspeech_snapshot",
                return_value="/shared/huggingface-cache/snapshots/reazonspeech",
            ),
            patch.object(
                ReazonSpeechModelManager,
                "_cache_path",
                return_value="/shared/huggingface-cache",
            ),
        ):
            status = ReazonSpeechModelManager().status()

        assert status.state == "ready"
        assert status.language == "ja"
        assert status.total_bytes == REAZONSPEECH_DOWNLOAD_BYTES
        assert status.cancelable is False

    def test_missing_status_refreshes_when_runtime_populates_shared_cache(self) -> None:
        cached_path: str | None = None

        def cached() -> str | None:
            return cached_path

        with (
            patch(
                "app.services.reazonspeech_model_manager.cached_reazonspeech_snapshot",
                side_effect=cached,
            ),
            patch.object(
                ReazonSpeechModelManager,
                "_cache_path",
                return_value="/shared/huggingface-cache",
            ),
        ):
            manager = ReazonSpeechModelManager()
            missing = manager.status()
            cached_path = "/shared/huggingface-cache/snapshots/reazonspeech"
            ready = manager.status()

        assert missing.state == "missing"
        assert ready.state == "ready"
        assert ready.model_path == cached_path

    def test_download_error_is_retryable_without_exposing_external_details(self) -> None:
        code, message = ReazonSpeechModelManager._classify_error(  # pyright: ignore[reportPrivateUsage]
            OSError(errno.ENOSPC, "synthetic path detail")
        )

        assert code == "disk_full"
        assert message == "空き容量が不足しています。"
        assert "synthetic" not in message


if __name__ == "__main__":
    _ = unittest.main()
