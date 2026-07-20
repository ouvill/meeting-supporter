"""Behavioral contracts for Whisper preparation in Hugging Face's shared cache."""

from __future__ import annotations

import asyncio
import errno
import tempfile
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol, cast, final
from unittest.mock import patch

import httpx

from app.services.whisper_model_manager import WHISPER_MODEL_REPOSITORIES, WhisperModelManager


class _ProgressReporter(Protocol):
    """The Hugging Face progress protocol needed for aggregate byte reporting."""

    total: float | None

    def update(self, n: int | float | None = 1) -> object: ...

    def refresh(self, *args: object, **kwargs: object) -> object: ...

    def close(self) -> object: ...


@final
class _SnapshotDownloadFake:
    """A controllable Hugging Face boundary that never reaches the network."""

    def __init__(self, model_path: str) -> None:
        self.model_path: str = model_path
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.progress_published: threading.Event = threading.Event()
        self.release: threading.Event = threading.Event()

    def __call__(self, repository: str, **kwargs: object) -> str:
        self.calls.append((repository, kwargs))
        progress_class = cast(Callable[..., _ProgressReporter], kwargs["tqdm_class"])
        progress = progress_class(unit="B")
        progress.total = 100
        _ = progress.refresh()
        _ = progress.update(37)
        self.progress_published.set()
        _ = self.release.wait()
        _ = progress.close()
        return self.model_path


class WhisperModelManagerContractTest(unittest.IsolatedAsyncioTestCase):
    """Exercise the public preparation state machine with fake Hugging Face boundaries."""

    async def _await_single_download(self, manager: WhisperModelManager) -> None:
        """Synchronize on the manager task because its public API deliberately has no wait endpoint."""
        tasks = tuple(manager._tasks.values())  # pyright: ignore[reportPrivateUsage]
        assert len(tasks) == 1
        await tasks[0]

    def test_status_recognizes_complete_model_in_faster_whisper_shared_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cached_snapshot = Path(temporary) / "models--systran--faster-whisper-base" / "snapshots" / "revision"
            cached_snapshot.mkdir(parents=True)
            _ = (cached_snapshot / "model.bin").write_bytes(b"model")

            with (
                patch("faster_whisper.utils.download_model", return_value=str(cached_snapshot)),
                patch.object(WhisperModelManager, "_cache_path", return_value="/shared/huggingface-cache"),
            ):
                status = WhisperModelManager().status("ja", "base")

        assert status.state == "ready"
        assert status.phase == "ready"
        assert status.model_path == str(cached_snapshot)
        assert status.progress_percent == 100
        assert status.cancelable is False

    async def test_start_is_idempotent_publishes_aggregate_progress_and_uses_shared_cache_snapshot(self) -> None:
        manager = WhisperModelManager()
        transport = _SnapshotDownloadFake("/shared/huggingface-cache/snapshots/small")

        with (
            patch.object(WhisperModelManager, "_cached_model_path", return_value=None) as cached_model_path,
            patch("huggingface_hub.snapshot_download", side_effect=transport),
        ):
            first = await manager.start("ja", "small")
            repeated = await manager.start("ja", "small")
            _ = await asyncio.to_thread(transport.progress_published.wait)
            progress = manager.status("ja", "small")
            unchanged_by_cancel = manager.cancel("ja", "small")
            transport.release.set()
            await self._await_single_download(manager)
            cached_model_path.return_value = transport.model_path
            ready = manager.status("ja", "small")

        assert first.state == "downloading"
        assert first.cancelable is False
        assert repeated == first
        assert progress.state == "downloading"
        assert progress.downloaded_bytes == 37
        assert progress.total_bytes == 100
        assert progress.progress_percent == 37
        assert unchanged_by_cancel == progress
        assert transport.calls[0][0] == WHISPER_MODEL_REPOSITORIES["small"]
        snapshot_kwargs = transport.calls[0][1]
        assert "local_dir" not in snapshot_kwargs
        assert "output_dir" not in snapshot_kwargs
        assert "cache_dir" not in snapshot_kwargs
        assert ready.state == "ready"
        assert ready.phase == "ready"
        assert ready.model_path == "/shared/huggingface-cache/snapshots/small"
        assert ready.downloaded_bytes == 37
        assert ready.total_bytes == 100
        assert ready.progress_percent == 100
        assert ready.retryable is False
        assert ready.cancelable is False

    async def test_download_failures_surface_retryable_provider_error_codes(self) -> None:
        cases: tuple[tuple[str, Callable[[], Exception], Literal["network", "disk_full", "permission"]], ...] = (
            ("network", lambda: httpx.ConnectError("offline"), "network"),
            ("disk full", lambda: OSError(errno.ENOSPC, "full"), "disk_full"),
            ("permission denied", lambda: PermissionError(errno.EACCES, "denied"), "permission"),
        )

        for name, make_error, expected_code in cases:
            with self.subTest(name=name):
                manager = WhisperModelManager()
                with (
                    patch.object(WhisperModelManager, "_cached_model_path", return_value=None),
                    patch("huggingface_hub.snapshot_download", side_effect=make_error()),
                ):
                    started = await manager.start("en", "tiny")
                    await self._await_single_download(manager)
                    failed = manager.status("en", "tiny")

                assert started.state == "downloading"
                assert failed.state == "failed"
                assert failed.error_code == expected_code
                assert failed.retryable is True
                assert failed.cancelable is False
