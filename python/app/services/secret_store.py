"""SecretStore protocol implementations."""

from __future__ import annotations

import importlib
import logging
import os
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from app.core.config import SECRET_KEYS
from app.core.protocols import SecretRollbackError, SecretSnapshot, SecretSnapshotError
from app.services._file_utils import atomic_write_bytes, atomic_write_text

logger = logging.getLogger(__name__)

_DEFAULT_SERVICE_NAME = "net.ouvill.meeting-supporter"
_BACKEND_ENV = "SECRET_STORE_BACKEND"


class _KeyringClient(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...
    def set_password(self, service_name: str, username: str, password: str) -> None: ...
    def delete_password(self, service_name: str, username: str) -> None: ...


@dataclass(frozen=True)
class _ImportedKeyringClient:
    module: object

    def get_password(self, service_name: str, username: str) -> str | None:
        get_password = cast("Callable[[str, str], str | None]", getattr(self.module, "get_password"))
        return get_password(service_name, username)

    def set_password(self, service_name: str, username: str, password: str) -> None:
        set_password = cast("Callable[[str, str, str], None]", getattr(self.module, "set_password"))
        set_password(service_name, username, password)

    def delete_password(self, service_name: str, username: str) -> None:
        delete_password = cast("Callable[[str, str], None]", getattr(self.module, "delete_password"))
        delete_password(service_name, username)


def _load_keyring_client() -> _KeyringClient | None:
    try:
        module = importlib.import_module("keyring")
    except Exception as exc:
        logger.info("OS credential store backend is unavailable: %s", exc)
        return None
    return _ImportedKeyringClient(module)


@dataclass(frozen=True)
class _FileSecretSnapshotValue:
    """Exact fallback-file bytes and environment before a mutation."""

    file_content: bytes | None
    environment: dict[str, str | None]


@dataclass(frozen=True)
class _CredentialSecretSnapshotValue:
    """Credential, fallback-file, and environment state before a mutation."""

    credential_values: dict[str, str | None] | None
    credential_failed: bool
    fallback: SecretSnapshot
    environment: dict[str, str | None]


def _restore_environment(environment: dict[str, str | None]) -> list[Exception]:
    """Restore every captured environment entry, collecting independent failures."""
    failures: list[Exception] = []
    for key, secret in environment.items():
        try:
            if secret is None:
                _ = os.environ.pop(key, None)
            else:
                os.environ[key] = secret
        except Exception as exc:
            failures.append(RuntimeError(f"environment rollback failed for {key}: {exc}"))
    return failures


@dataclass
class FileSecretStore:
    path: Path

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        with open(self.path, "rb") as f:
            raw_data = cast(object, tomllib.load(f))
        if not isinstance(raw_data, dict):
            return {}
        data = cast("dict[object, object]", raw_data)
        return {key: value for key, value in data.items() if isinstance(key, str) and isinstance(value, str)}

    def _write(self, data: dict[str, str]) -> None:
        lines: list[str] = []
        for key, value in data.items():
            escaped = (
                value.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
                .replace("\t", "\\t")
                .replace("\r", "\\r")
            )
            lines.append(f'{key} = "{escaped}"')
        content = "\n".join(lines) + "\n"
        atomic_write_text(self.path, content)

    def apply_secrets_to_env(self, keys: Iterable[str] | None = None) -> None:
        """Startup helper: populate os.environ from file for SDK compatibility.

        ``keys=None`` expands all known built-in secret keys for backward
        compatibility. Callers can pass a subset of keys (e.g. provider key
        refs) to scope the expansion.
        """
        file_secrets = self._load()
        target_keys = list(SECRET_KEYS) if keys is None else list(keys)
        for key in target_keys:
            value = file_secrets.get(key)
            if value and not os.getenv(key):
                os.environ[key] = value

    def get(self, key: str) -> str | None:
        return os.getenv(key) or self._load().get(key) or None

    def set_secrets(self, updates: dict[str, str]) -> None:
        existing = self._load()
        for key, value in updates.items():
            existing[key] = value
            os.environ[key] = value
        self._write(existing)

    def delete(self, key: str) -> None:
        existing = self._load()
        _ = existing.pop(key, None)
        _ = os.environ.pop(key, None)
        self._write(existing)

    def status(self, key: str) -> bool:
        file_secrets = self._load()
        return bool(os.getenv(key) or file_secrets.get(key))

    def status_all(self) -> dict[str, bool]:
        file_secrets = self._load()
        keys = set(SECRET_KEYS) | set(file_secrets)
        return {key: bool(os.getenv(key) or file_secrets.get(key)) for key in keys}

    def keys(self) -> set[str]:
        return set(self._load())

    def snapshot(self, keys: Iterable[str]) -> SecretSnapshot:
        """Capture the exact file bytes and affected process environment."""
        affected_keys = set(keys)
        environment = {key: os.environ.get(key) for key in affected_keys}
        try:
            file_content = self.path.read_bytes()
        except FileNotFoundError:
            file_content = None
        return SecretSnapshot(
            _FileSecretSnapshotValue(file_content=file_content, environment=environment),
        )

    def restore(self, snapshot: SecretSnapshot) -> None:
        """Restore the exact fallback file and affected process environment."""
        value = snapshot.value
        if not isinstance(value, _FileSecretSnapshotValue):
            raise TypeError("snapshot was not created by a file secret store")

        failures: list[Exception] = []
        try:
            if value.file_content is None:
                self.path.unlink(missing_ok=True)
            else:
                try:
                    current_content = self.path.read_bytes()
                except FileNotFoundError:
                    current_content = None
                if current_content != value.file_content:
                    atomic_write_bytes(self.path, value.file_content)
        except Exception as exc:
            failures.append(RuntimeError(f"fallback file rollback failed: {exc}"))
        finally:
            failures.extend(_restore_environment(value.environment))

        if failures:
            raise SecretRollbackError(failures) from failures[0]


@dataclass
class CredentialSecretStore:
    """Secret store backed by the OS credential store with file fallback.

    `keyring` maps to macOS Keychain, Windows Credential Manager, and Linux
    Secret Service when those backends are available. Existing `secrets.toml`
    remains a read-only migration source: reads/status/env hydration consult it
    after the OS store, while new writes go to the OS store unless that backend
    is unavailable.
    """

    fallback: FileSecretStore
    service_name: str = _DEFAULT_SERVICE_NAME
    keyring_client: _KeyringClient | None = None
    _credential_failed: bool = False

    def _forced_file_backend(self) -> bool:
        return os.getenv(_BACKEND_ENV, "auto").lower() == "file"

    def _client(self) -> _KeyringClient | None:
        if self._forced_file_backend() or self._credential_failed:
            return None
        if self.keyring_client is not None:
            return self.keyring_client
        return _load_keyring_client()

    def _get_credential(self, key: str) -> str | None:
        client = self._client()
        if client is None:
            return None
        try:
            return client.get_password(self.service_name, key)
        except Exception as exc:
            self._credential_failed = True
            logger.warning("OS credential store read failed; falling back to secrets.toml: %s", exc)
            return None

    def _snapshot_credentials(self, keys: Iterable[str]) -> dict[str, str | None] | None:
        """Read prior credentials without conflating backend failure with absence."""
        client = self._client()
        if client is None:
            return None

        values: dict[str, str | None] = {}
        for key in keys:
            try:
                values[key] = client.get_password(self.service_name, key)
            except Exception as exc:
                self._credential_failed = True
                raise SecretSnapshotError(
                    f"credential snapshot failed while reading {key}; settings were not changed"
                ) from exc
        return values

    def _set_credentials(self, updates: dict[str, str]) -> bool:
        client = self._client()
        if client is None:
            return False
        try:
            for key, value in updates.items():
                client.set_password(self.service_name, key, value)
        except Exception as exc:
            self._credential_failed = True
            logger.warning("OS credential store write failed; falling back to secrets.toml: %s", exc)
            return False
        return True

    def _delete_credential(self, key: str) -> bool:
        client = self._client()
        if client is None:
            return False
        try:
            client.delete_password(self.service_name, key)
        except Exception:
            self._credential_failed = True
            logger.warning("OS credential store deletion failed; falling back to secrets.toml")
            return False
        return True

    def apply_secrets_to_env(self, keys: Iterable[str] | None = None) -> None:
        target_keys = list(SECRET_KEYS) if keys is None else list(keys)
        for key in target_keys:
            if os.getenv(key):
                continue
            value = self.get(key)
            if value:
                os.environ[key] = value

    def get(self, key: str) -> str | None:
        return os.getenv(key) or self._get_credential(key) or self.fallback.get(key)

    def set_secrets(self, updates: dict[str, str]) -> None:
        if self._set_credentials(updates):
            for key, value in updates.items():
                os.environ[key] = value
            return
        self.fallback.set_secrets(updates)

    def delete(self, key: str) -> None:
        """Remove a secret from every active store and this process environment."""
        _ = self._delete_credential(key)
        self.fallback.delete(key)
        _ = os.environ.pop(key, None)

    def status(self, key: str) -> bool:
        return bool(os.getenv(key) or self._get_credential(key) or self.fallback.status(key))

    def status_all(self) -> dict[str, bool]:
        keys = set(SECRET_KEYS) | self.fallback.keys()
        return {key: self.status(key) for key in keys}

    def snapshot(self, keys: Iterable[str]) -> SecretSnapshot:
        """Capture credential, fallback-file, and process-environment state."""
        affected_keys = set(keys)
        environment = {key: os.environ.get(key) for key in affected_keys}
        credential_failed = self._credential_failed
        credential_values = self._snapshot_credentials(affected_keys)
        return SecretSnapshot(
            _CredentialSecretSnapshotValue(
                credential_values=credential_values,
                credential_failed=credential_failed,
                fallback=self.fallback.snapshot(affected_keys),
                environment=environment,
            ),
        )

    def restore(self, snapshot: SecretSnapshot) -> None:
        """Restore the captured credential, fallback-file, and environment state."""
        value = snapshot.value
        if not isinstance(value, _CredentialSecretSnapshotValue):
            raise TypeError("snapshot was not created by a credential secret store")

        failures: list[Exception] = []
        credential_restore_failed = False
        try:
            if value.credential_values is not None:
                self._credential_failed = value.credential_failed
                client = self._client()
                if client is None:
                    credential_restore_failed = True
                    failures.append(RuntimeError("credential rollback failed: backend is unavailable"))
                else:
                    for key, secret in value.credential_values.items():
                        try:
                            current = client.get_password(self.service_name, key)
                        except Exception as exc:
                            credential_restore_failed = True
                            failures.append(RuntimeError(f"credential rollback read failed for {key}: {exc}"))
                            continue
                        if current == secret:
                            continue

                        self._credential_failed = value.credential_failed
                        try:
                            restored = (
                                self._delete_credential(key) if secret is None else self._set_credentials({key: secret})
                            )
                        except Exception as exc:
                            credential_restore_failed = True
                            failures.append(RuntimeError(f"credential rollback write failed for {key}: {exc}"))
                        else:
                            if not restored:
                                credential_restore_failed = True
                                failures.append(RuntimeError(f"credential rollback write failed for {key}"))

            try:
                self.fallback.restore(value.fallback)
            except Exception as exc:
                failures.append(exc)
        finally:
            failures.extend(_restore_environment(value.environment))
            self._credential_failed = True if credential_restore_failed else value.credential_failed

        if failures:
            raise SecretRollbackError(failures) from failures[0]


def create_secret_store(path: Path) -> CredentialSecretStore | FileSecretStore:
    fallback = FileSecretStore(path)
    if os.getenv(_BACKEND_ENV, "auto").lower() == "file":
        return fallback
    return CredentialSecretStore(fallback=fallback)


__all__ = [
    "CredentialSecretStore",
    "FileSecretStore",
    "create_secret_store",
]
