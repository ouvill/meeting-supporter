from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.reazonspeech_model_manager import ReazonSpeechModelManager
from app.stt.reazonspeech_model import REAZONSPEECH_DOWNLOAD_BYTES


class ReazonSpeechModelManagerContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_prepares_fixed_shared_cache_snapshot_once(self) -> None:
        manager = ReazonSpeechModelManager()
        calls = 0

        def download(**kwargs: object) -> str:
            nonlocal calls
            calls += 1
            assert kwargs == {}
            return "/shared/huggingface-cache/snapshots/reazonspeech"

        def cached() -> str | None:
            return "/shared/huggingface-cache/snapshots/reazonspeech" if calls else None

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
        assert ready.progress_percent == 100
        assert ready.cancelable is False

    def test_recognizes_complete_cached_model(self) -> None:
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


if __name__ == "__main__":
    _ = unittest.main()
