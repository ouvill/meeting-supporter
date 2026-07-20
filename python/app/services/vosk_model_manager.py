"""Managed download and installation of the bundled small speech models.

The manager deliberately accepts a language key rather than a URL.  Every byte
that reaches disk therefore comes from the immutable catalog below, and archive
contents are extracted manually after structural validation.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import shutil
import stat
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
import zipfile
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, cast, final

from app.core.event_bus import EventBus
from app.core.events import ConfigChanged
from app.services.settings_store import SettingsStore

ModelLanguage = Literal["ja", "en"]
ModelState = Literal["missing", "downloading", "ready", "failed", "cancelled"]
ModelPhase = Literal["idle", "downloading", "verifying", "extracting", "ready"]
ModelErrorCode = Literal["network", "disk_full", "permission", "checksum", "archive", "cancelled", "unknown"]

_MAX_COMPRESSED_BYTES = 128 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 1_000
_MAX_EXPANDED_BYTES = 512 * 1024 * 1024
_MAX_MEMBER_NAME_LENGTH = 240
_READ_CHUNK_SIZE = 256 * 1024


class _ResponseHeaders(Protocol):
    def get(self, name: str, default: str | None = None) -> str | None: ...


class _DownloadResponse(Protocol):
    headers: _ResponseHeaders

    def close(self) -> None: ...

    def read(self, amount: int = -1) -> bytes: ...


@dataclass(frozen=True)
class ModelCatalogEntry:
    """An immutable, application-controlled download definition."""

    language: ModelLanguage
    model_id: str
    url: str
    sha256: str
    advertised_bytes: int


CATALOG: dict[ModelLanguage, ModelCatalogEntry] = {
    "ja": ModelCatalogEntry(
        language="ja",
        model_id="vosk-model-small-ja-0.22",
        url="https://alphacephei.com/vosk/models/vosk-model-small-ja-0.22.zip",
        sha256="efa092d280153a77615e9e0c7d7283e93e600de3d19d3bec686c57ef19d52eac",
        advertised_bytes=48 * 1024 * 1024,
    ),
    "en": ModelCatalogEntry(
        language="en",
        model_id="vosk-model-small-en-us-0.15",
        url="https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        sha256="30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498",
        advertised_bytes=40 * 1024 * 1024,
    ),
}


@dataclass(frozen=True)
class SpeechModelStatus:
    """The public, provider-neutral state of one managed speech model."""

    state: ModelState
    phase: ModelPhase
    language: ModelLanguage
    downloaded_bytes: int
    total_bytes: int | None
    progress_percent: int | None
    model_path: str | None
    storage_path: str
    error_code: ModelErrorCode | None
    message: str
    retryable: bool
    cancelable: bool


@final
class _Cancelled(Exception):
    """Internal cooperative cancellation signal."""


@final
class _ModelDownloadError(Exception):
    code: ModelErrorCode
    message: str

    def __init__(self, code: ModelErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@final
class VoskModelManager:
    """Own a single cancellable managed-model download for the application."""

    def __init__(
        self,
        *,
        user_data_dir: Path,
        settings_store: SettingsStore,
        event_bus: EventBus,
    ) -> None:
        self._user_data_dir = user_data_dir
        self._storage_dir = user_data_dir / "models" / "speech"
        self._settings_store = settings_store
        self._event_bus = event_bus
        self._lock = threading.RLock()
        self._statuses: dict[ModelLanguage, SpeechModelStatus] = {}
        self._active_task: asyncio.Task[None] | None = None
        self._active_language: ModelLanguage | None = None
        self._cancel_requested: threading.Event | None = None

    def _model_path(self, language: ModelLanguage) -> Path:
        return self._storage_dir / CATALOG[language].model_id

    def _ready_status(self, language: ModelLanguage, model_path: Path) -> SpeechModelStatus:
        return SpeechModelStatus(
            state="ready",
            phase="ready",
            language=language,
            downloaded_bytes=0,
            total_bytes=CATALOG[language].advertised_bytes,
            progress_percent=100,
            model_path=str(model_path),
            storage_path=str(self._user_data_dir),
            error_code=None,
            message="音声認識データを利用できます。",
            retryable=False,
            cancelable=False,
        )

    def _configured_model_path(self, language: ModelLanguage) -> Path | None:
        """Return a valid manual path only for its configured language."""
        with self._settings_store.locked():
            config = self._settings_store.load_config()
        stt = config.get("stt")
        if not isinstance(stt, dict) or stt.get("language") != language:
            return None
        configured_path = stt.get("vosk_model_path")
        if not isinstance(configured_path, str) or not configured_path.strip():
            return None
        model_path = Path(configured_path).expanduser()
        return model_path if self._is_valid_model(model_path) else None

    def _default_status(self, language: ModelLanguage) -> SpeechModelStatus:
        model_path = self._configured_model_path(language) or self._model_path(language)
        if self._is_valid_model(model_path):
            return self._ready_status(language, model_path)
        return SpeechModelStatus(
            state="missing",
            phase="idle",
            language=language,
            downloaded_bytes=0,
            total_bytes=None,
            progress_percent=None,
            model_path=None,
            storage_path=str(self._user_data_dir),
            error_code=None,
            message="音声認識データはまだありません。",
            retryable=True,
            cancelable=False,
        )

    def status(self, language: ModelLanguage) -> SpeechModelStatus:
        """Return a snapshot and recognize valid configured manual models."""
        configured_path = self._configured_model_path(language)
        with self._lock:
            status = self._statuses.get(language)
            if configured_path is not None and (status is None or status.state != "downloading"):
                status = self._ready_status(language, configured_path)
                self._statuses[language] = status
            elif status is None:
                status = self._default_status(language)
                self._statuses[language] = status
            return status

    async def start(self, language: ModelLanguage) -> SpeechModelStatus:
        """Start the sole download, or idempotently return its current status."""
        with self._lock:
            active_task = self._active_task
            if active_task is not None and not active_task.done():
                active_language = self._active_language
                if active_language is not None:
                    return self.status(active_language)

            current = self.status(language)
            if (
                current.state == "ready"
                and current.model_path is not None
                and self._is_valid_model(Path(current.model_path))
            ):
                return current

            cancel_requested = threading.Event()
            initial = replace(
                self._default_status(language),
                state="downloading",
                phase="downloading",
                total_bytes=None,
                message="音声認識データを取得しています。",
                retryable=False,
                cancelable=True,
            )
            self._statuses[language] = initial
            self._active_language = language
            self._cancel_requested = cancel_requested
            self._active_task = asyncio.create_task(
                self._run_download(language, cancel_requested), name=f"speech-model-download-{language}"
            )
            return initial

    async def cancel(self) -> SpeechModelStatus:
        """Request cooperative cancellation; repeated requests are harmless."""
        with self._lock:
            active_language = self._active_language
            cancel_requested = self._cancel_requested
            active_task = self._active_task
            if active_language is None or cancel_requested is None or active_task is None or active_task.done():
                return self.status(active_language or "ja")
            current = self.status(active_language)
            if not current.cancelable:
                return current
            cancel_requested.set()
            cancelled = replace(
                self.status(active_language),
                state="cancelled",
                error_code="cancelled",
                message="音声認識データの取得をキャンセルしました。",
                retryable=True,
                cancelable=False,
            )
            self._statuses[active_language] = cancelled
            return cancelled

    async def wait(self) -> None:
        """Await the current download task without changing its cancellation state."""
        with self._lock:
            task = self._active_task
        if task is not None:
            await task

    async def shutdown(self) -> None:
        """Cancel and await the owned worker so no partial archive survives exit."""
        with self._lock:
            cancel_requested = self._cancel_requested
            if cancel_requested is not None:
                cancel_requested.set()
        await self.wait()

    async def _run_download(self, language: ModelLanguage, cancel_requested: threading.Event) -> None:
        installed_path: Path | None = None
        try:
            installed_path = await asyncio.to_thread(self._download_and_install, language, cancel_requested)
            self._check_cancelled(cancel_requested)
            # Settings persistence is the commit boundary.  Once it starts we
            # cannot cancel without risking a configured path to a removed model.
            self._set_status(language, cancelable=False)
            await asyncio.to_thread(self._save_managed_model_settings, language, installed_path)
            await self._event_bus.publish(ConfigChanged())
            self._set_status(
                language,
                state="ready",
                phase="ready",
                downloaded_bytes=CATALOG[language].advertised_bytes,
                total_bytes=CATALOG[language].advertised_bytes,
                progress_percent=100,
                model_path=str(installed_path),
                error_code=None,
                message="音声認識データを利用できます。",
                retryable=False,
                cancelable=False,
            )
        except _Cancelled:
            if installed_path is not None:
                await asyncio.to_thread(self._remove_path, installed_path)
            self._set_cancelled(language)
        except _ModelDownloadError as error:
            if installed_path is not None:
                await asyncio.to_thread(self._remove_path, installed_path)
            self._set_failure(language, error.code, error.message)
        except OSError as error:
            if installed_path is not None:
                await asyncio.to_thread(self._remove_path, installed_path)
            code, message = self._classify_os_error(error)
            self._set_failure(language, code, message)
        except Exception:
            if installed_path is not None:
                await asyncio.to_thread(self._remove_path, installed_path)
            self._set_failure(language, "unknown", "音声認識データを取得できませんでした。")
        finally:
            with self._lock:
                if self._active_language == language:
                    self._active_task = None
                    self._active_language = None
                    self._cancel_requested = None

    def _download_and_install(self, language: ModelLanguage, cancel_requested: threading.Event) -> Path:
        entry = CATALOG[language]
        self._check_cancelled(cancel_requested)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self._storage_dir / f".{entry.model_id}.{uuid.uuid4().hex}.part"
        staging_path: Path | None = None
        installed = False
        try:
            self._download_archive(entry, archive_path, cancel_requested)
            self._set_status(
                language,
                phase="verifying",
                message="取得したデータを確認しています。",
                cancelable=True,
            )
            self._verify_checksum(archive_path, entry, cancel_requested)
            self._check_cancelled(cancel_requested)
            self._set_status(
                language,
                phase="extracting",
                message="音声認識データを準備しています。",
                cancelable=True,
            )
            staging_path = Path(tempfile.mkdtemp(prefix=f".{entry.model_id}.staging-", dir=self._storage_dir))
            extracted_root = self._extract_archive(archive_path, staging_path, cancel_requested)
            self._verify_model_root(extracted_root)
            self._check_cancelled(cancel_requested)
            destination = self._model_path(language)
            self._replace_model(extracted_root, destination)
            installed = True
            return destination
        finally:
            self._remove_path(archive_path)
            if staging_path is not None:
                self._remove_path(staging_path)
            if not installed and cancel_requested.is_set():
                # The task's common handler publishes the public cancelled state.
                pass

    def _download_archive(
        self,
        entry: ModelCatalogEntry,
        archive_path: Path,
        cancel_requested: threading.Event,
    ) -> None:
        request = urllib.request.Request(entry.url, headers={"User-Agent": "meeting-supporter/1"})
        try:
            with (
                closing(cast(_DownloadResponse, urllib.request.urlopen(request, timeout=30))) as response,
                open(archive_path, "xb") as output,
            ):
                length_header = response.headers.get("Content-Length")
                total_bytes: int | None = None
                if length_header is not None:
                    try:
                        total_bytes = int(length_header)
                    except ValueError as error:
                        raise _ModelDownloadError("network", "ダウンロード情報を確認できませんでした。") from error
                    if total_bytes < 0 or total_bytes > _MAX_COMPRESSED_BYTES:
                        raise _ModelDownloadError("archive", "音声認識データのサイズが上限を超えています。")
                downloaded = 0
                self._set_status(entry.language, total_bytes=total_bytes, downloaded_bytes=0)
                while True:
                    self._check_cancelled(cancel_requested)
                    chunk = response.read(_READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > _MAX_COMPRESSED_BYTES:
                        raise _ModelDownloadError("archive", "音声認識データのサイズが上限を超えています。")
                    _ = output.write(chunk)
                    progress = int(downloaded * 100 / total_bytes) if total_bytes else None
                    self._set_status(
                        entry.language,
                        downloaded_bytes=downloaded,
                        total_bytes=total_bytes,
                        progress_percent=min(progress, 100) if progress is not None else None,
                    )
                if total_bytes is not None and downloaded != total_bytes:
                    raise _ModelDownloadError("network", "ダウンロードが途中で終了しました。")
        except _Cancelled:
            raise
        except _ModelDownloadError:
            raise
        except urllib.error.URLError as error:
            raise _ModelDownloadError("network", "音声認識データを取得できませんでした。") from error

    def _verify_checksum(
        self,
        archive_path: Path,
        entry: ModelCatalogEntry,
        cancel_requested: threading.Event,
    ) -> None:
        digest = hashlib.sha256()
        with open(archive_path, "rb") as archive:
            while chunk := archive.read(_READ_CHUNK_SIZE):
                self._check_cancelled(cancel_requested)
                digest.update(chunk)
        if digest.hexdigest() != entry.sha256:
            raise _ModelDownloadError("checksum", "音声認識データの確認に失敗しました。")

    def _extract_archive(
        self,
        archive_path: Path,
        staging_path: Path,
        cancel_requested: threading.Event,
    ) -> Path:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                root_name = self._validate_archive_members(members)
                staging_root = staging_path.resolve()
                for member in members:
                    self._check_cancelled(cancel_requested)
                    member_path = self._safe_member_path(staging_root, member.filename)
                    if member.is_dir():
                        member_path.mkdir(parents=True, exist_ok=True)
                        continue
                    member_path.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member, "r") as source, open(member_path, "xb") as destination:
                        remaining = member.file_size
                        while remaining:
                            self._check_cancelled(cancel_requested)
                            chunk = source.read(min(_READ_CHUNK_SIZE, remaining))
                            if not chunk:
                                raise _ModelDownloadError("archive", "音声認識データの展開に失敗しました。")
                            _ = destination.write(chunk)
                            remaining -= len(chunk)
                        if source.read(1):
                            raise _ModelDownloadError("archive", "音声認識データの展開に失敗しました。")
                return staging_root / root_name
        except _Cancelled:
            raise
        except _ModelDownloadError:
            raise
        except OSError:
            raise
        except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, EOFError) as error:
            raise _ModelDownloadError("archive", "音声認識データを安全に展開できませんでした。") from error

    @staticmethod
    def _validate_archive_members(members: list[zipfile.ZipInfo]) -> str:
        if not members or len(members) > _MAX_ARCHIVE_MEMBERS:
            raise _ModelDownloadError("archive", "音声認識データの形式が正しくありません。")
        expanded_bytes = 0
        roots: set[str] = set()
        for member in members:
            name = member.filename
            if len(name) > _MAX_MEMBER_NAME_LENGTH or "\x00" in name or "\\" in name:
                raise _ModelDownloadError("archive", "音声認識データの形式が正しくありません。")
            path = PurePosixPath(name)
            if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
                raise _ModelDownloadError("archive", "音声認識データの形式が正しくありません。")
            if any(":" in part for part in path.parts):
                raise _ModelDownloadError("archive", "音声認識データの形式が正しくありません。")
            mode = member.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if member.is_dir():
                if file_type not in (0, stat.S_IFDIR):
                    raise _ModelDownloadError("archive", "音声認識データの形式が正しくありません。")
            elif file_type not in (0, stat.S_IFREG):
                # This rejects symlinks, device nodes, FIFOs, and sockets.
                raise _ModelDownloadError("archive", "音声認識データの形式が正しくありません。")
            expanded_bytes += member.file_size
            if expanded_bytes > _MAX_EXPANDED_BYTES:
                raise _ModelDownloadError("archive", "音声認識データのサイズが上限を超えています。")
            roots.add(path.parts[0])
        if len(roots) != 1:
            raise _ModelDownloadError("archive", "音声認識データの形式が正しくありません。")
        return next(iter(roots))

    @staticmethod
    def _safe_member_path(staging_root: Path, name: str) -> Path:
        destination = staging_root.joinpath(*PurePosixPath(name).parts)
        try:
            _ = destination.resolve().relative_to(staging_root)
        except ValueError as error:
            raise _ModelDownloadError("archive", "音声認識データを安全に展開できませんでした。") from error
        return destination

    @staticmethod
    def _verify_model_root(model_root: Path) -> None:
        required_directories = (model_root, model_root / "am", model_root / "conf")
        required_markers = (
            model_root / "am" / "final.mdl",
            model_root / "conf" / "mfcc.conf",
            model_root / "conf" / "model.conf",
        )
        if any(directory.is_symlink() or not directory.is_dir() for directory in required_directories) or any(
            marker.is_symlink() or not marker.is_file() for marker in required_markers
        ):
            raise _ModelDownloadError("archive", "音声認識データの内容を確認できませんでした。")

    def _replace_model(self, extracted_root: Path, destination: Path) -> None:
        if destination.exists() or destination.is_symlink():
            if self._is_valid_model(destination):
                return
            self._remove_path(destination)
        os.replace(extracted_root, destination)

    def _save_managed_model_settings(self, language: ModelLanguage, model_path: Path) -> None:
        with self._settings_store.locked():
            config = self._settings_store.load_config()
            existing_stt = config.get("stt")
            stt = dict(existing_stt) if isinstance(existing_stt, dict) else {}
            # Do not set backend here. Downloading must not switch an active STT runtime.
            stt["language"] = language
            stt["vosk_model_path"] = str(model_path)
            # The sectioned TOML writer needs the parsed AI hierarchy normalized
            # back to its dotted tables; reuse the settings API's canonical path so
            # a managed model update cannot discard existing route configuration.
            from app.api.settings import flatten_ai_tables

            flatten_ai_tables(config)
            config["stt"] = stt
            self._settings_store.write_sectioned_toml(self._settings_store.config_path, config)

    def _set_status(self, language: ModelLanguage, **changes: object) -> None:
        with self._lock:
            status = self._statuses.get(language) or self._default_status(language)
            self._statuses[language] = replace(status, **changes)

    def _set_cancelled(self, language: ModelLanguage) -> None:
        with self._lock:
            current = self._statuses.get(language) or self._default_status(language)
            if current.state == "cancelled":
                return
            self._statuses[language] = replace(
                current,
                state="cancelled",
                error_code="cancelled",
                message="音声認識データの取得をキャンセルしました。",
                retryable=True,
                cancelable=False,
            )

    def _set_failure(self, language: ModelLanguage, code: ModelErrorCode, message: str) -> None:
        self._set_status(
            language,
            state="failed",
            error_code=code,
            message=message,
            retryable=True,
            cancelable=False,
        )

    @staticmethod
    def _check_cancelled(cancel_requested: threading.Event) -> None:
        if cancel_requested.is_set():
            raise _Cancelled()

    @staticmethod
    def _is_valid_model(model_path: Path) -> bool:
        try:
            VoskModelManager._verify_model_root(model_path)
        except _ModelDownloadError:
            return False
        return True

    @staticmethod
    def _remove_path(path: Path) -> None:
        try:
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
            elif path.exists():
                shutil.rmtree(path)
        except OSError:
            # Cleanup must never mask the original operation failure.
            pass

    @staticmethod
    def _classify_os_error(error: OSError) -> tuple[ModelErrorCode, str]:
        if error.errno == errno.ENOSPC:
            return "disk_full", "空き容量が不足しています。"
        if error.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            return "permission", "保存先に書き込む権限がありません。"
        return "unknown", "音声認識データを保存できませんでした。"


__all__ = [
    "CATALOG",
    "ModelErrorCode",
    "ModelLanguage",
    "ModelPhase",
    "ModelState",
    "SpeechModelStatus",
    "VoskModelManager",
]
