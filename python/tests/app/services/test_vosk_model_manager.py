"""Security and lifecycle contracts for managed local speech-model downloads."""

from __future__ import annotations

import asyncio
import hashlib
import io
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import override
from unittest.mock import patch

from app.core.event_bus import EventBus
from app.core.events import ConfigChanged
from app.services.settings_store import SettingsStore
from app.services.vosk_model_manager import CATALOG, ModelCatalogEntry, VoskModelManager


def _archive_bytes(
    *,
    extra_members: list[tuple[str, bytes, int | None]] | None = None,
    include_required_markers: bool = True,
) -> bytes:
    """Create a small ZIP with optional model markers and security test entries."""
    output = io.BytesIO()
    entries = [
        *(
            [
                ("model/am/final.mdl", b"model", None),
                ("model/conf/mfcc.conf", b"mfcc", None),
                ("model/conf/model.conf", b"config", None),
            ]
            if include_required_markers
            else []
        ),
        *(extra_members or []),
    ]
    with zipfile.ZipFile(output, "w") as archive:
        for name, contents, mode in entries:
            info = zipfile.ZipInfo(name)
            if mode is not None:
                info.external_attr = mode << 16
            archive.writestr(info, contents)
    return output.getvalue()


def _write_valid_model(path: Path) -> None:
    (path / "am").mkdir(parents=True)
    (path / "conf").mkdir()
    _ = (path / "am" / "final.mdl").write_bytes(b"model")
    _ = (path / "conf" / "mfcc.conf").write_text("mfcc", encoding="utf-8")
    _ = (path / "conf" / "model.conf").write_text("config", encoding="utf-8")


class _Response:
    _payload: bytes
    _sent: bool
    headers: dict[str, str]

    def __init__(self, payload: bytes, *, content_length: str | None = None) -> None:
        self._payload = payload
        self._sent = False
        self.headers = {"Content-Length": content_length or str(len(payload))}

    def close(self) -> None:
        return None

    def read(self, _size: int) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._payload


class _BlockedResponse(_Response):
    read_started: threading.Event
    release_read: threading.Event

    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.read_started = threading.Event()
        self.release_read = threading.Event()

    @override
    def read(self, size: int) -> bytes:
        self.read_started.set()
        _ = self.release_read.wait()
        return super().read(size)


