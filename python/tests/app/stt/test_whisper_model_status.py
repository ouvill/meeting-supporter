"""Tests for Whisper model loading stage notifications via WebSocket StatusMsg."""

import unittest
from collections.abc import Coroutine
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest.mock import call, patch

from huggingface_hub.errors import LocalEntryNotFoundError

from app.core.config import SttConfig
from app.core.messages import OutgoingMessage, StatusMsg
from app.stt.stages.stt_whisper import WhisperEngine


class _FakePublisher:
    """Captures published StatusMsg texts for assertion."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def publish(self, msg: OutgoingMessage) -> None:
        if isinstance(msg, StatusMsg):
            self.messages.append(msg.text)

    def schedule(self, coro: Coroutine[object, object, object]) -> None:
        coro.close()


class WhisperEngineStatusNotificationTest(unittest.TestCase):
    """Verify stage notifications (確認中 / ダウンロード中 / ロード中 / 準備完了)."""

    @override
    def setUp(self) -> None:
        self._reset_engine()

    @override
    def tearDown(self) -> None:
        self._reset_engine()

    # ── helpers ------------------------------------------------------------------

    @staticmethod
    def _reset_engine() -> None:
        # Step 1: signal shutdown — let thread exit before clearing _queue.
        with WhisperEngine._lock:  # pyright: ignore[reportPrivateUsage]
            WhisperEngine._running = False  # pyright: ignore[reportPrivateUsage]
            t = WhisperEngine._thread  # pyright: ignore[reportPrivateUsage]
        if t is not None and t.is_alive():
            t.join(timeout=1.0)
        # Step 2: now it's safe to clear fields.
        with WhisperEngine._lock:  # pyright: ignore[reportPrivateUsage]
            WhisperEngine._thread = None  # pyright: ignore[reportPrivateUsage]
            WhisperEngine._model = None  # pyright: ignore[reportPrivateUsage]
            WhisperEngine._queue = None  # pyright: ignore[reportPrivateUsage]
            WhisperEngine._ref_count = 0  # pyright: ignore[reportPrivateUsage]
            WhisperEngine._active_device = None  # pyright: ignore[reportPrivateUsage]
            WhisperEngine._model_path = None  # pyright: ignore[reportPrivateUsage]

    @staticmethod
    def _make_config(whisper_model: str = "tiny") -> SttConfig:
        return SttConfig(
            backend="whisper",
            whisper_model=whisper_model,
            deepgram_model="nova-3",
            language="ja",
            vad_sensitivity=0.4,
            silence_duration=0.4,
            vad_aggressiveness=2,
            device="cpu",
            remote_url="",
            remote_token="",
            sample_rate=16000,
            chunk_size=1600,
        )

    # ── tests --------------------------------------------------------------------

    def test_cached_model_publishes_no_download_message(self) -> None:
        """Cached complete model loads without publishing a download message."""
        cfg = self._make_config()
        publisher = _FakePublisher()

        with TemporaryDirectory() as tmp_dir:
            fake_model_path = Path(tmp_dir) / "huggingface" / "hub" / "models--tiny" / "snapshots" / "complete"
            fake_model_path.mkdir(parents=True)
            _ = (fake_model_path / "model.bin").write_bytes(b"model")

            with (
                patch("os.path.isdir", return_value=False),
                patch("faster_whisper.utils.download_model", return_value=str(fake_model_path)) as mock_download,
                patch("faster_whisper.WhisperModel") as mock_wm,
            ):
                WhisperEngine.acquire(cfg, publisher=publisher)

        # download_model called once with local_files_only=True
        mock_download.assert_called_once_with("tiny", local_files_only=True)

        # WhisperModel loaded from the returned cache path
        mock_wm.assert_called_once_with(str(fake_model_path), device="cpu", compute_type="int8")

        # Stage notifications: checking → loading → ready (no download)
        self.assertIn("Whisperモデルを確認中...", publisher.messages)
        self.assertIn("Whisperモデルをロード中...", publisher.messages)
        self.assertIn("Whisperモデルの準備が完了しました", publisher.messages)
        self.assertNotIn(
            "Whisperモデルをダウンロード中です。初回のみ時間がかかる場合があります...",
            publisher.messages,
        )

    def test_uncached_model_publishes_download_message(self) -> None:
        """Uncached: first local_files_only raises, then explicit download."""
        cfg = self._make_config()
        publisher = _FakePublisher()

        with TemporaryDirectory() as tmp_dir:
            fake_model_path = Path(tmp_dir) / "huggingface" / "hub" / "models--tiny" / "snapshots" / "downloaded"
            fake_model_path.mkdir(parents=True)
            _ = (fake_model_path / "model.bin").write_bytes(b"model")

            def download_side_effect(_size_or_id: str, **kwargs: object) -> str:
                if kwargs.get("local_files_only"):
                    msg = "Not found locally"
                    raise LocalEntryNotFoundError(msg)
                return str(fake_model_path)

            with (
                patch("os.path.isdir", return_value=False),
                patch("faster_whisper.utils.download_model", side_effect=download_side_effect) as mock_download,
                patch("faster_whisper.WhisperModel") as mock_wm,
            ):
                WhisperEngine.acquire(cfg, publisher=publisher)

        self.assertEqual(
            mock_download.call_args_list,
            [call("tiny", local_files_only=True), call("tiny")],
        )
        mock_wm.assert_called_once_with(str(fake_model_path), device="cpu", compute_type="int8")

        # All four stage notifications in order
        self.assertEqual(
            publisher.messages,
            [
                "Whisperモデルを確認中...",
                "Whisperモデルをダウンロード中です。初回のみ時間がかかる場合があります...",
                "Whisperモデルをロード中...",
                "Whisperモデルの準備が完了しました",
            ],
        )

    def test_incomplete_huggingface_cache_repairs_then_loads_repaired_path(self) -> None:
        """A cache snapshot without model.bin is repaired before WhisperModel loads."""
        cfg = self._make_config()
        publisher = _FakePublisher()

        with TemporaryDirectory() as tmp_dir:
            cache_root = Path(tmp_dir) / "huggingface" / "hub" / "models--tiny" / "snapshots"
            incomplete_path = cache_root / "incomplete"
            repaired_path = cache_root / "repaired"
            incomplete_path.mkdir(parents=True)
            repaired_path.mkdir(parents=True)
            _ = (repaired_path / "model.bin").write_bytes(b"model")

            def download_side_effect(_size_or_id: str, **kwargs: object) -> str:
                if kwargs.get("local_files_only"):
                    return str(incomplete_path)
                return str(repaired_path)

            with (
                patch("os.path.isdir", return_value=False),
                patch("faster_whisper.utils.download_model", side_effect=download_side_effect) as mock_download,
                patch("faster_whisper.WhisperModel") as mock_wm,
            ):
                WhisperEngine.acquire(cfg, publisher=publisher)

        self.assertEqual(
            mock_download.call_args_list,
            [call("tiny", local_files_only=True), call("tiny")],
        )
        mock_wm.assert_called_once_with(str(repaired_path), device="cpu", compute_type="int8")
        self.assertEqual(
            publisher.messages,
            [
                "Whisperモデルを確認中...",
                "Whisperモデルのキャッシュが不完全なため、再ダウンロードします...",
                "Whisperモデルをロード中...",
                "Whisperモデルの準備が完了しました",
            ],
        )

    def test_model_bin_open_error_repairs_cache_once_before_success(self) -> None:
        """A model.bin open failure during load repairs cache once and loads repaired path."""
        cfg = self._make_config()
        publisher = _FakePublisher()
        model_bin_error = RuntimeError("Unable to open file 'model.bin'")
        repaired_model = object()

        with TemporaryDirectory() as tmp_dir:
            cache_root = Path(tmp_dir) / "huggingface" / "hub" / "models--tiny" / "snapshots"
            stale_path = cache_root / "stale"
            repaired_path = cache_root / "repaired"
            stale_path.mkdir(parents=True)
            repaired_path.mkdir(parents=True)
            _ = (stale_path / "model.bin").write_bytes(b"stale")
            _ = (repaired_path / "model.bin").write_bytes(b"repaired")

            def download_side_effect(_size_or_id: str, **kwargs: object) -> str:
                if kwargs.get("local_files_only"):
                    return str(stale_path)
                return str(repaired_path)

            with (
                patch("os.path.isdir", return_value=False),
                patch("faster_whisper.utils.download_model", side_effect=download_side_effect) as mock_download,
                patch("faster_whisper.WhisperModel", side_effect=[model_bin_error, repaired_model]) as mock_wm,
            ):
                WhisperEngine.acquire(cfg, publisher=publisher)

        self.assertEqual(
            mock_download.call_args_list,
            [call("tiny", local_files_only=True), call("tiny")],
        )
        self.assertEqual(
            mock_wm.mock_calls,
            [
                call(str(stale_path), device="cpu", compute_type="int8"),
                call(str(repaired_path), device="cpu", compute_type="int8"),
            ],
        )
        self.assertEqual(
            publisher.messages,
            [
                "Whisperモデルを確認中...",
                "Whisperモデルをロード中...",
                "Whisperモデルのキャッシュが不完全なため、再ダウンロードします...",
                "Whisperモデルの準備が完了しました",
            ],
        )

    def test_value_error_propagates(self) -> None:
        """ValueError from download_model (invalid model name) propagates,
        and no further notifications are sent."""
        cfg = self._make_config(whisper_model="invalid-model")
        publisher = _FakePublisher()

        with (
            patch("os.path.isdir", return_value=False),
            patch(
                "faster_whisper.utils.download_model",
                side_effect=ValueError("Invalid model size"),
            ),
            patch("faster_whisper.WhisperModel"),
        ):
            with self.assertRaises(ValueError):
                WhisperEngine.acquire(cfg, publisher=publisher)

        # Only the "確認中" message was published before the error
        self.assertEqual(publisher.messages, ["Whisperモデルを確認中..."])

    def test_local_directory_skips_download_model(self) -> None:
        """Local directory bypasses download_model even when model.bin is absent."""
        publisher = _FakePublisher()

        with TemporaryDirectory() as tmp_dir:
            local_model_path = Path(tmp_dir) / "local-model"
            local_model_path.mkdir()
            cfg = self._make_config(whisper_model=str(local_model_path))

            with (
                patch("faster_whisper.utils.download_model") as mock_download,
                patch("faster_whisper.WhisperModel") as mock_wm,
            ):
                WhisperEngine.acquire(cfg, publisher=publisher)

        mock_download.assert_not_called()
        mock_wm.assert_called_once_with(
            str(local_model_path),
            device="cpu",
            compute_type="int8",
        )

        # Stage notifications: checking → loading → ready (no download)
        self.assertIn("Whisperモデルを確認中...", publisher.messages)
        self.assertIn("Whisperモデルをロード中...", publisher.messages)
        self.assertIn("Whisperモデルの準備が完了しました", publisher.messages)
        self.assertNotIn(
            "Whisperモデルをダウンロード中です。初回のみ時間がかかる場合があります...",
            publisher.messages,
        )

    def test_already_loaded_skips_notifications(self) -> None:
        """When model is already loaded, a second acquire does not re-notify."""
        cfg = self._make_config()
        publisher1 = _FakePublisher()
        publisher2 = _FakePublisher()

        with TemporaryDirectory() as tmp_dir:
            fake_model_path = Path(tmp_dir) / "huggingface" / "hub" / "models--tiny" / "snapshots" / "complete"
            fake_model_path.mkdir(parents=True)
            _ = (fake_model_path / "model.bin").write_bytes(b"model")

            with (
                patch("os.path.isdir", return_value=False),
                patch("faster_whisper.utils.download_model", return_value=str(fake_model_path)),
                patch("faster_whisper.WhisperModel"),
            ):
                WhisperEngine.acquire(cfg, publisher=publisher1)
                WhisperEngine.acquire(cfg, publisher=publisher2)

        # First acquire: stage notifications were sent
        self.assertGreater(len(publisher1.messages), 0)
        # Second acquire: model already loaded → no notifications
        self.assertEqual(publisher2.messages, [])


if __name__ == "__main__":
    _ = unittest.main()