class VoskModelManagerContractTest(unittest.IsolatedAsyncioTestCase):
    """Test public manager behavior using a real temporary filesystem and fake transport only."""

    @override
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self._temporary: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.tmp_path: Path = Path(self._temporary.name)
        self.config_path: Path = self.tmp_path / "config.toml"
        self.default_path: Path = self.tmp_path / "default.toml"
        _ = self.default_path.write_text('[stt]\nbackend = "whisper"\n', encoding="utf-8")
        _ = self.config_path.write_text(
            '[stt]\nbackend = "deepgram"\nlanguage = "en"\nremote_url = "https://speech.example"\n\n'
            + '[audio]\ninput_device = "Mic A"\n\n[other]\nkeep = "this section"\n',
            encoding="utf-8",
        )
        self.store: SettingsStore = SettingsStore(config_path=self.config_path, default_config_path=self.default_path)
        self.event_bus: EventBus = EventBus()
        self.events: list[ConfigChanged] = []

        async def capture(event: ConfigChanged) -> None:
            self.events.append(event)

        self.event_bus.subscribe(ConfigChanged, capture)
        self._manager: VoskModelManager | None = None

    @property
    def manager(self) -> VoskModelManager:
        assert self._manager is not None
        return self._manager

    @override
    async def asyncSetUp(self) -> None:
        self._manager = VoskModelManager(
            user_data_dir=self.tmp_path / "data", settings_store=self.store, event_bus=self.event_bus
        )

    @override
    async def asyncTearDown(self) -> None:
        if self._manager is not None:
            await self._manager.shutdown()
        self._temporary.cleanup()

    @staticmethod
    def _entry(payload: bytes) -> ModelCatalogEntry:
        original = CATALOG["ja"]
        return ModelCatalogEntry(
            language="ja",
            model_id=original.model_id,
            url="https://catalog.invalid/managed-ja.zip",
            sha256=hashlib.sha256(payload).hexdigest(),
            advertised_bytes=len(payload),
        )

    def _catalog_patch(self, payload: bytes):
        return patch.dict("app.services.vosk_model_manager.CATALOG", {"ja": self._entry(payload)})

    def _assert_no_partial_installation(self) -> None:
        storage = self.tmp_path / "data" / "models" / "speech"
        destination = storage / CATALOG["ja"].model_id
        assert not destination.exists()
        if storage.exists():
            assert list(storage.glob(".*.part")) == []
            assert list(storage.glob(".*.staging-*")) == []

    async def test_download_installs_verified_marked_model_atomically_and_preserves_settings(self) -> None:
        payload = _archive_bytes()
        requested_urls: list[str] = []

        def open_catalog_request(request: urllib.request.Request, *, timeout: int) -> _Response:
            requested_urls.append(request.full_url)
            assert timeout == 30
            return _Response(payload)

        with (
            self._catalog_patch(payload),
            patch("app.services.vosk_model_manager.urllib.request.urlopen", side_effect=open_catalog_request),
        ):
            starting = await self.manager.start("ja")
            assert starting.state == "downloading"
            assert starting.cancelable is True
            await self.manager.wait()

            ready = self.manager.status("ja")
            repeated = await self.manager.start("ja")

        destination = self.tmp_path / "data" / "models" / "speech" / CATALOG["ja"].model_id
        assert requested_urls == ["https://catalog.invalid/managed-ja.zip"]
        assert ready.state == "ready"
        assert ready.phase == "ready"
        assert ready.model_path == str(destination)
        assert ready.progress_percent == 100
        assert repeated == ready
        assert (destination / "am" / "final.mdl").is_file()
        assert (destination / "conf" / "mfcc.conf").is_file()
        assert (destination / "conf" / "model.conf").is_file()
        assert list(destination.parent.glob(".*.part")) == []
        assert list(destination.parent.glob(".*.staging-*")) == []

        saved = self.store.load_config()
        stt = saved["stt"]
        assert isinstance(stt, dict)
        assert stt["backend"] == "deepgram"
        assert stt["language"] == "ja"
        assert stt["vosk_model_path"] == str(destination)
        assert stt["remote_url"] == "https://speech.example"
        assert saved["audio"] == {"input_device": "Mic A"}
        assert saved["other"] == {"keep": "this section"}
        assert len(self.events) == 1

    async def test_valid_manual_model_path_is_ready_but_incomplete_or_missing_manual_path_is_missing(self) -> None:
        manual = self.tmp_path / "manual-vosk"
        _write_valid_model(manual)
        self.store.write_sectioned_toml(self.config_path, {"stt": {"language": "ja", "vosk_model_path": str(manual)}})
        manager = VoskModelManager(
            user_data_dir=self.tmp_path / "other-data", settings_store=self.store, event_bus=self.event_bus
        )
        assert manager.status("ja").state == "ready"
        assert manager.status("ja").model_path == str(manual)

        incomplete = self.tmp_path / "incomplete-vosk"
        (incomplete / "am").mkdir(parents=True)
        _ = (incomplete / "am" / "final.mdl").write_bytes(b"model")
        self.store.write_sectioned_toml(
            self.config_path, {"stt": {"language": "ja", "vosk_model_path": str(incomplete)}}
        )
        invalid_manager = VoskModelManager(
            user_data_dir=self.tmp_path / "third-data", settings_store=self.store, event_bus=self.event_bus
        )
        assert invalid_manager.status("ja").state == "missing"
        self.store.write_sectioned_toml(
            self.config_path,
            {"stt": {"language": "ja", "vosk_model_path": str(self.tmp_path / "does-not-exist")}},
        )
        missing_manager = VoskModelManager(
            user_data_dir=self.tmp_path / "fourth-data", settings_store=self.store, event_bus=self.event_bus
        )
        assert missing_manager.status("ja").state == "missing"

    async def test_cancelled_download_never_publishes_final_model_or_partial_files(self) -> None:
        payload = _archive_bytes()
        response = _BlockedResponse(payload)
        with (
            self._catalog_patch(payload),
            patch("app.services.vosk_model_manager.urllib.request.urlopen", return_value=response),
        ):
            started = await self.manager.start("ja")
            assert started.state == "downloading"
            _ = await asyncio.to_thread(response.read_started.wait)
            cancelled = await self.manager.cancel()
            assert cancelled.state == "cancelled"
            assert cancelled.error_code == "cancelled"
            assert cancelled.retryable is True
            assert cancelled.cancelable is False
            _ = response.release_read.set()
            await self.manager.wait()

        assert self.manager.status("ja").state == "cancelled"
        self._assert_no_partial_installation()
        assert self.events == []

    async def test_active_download_is_singleton_and_repeated_cancel_is_idempotent(self) -> None:
        payload = _archive_bytes()
        response = _BlockedResponse(payload)
        with (
            self._catalog_patch(payload),
            patch("app.services.vosk_model_manager.urllib.request.urlopen", return_value=response),
        ):
            first = await self.manager.start("ja")
            _ = await asyncio.to_thread(response.read_started.wait)
            second = await self.manager.start("en")
            assert first.state == "downloading"
            assert second.language == "ja"
            assert second.state == "downloading"
            first_cancel = await self.manager.cancel()
            second_cancel = await self.manager.cancel()
            assert second_cancel == first_cancel
            _ = response.release_read.set()
            await self.manager.wait()

        self._assert_no_partial_installation()

    async def test_network_and_checksum_failures_are_retryable_and_leave_no_installation(self) -> None:
        payload = _archive_bytes()
        with (
            self._catalog_patch(payload),
            patch(
                "app.services.vosk_model_manager.urllib.request.urlopen", side_effect=urllib.error.URLError("offline")
            ),
        ):
            _ = await self.manager.start("ja")
            await self.manager.wait()

        failed = self.manager.status("ja")
        assert failed.state == "failed"
        assert failed.error_code == "network"
        assert failed.retryable is True
        self._assert_no_partial_installation()

        mismatched = ModelCatalogEntry(
            language="ja",
            model_id=CATALOG["ja"].model_id,
            url="https://catalog.invalid/managed-ja.zip",
            sha256="0" * 64,
            advertised_bytes=len(payload),
        )
        with (
            patch.dict("app.services.vosk_model_manager.CATALOG", {"ja": mismatched}),
            patch("app.services.vosk_model_manager.urllib.request.urlopen", return_value=_Response(payload)),
        ):
            retry = await self.manager.start("ja")
            assert retry.state == "downloading"
            await self.manager.wait()

        checksum_failure = self.manager.status("ja")
        assert checksum_failure.state == "failed"
        assert checksum_failure.error_code == "checksum"
        assert checksum_failure.retryable is True
        self._assert_no_partial_installation()

    async def test_rejects_malformed_and_unsafe_archives_without_writing_outside_storage(self) -> None:
        attack_cases = {
            "broken archive": b"not a zip archive",
            "missing required markers": _archive_bytes(
                include_required_markers=False, extra_members=[("model/readme", b"not a model", None)]
            ),
            "parent traversal": _archive_bytes(extra_members=[("model/../../escaped", b"x", None)]),
            "absolute member": _archive_bytes(extra_members=[("/escaped", b"x", None)]),
            "symlink member": _archive_bytes(extra_members=[("model/link", b"target", stat.S_IFLNK | 0o777)]),
            "special member": _archive_bytes(extra_members=[("model/fifo", b"", stat.S_IFIFO | 0o644)]),
        }
        outside = self.tmp_path / "escaped"
        for name, payload in attack_cases.items():
            with (
                self.subTest(name=name),
                self._catalog_patch(payload),
                patch("app.services.vosk_model_manager.urllib.request.urlopen", return_value=_Response(payload)),
            ):
                _ = await self.manager.start("ja")
                await self.manager.wait()
                failed = self.manager.status("ja")
                assert failed.state == "failed"
                assert failed.error_code == "archive"
                assert failed.retryable is True
                self._assert_no_partial_installation()
                assert not outside.exists()

    async def test_rejects_member_count_and_expanded_size_limit_without_final_model(self) -> None:
        too_many_members = _archive_bytes(extra_members=[(f"model/files/{index}", b"x", None) for index in range(998)])
        with (
            self.subTest(name="member limit"),
            self._catalog_patch(too_many_members),
            patch("app.services.vosk_model_manager.urllib.request.urlopen", return_value=_Response(too_many_members)),
        ):
            _ = await self.manager.start("ja")
            await self.manager.wait()
            assert self.manager.status("ja").error_code == "archive"
            self._assert_no_partial_installation()

        over_limit = _archive_bytes(extra_members=[("model/files/large", b"01234567890", None)])
        with (
            self.subTest(name="expanded size limit"),
            patch("app.services.vosk_model_manager._MAX_EXPANDED_BYTES", 10),
            self._catalog_patch(over_limit),
            patch("app.services.vosk_model_manager.urllib.request.urlopen", return_value=_Response(over_limit)),
        ):
            _ = await self.manager.start("ja")
            await self.manager.wait()
            assert self.manager.status("ja").error_code == "archive"
            self._assert_no_partial_installation()

    async def test_rejects_oversized_advertised_download_before_writing_part_file(self) -> None:
        payload = _archive_bytes()
        too_large_header = str(128 * 1024 * 1024 + 1)
        with (
            self._catalog_patch(payload),
            patch(
                "app.services.vosk_model_manager.urllib.request.urlopen",
                return_value=_Response(payload, content_length=too_large_header),
            ),
        ):
            _ = await self.manager.start("ja")
            await self.manager.wait()

        failed = self.manager.status("ja")
        assert failed.state == "failed"
        assert failed.error_code == "archive"
        self._assert_no_partial_installation()
