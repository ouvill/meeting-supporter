"""Tests for FileSecretStore (atomic writes included)."""
# pyright: reportUnusedFunction=false

import os
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import final
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import SECRET_KEYS
from app.services.secret_store import CredentialSecretStore, FileSecretStore, create_secret_store


@final
class _FakeKeyring:
    def __init__(
        self,
        initial: dict[str, str] | None = None,
        fail_writes: bool = False,
        fail_deletes: bool = False,
    ) -> None:
        self.passwords: dict[str, str] = dict(initial or {})
        self.fail_writes: bool = fail_writes
        self.fail_deletes: bool = fail_deletes
        self.get_calls: list[str] = []

    def get_password(self, service_name: str, username: str) -> str | None:
        _ = service_name
        self.get_calls.append(username)
        return self.passwords.get(username)

    def set_password(self, service_name: str, username: str, password: str) -> None:
        _ = service_name
        if self.fail_writes:
            raise RuntimeError("credential store locked")
        self.passwords[username] = password

    def delete_password(self, service_name: str, username: str) -> None:
        _ = service_name
        if self.fail_deletes:
            raise RuntimeError("credential store locked")
        _ = self.passwords.pop(username, None)


@final
class _BlockingReadKeyring:
    def __init__(self, initial: dict[str, str], blocked_key: str) -> None:
        self.passwords = dict(initial)
        self.blocked_key = blocked_key
        self.read_started = Event()
        self.release_read = Event()
        self._block_once = True

    def get_password(self, service_name: str, username: str) -> str | None:
        _ = service_name
        value = self.passwords.get(username)
        if username == self.blocked_key and self._block_once:
            self._block_once = False
            self.read_started.set()
            if not self.release_read.wait(timeout=5):
                raise TimeoutError("blocked keyring read was not released")
        return value

    def set_password(self, service_name: str, username: str, password: str) -> None:
        _ = service_name
        self.passwords[username] = password

    def delete_password(self, service_name: str, username: str) -> None:
        _ = service_name
        _ = self.passwords.pop(username, None)


@pytest.fixture(autouse=True)
def _clean_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in SECRET_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("PROVIDER_LMSTUDIO_API_KEY", raising=False)
    monkeypatch.delenv("SECRET_STORE_BACKEND", raising=False)


class TestFileSecretStore:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Remove all known secret env vars before each test."""
        for key in SECRET_KEYS:
            monkeypatch.delenv(key, raising=False)

    # ── Public API tests (no private-member access) ──────────────────────

    def test_set_secrets_and_get(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)

        store.set_secrets({"GEMINI_API_KEY": "test-key-123"})

        # Should be retrievable via get()
        assert store.get("GEMINI_API_KEY") == "test-key-123"
        # Should also be set in environ
        assert os.environ.get("GEMINI_API_KEY") == "test-key-123"

    def test_set_secrets_writes_file(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)

        store.set_secrets({"GEMINI_API_KEY": "file-key"})
        # Verify by reading the file directly
        content = path.read_text(encoding="utf-8")
        assert 'GEMINI_API_KEY = "file-key"' in content

    def test_no_tmp_file_left_after_set_secrets(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)

        store.set_secrets({"GEMINI_API_KEY": "val"})

        assert path.exists()
        # Verify no leftover temp files — only the target file remains
        dir_contents = list(tmp_path.iterdir())
        assert dir_contents == [path]

    def test_delete_removes_persisted_secret_and_runtime_environment(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)
        store.set_secrets({"GEMINI_API_KEY": "file-key"})

        store.delete("GEMINI_API_KEY")

        assert store.get("GEMINI_API_KEY") is None
        assert store.status("GEMINI_API_KEY") is False
        assert os.getenv("GEMINI_API_KEY") is None
        assert "GEMINI_API_KEY" not in path.read_text(encoding="utf-8")

    def test_load_nonexistent_file(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.toml"
        store = FileSecretStore(path)
        # get() internally calls _load() and returns None for missing keys
        assert store.get("GEMINI_API_KEY") is None

    def test_get_with_env_precedence(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)
        store.set_secrets({"GEMINI_API_KEY": "file-key"})

        monkeypatch.setenv("GEMINI_API_KEY", "env-key")
        # os.environ takes precedence over file
        assert store.get("GEMINI_API_KEY") == "env-key"

    def test_status_all_false_when_none_set(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)

        status = store.status_all()
        assert isinstance(status, dict)
        assert not any(status.values())

    def test_status_true_when_env_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)

        monkeypatch.setenv("GEMINI_API_KEY", "env-key")
        status = store.status_all()
        assert status.get("GEMINI_API_KEY") is True

    def test_provider_key_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)

        monkeypatch.setenv("PROVIDER_LMSTUDIO_API_KEY", "lm-key")
        assert store.status("PROVIDER_LMSTUDIO_API_KEY") is True
        assert store.get("PROVIDER_LMSTUDIO_API_KEY") == "lm-key"

    def test_set_and_get_provider_key(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)

        store.set_secrets({"PROVIDER_LMSTUDIO_API_KEY": "lm-key"})
        assert store.get("PROVIDER_LMSTUDIO_API_KEY") == "lm-key"
        assert store.status_all().get("PROVIDER_LMSTUDIO_API_KEY") is True

    def test_apply_secrets_to_env(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)
        store.set_secrets({"GEMINI_API_KEY": "file-key"})

        store.apply_secrets_to_env()

        assert os.environ.get("GEMINI_API_KEY") == "file-key"

    def test_apply_secrets_to_env_with_keys(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)
        store.set_secrets({"GEMINI_API_KEY": "gemini", "PROVIDER_LMSTUDIO_API_KEY": "lmstudio"})

        for key in ("GEMINI_API_KEY", "PROVIDER_LMSTUDIO_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        store.apply_secrets_to_env(keys=["PROVIDER_LMSTUDIO_API_KEY"])

        assert os.environ.get("PROVIDER_LMSTUDIO_API_KEY") == "lmstudio"
        assert os.environ.get("GEMINI_API_KEY") is None

    def test_apply_secrets_does_not_override_existing_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)
        store.set_secrets({"GEMINI_API_KEY": "file-key"})

        monkeypatch.setenv("GEMINI_API_KEY", "env-key")
        store.apply_secrets_to_env()

        assert os.environ.get("GEMINI_API_KEY") == "env-key"

    def test_restore_reproduces_exact_file_bytes_and_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "secrets.toml"
        original_file = (
            b"# synthetic formatting preserved across rollback\n"
            b'OPENAI_API_KEY    = "synthetic-openai-original"\n'
            b"\n"
            b'DEEPGRAM_API_KEY = "synthetic-deepgram-original"  # harmless comment\n'
        )
        _ = path.write_bytes(original_file)
        store = FileSecretStore(path)
        monkeypatch.setenv("OPENAI_API_KEY", "synthetic-environment-original")
        snapshot = store.snapshot(["OPENAI_API_KEY"])

        store.delete("OPENAI_API_KEY")
        store.restore(snapshot)

        assert path.read_bytes() == original_file
        assert os.environ.get("OPENAI_API_KEY") == "synthetic-environment-original"
        assert os.environ.get("DEEPGRAM_API_KEY") is None
        monkeypatch.delenv("OPENAI_API_KEY")
        assert store.get("OPENAI_API_KEY") == "synthetic-openai-original"
        assert store.get("DEEPGRAM_API_KEY") == "synthetic-deepgram-original"

    def test_restore_file_failure_still_restores_runtime_environment(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)
        store.set_secrets({"GEMINI_API_KEY": "synthetic-original"})
        snapshot = store.snapshot(["GEMINI_API_KEY"])
        store.set_secrets({"GEMINI_API_KEY": "synthetic-replacement"})

        with (
            patch("app.services.secret_store.atomic_write_bytes", side_effect=OSError("injected file failure")),
            pytest.raises(RuntimeError, match="rollback"),
        ):
            store.restore(snapshot)

        assert os.environ.get("GEMINI_API_KEY") == "synthetic-original"

    # ── Internals tests (white-box: must call _write / _load) ───────────

    def test_original_file_preserved_on_write_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)

        original_content = 'KEY_OLD = "old_val"\n'
        _ = path.write_text(original_content, encoding="utf-8")

        # Mock NamedTemporaryFile so that write() raises an error
        mock_file = MagicMock()
        mock_file.name = str(tmp_path / "_tmp_for_test")
        mock_file.write.side_effect = OSError("disk full")  # pyright: ignore[reportAny]
        with patch("app.services._file_utils.tempfile.NamedTemporaryFile", return_value=mock_file):
            with pytest.raises(OSError, match="disk full"):
                store._write({"KEY": "val"})  # pyright: ignore[reportPrivateUsage]

        # Original file must be untouched
        assert path.read_text(encoding="utf-8") == original_content
        # Temp file must be cleaned up — no leftover files
        dir_contents = list(tmp_path.iterdir())
        assert dir_contents == [path]

    def test_write_and_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        store = FileSecretStore(path)

        # Directly exercise the private write/load path
        store._write({"API_KEY": "test-only-api-key", "OTHER_KEY": "val"})  # pyright: ignore[reportPrivateUsage]

        data = store._load()  # pyright: ignore[reportPrivateUsage]
        assert data == {"API_KEY": "test-only-api-key", "OTHER_KEY": "val"}


class TestCredentialSecretStore:
    def test_successful_keyring_write_keeps_legacy_file_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "secrets.toml"
        keyring = _FakeKeyring()
        store = CredentialSecretStore(fallback=FileSecretStore(path), keyring_client=keyring)

        store.set_secrets({"GEMINI_API_KEY": "credential-key"})

        assert keyring.passwords["GEMINI_API_KEY"] == "credential-key"
        assert not path.exists()
        monkeypatch.delenv("GEMINI_API_KEY")
        assert store.get("GEMINI_API_KEY") == "credential-key"

    def test_env_value_wins_over_keyring_and_legacy_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "secrets.toml"
        _ = path.write_text('GEMINI_API_KEY = "file-key"\n', encoding="utf-8")
        store = CredentialSecretStore(
            fallback=FileSecretStore(path),
            keyring_client=_FakeKeyring({"GEMINI_API_KEY": "credential-key"}),
        )

        monkeypatch.setenv("GEMINI_API_KEY", "env-key")

        assert store.get("GEMINI_API_KEY") == "env-key"

    def test_legacy_file_hydrates_env_when_keyring_has_no_value(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        _ = path.write_text('OPENAI_API_KEY = "legacy-openai-key"\n', encoding="utf-8")
        store = CredentialSecretStore(fallback=FileSecretStore(path), keyring_client=_FakeKeyring())

        store.apply_secrets_to_env(keys=["OPENAI_API_KEY"])

        assert os.environ.get("OPENAI_API_KEY") == "legacy-openai-key"

    def test_keyring_write_failure_falls_back_to_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "secrets.toml"
        keyring = _FakeKeyring(fail_writes=True)
        store = CredentialSecretStore(fallback=FileSecretStore(path), keyring_client=keyring)

        store.set_secrets({"ANTHROPIC_API_KEY": "file-fallback-key"})

        assert "ANTHROPIC_API_KEY" not in keyring.passwords
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        assert FileSecretStore(path).get("ANTHROPIC_API_KEY") == "file-fallback-key"

    def test_delete_removes_keyring_file_fallback_and_runtime_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "secrets.toml"
        fallback = FileSecretStore(path)
        fallback.set_secrets({"OPENAI_API_KEY": "legacy-file-key"})
        keyring = _FakeKeyring({"OPENAI_API_KEY": "credential-key"})
        store = CredentialSecretStore(fallback=fallback, keyring_client=keyring)
        monkeypatch.setenv("OPENAI_API_KEY", "runtime-key")

        store.delete("OPENAI_API_KEY")

        assert "OPENAI_API_KEY" not in keyring.passwords
        assert fallback.get("OPENAI_API_KEY") is None
        assert store.status("OPENAI_API_KEY") is False
        assert os.getenv("OPENAI_API_KEY") is None

    def test_delete_falls_back_to_file_and_clears_environment_when_keyring_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        fallback = FileSecretStore(path)
        fallback.set_secrets({"ANTHROPIC_API_KEY": "legacy-file-key"})
        keyring = _FakeKeyring({"ANTHROPIC_API_KEY": "credential-key"}, fail_deletes=True)
        store = CredentialSecretStore(fallback=fallback, keyring_client=keyring)

        store.delete("ANTHROPIC_API_KEY")

        assert fallback.get("ANTHROPIC_API_KEY") is None
        assert os.getenv("ANTHROPIC_API_KEY") is None
        assert store.status("ANTHROPIC_API_KEY") is False

    def test_restore_surfaces_keyring_set_failure_and_retains_failed_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "secrets.toml"
        original_file = (
            b"# synthetic fallback formatting preserved across rollback\n"
            b'OPENAI_API_KEY = "synthetic-unrelated-fallback"\n'
            b"\n"
            b'DEEPGRAM_API_KEY    = "synthetic-fallback-original"  # harmless comment\n'
        )
        _ = path.write_bytes(original_file)
        fallback = FileSecretStore(path)
        keyring = _FakeKeyring({"OPENAI_API_KEY": "synthetic-original"})
        store = CredentialSecretStore(fallback=fallback, keyring_client=keyring)
        monkeypatch.setenv("OPENAI_API_KEY", "synthetic-environment-original")
        snapshot = store.snapshot(["OPENAI_API_KEY"])
        store.set_secrets({"OPENAI_API_KEY": "synthetic-replacement"})
        fallback.delete("OPENAI_API_KEY")
        keyring.fail_writes = True

        with pytest.raises(RuntimeError, match="rollback"):
            store.restore(snapshot)

        assert keyring.passwords["OPENAI_API_KEY"] == "synthetic-replacement"
        assert os.environ.get("OPENAI_API_KEY") == "synthetic-environment-original"
        assert path.read_bytes() == original_file

        keyring.fail_writes = False
        store.set_secrets({"ANTHROPIC_API_KEY": "synthetic-fallback-after-failure"})
        assert "ANTHROPIC_API_KEY" not in keyring.passwords
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        assert FileSecretStore(path).get("ANTHROPIC_API_KEY") == "synthetic-fallback-after-failure"

    def test_restore_surfaces_keyring_delete_failure_and_retains_failed_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "secrets.toml"
        keyring = _FakeKeyring()
        store = CredentialSecretStore(fallback=FileSecretStore(path), keyring_client=keyring)
        snapshot = store.snapshot(["ANTHROPIC_API_KEY"])
        store.set_secrets({"ANTHROPIC_API_KEY": "synthetic-new-secret"})
        keyring.fail_deletes = True

        with pytest.raises(RuntimeError, match="rollback"):
            store.restore(snapshot)

        assert keyring.passwords["ANTHROPIC_API_KEY"] == "synthetic-new-secret"
        assert os.environ.get("ANTHROPIC_API_KEY") is None

        keyring.fail_deletes = False
        store.set_secrets({"OPENAI_API_KEY": "synthetic-fallback-after-failure"})
        assert "OPENAI_API_KEY" not in keyring.passwords
        monkeypatch.delenv("OPENAI_API_KEY")
        assert FileSecretStore(path).get("OPENAI_API_KEY") == "synthetic-fallback-after-failure"

    def test_secret_store_backend_file_returns_file_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "secrets.toml"
        monkeypatch.setenv("SECRET_STORE_BACKEND", "file")
        store = create_secret_store(path)

        assert isinstance(store, FileSecretStore)
        store.set_secrets({"DEEPGRAM_API_KEY": "file-backend-key"})

        monkeypatch.delenv("DEEPGRAM_API_KEY")
        assert FileSecretStore(path).get("DEEPGRAM_API_KEY") == "file-backend-key"

    def test_status_all_reports_keyring_and_file_builtins_without_secret_values(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        _ = path.write_text('OPENAI_API_KEY = "legacy-openai-key"\n', encoding="utf-8")
        store = CredentialSecretStore(
            fallback=FileSecretStore(path),
            keyring_client=_FakeKeyring({"GEMINI_API_KEY": "credential-gemini-key"}),
        )

        status = store.status_all()

        assert status["GEMINI_API_KEY"] is True
        assert status["OPENAI_API_KEY"] is True
        assert status["ANTHROPIC_API_KEY"] is False
        assert all(isinstance(value, bool) for value in status.values())
        assert "credential-gemini-key" not in status
        assert "legacy-openai-key" not in status
        assert "credential-gemini-key" not in {str(value) for value in status.values()}
        assert "legacy-openai-key" not in {str(value) for value in status.values()}

    def test_status_all_reuses_boolean_presence_without_reloading_or_reprobing(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        credential_secret = "synthetic-credential-canary"
        fallback_secret = "synthetic-fallback-canary"
        _ = path.write_text(f'OPENAI_API_KEY = "{fallback_secret}"\n', encoding="utf-8")
        fallback = FileSecretStore(path)
        keyring = _FakeKeyring({"GEMINI_API_KEY": credential_secret})
        store = CredentialSecretStore(fallback=fallback, keyring_client=keyring)

        with patch("app.services.secret_store.tomllib.load", wraps=tomllib.load) as load:
            first = store.status_all()
            first_probe_count = len(keyring.get_calls)
            second = store.status_all()

        assert first == second
        assert load.call_count == 1
        assert len(keyring.get_calls) == first_probe_count
        assert first_probe_count > 0
        assert store._presence_cache is not None  # pyright: ignore[reportPrivateUsage]
        assert all(type(value) is bool for value in store._presence_cache.values())  # pyright: ignore[reportPrivateUsage]
        assert credential_secret not in repr(store._presence_cache)  # pyright: ignore[reportPrivateUsage]
        assert fallback_secret not in repr(store._presence_cache)  # pyright: ignore[reportPrivateUsage]

    def test_file_status_all_reuses_presence_until_environment_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "secrets.toml"
        _ = path.write_text('OPENAI_API_KEY = "synthetic-file-secret"\n', encoding="utf-8")
        store = FileSecretStore(path)

        with patch("app.services.secret_store.tomllib.load", wraps=tomllib.load) as load:
            assert store.status_all()["OPENAI_API_KEY"] is True
            assert store.status_all()["OPENAI_API_KEY"] is True
            assert load.call_count == 1
            monkeypatch.setenv("GEMINI_API_KEY", "synthetic-environment-secret")
            assert store.status_all()["GEMINI_API_KEY"] is True
            assert load.call_count == 2

        assert store._presence_cache is not None  # pyright: ignore[reportPrivateUsage]
        assert all(type(value) is bool for value in store._presence_cache.values())  # pyright: ignore[reportPrivateUsage]

    def test_mutations_hydration_and_restore_keep_cached_presence_coherent(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        fallback = FileSecretStore(path)
        keyring = _FakeKeyring()
        store = CredentialSecretStore(fallback=fallback, keyring_client=keyring)
        secret = "synthetic-cache-coherence-secret"

        assert store.status_all()["GEMINI_API_KEY"] is False
        snapshot = store.snapshot(["GEMINI_API_KEY"])
        store.set_secrets({"GEMINI_API_KEY": secret})
        assert store.status_all()["GEMINI_API_KEY"] is True
        store.delete("GEMINI_API_KEY")
        assert store.status_all()["GEMINI_API_KEY"] is False

        keyring.passwords["GEMINI_API_KEY"] = secret
        store.apply_secrets_to_env(keys=["GEMINI_API_KEY"])
        assert os.environ["GEMINI_API_KEY"] == secret
        assert store.status_all()["GEMINI_API_KEY"] is True

        store.restore(snapshot)
        assert os.getenv("GEMINI_API_KEY") is None
        assert store.status_all()["GEMINI_API_KEY"] is False
        assert store._presence_cache is not None  # pyright: ignore[reportPrivateUsage]
        assert all(type(value) is bool for value in store._presence_cache.values())  # pyright: ignore[reportPrivateUsage]
        assert secret not in repr(store._presence_cache)  # pyright: ignore[reportPrivateUsage]

    def test_direct_access_is_fresh_and_status_all_refreshes_after_ttl(self, tmp_path: Path) -> None:
        now = [100.0]
        keyring = _FakeKeyring()
        store = CredentialSecretStore(
            fallback=FileSecretStore(tmp_path / "secrets.toml"),
            keyring_client=keyring,
            presence_cache_ttl_seconds=5.0,
            presence_clock=lambda: now[0],
        )
        assert store.status_all()["ANTHROPIC_API_KEY"] is False
        initial_probe_count = len(keyring.get_calls)

        keyring.passwords["ANTHROPIC_API_KEY"] = "synthetic-injected-secret"

        assert store.get("ANTHROPIC_API_KEY") == "synthetic-injected-secret"
        assert store.status("ANTHROPIC_API_KEY") is True
        assert store.status_all()["ANTHROPIC_API_KEY"] is False
        assert len(keyring.get_calls) == initial_probe_count + 2

        now[0] += 5.0
        assert store.status_all()["ANTHROPIC_API_KEY"] is True
        assert len(keyring.get_calls) > initial_probe_count + 2

    def test_same_size_fallback_replacement_invalidates_file_and_credential_caches(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.toml"
        openai_content = 'OPENAI_API_KEY = "synthetic-a"\n'
        gemini_content = 'GEMINI_API_KEY = "synthetic-b"\n'
        assert len(openai_content) == len(gemini_content)
        _ = path.write_text(openai_content, encoding="utf-8")

        file_store = FileSecretStore(path)
        credential_store = CredentialSecretStore(
            fallback=FileSecretStore(path),
            keyring_client=_FakeKeyring(),
            presence_cache_ttl_seconds=60.0,
        )
        assert file_store.status_all()["OPENAI_API_KEY"] is True
        assert credential_store.status_all()["OPENAI_API_KEY"] is True

        replacement = tmp_path / "replacement.toml"
        _ = replacement.write_text(gemini_content, encoding="utf-8")
        _ = replacement.replace(path)

        file_status = file_store.status_all()
        credential_status = credential_store.status_all()
        assert file_status["OPENAI_API_KEY"] is False
        assert file_status["GEMINI_API_KEY"] is True
        assert credential_status["OPENAI_API_KEY"] is False
        assert credential_status["GEMINI_API_KEY"] is True

    def test_concurrent_delete_prevents_stale_keyring_presence_publication(self, tmp_path: Path) -> None:
        keyring = _BlockingReadKeyring(
            {"GEMINI_API_KEY": "synthetic-concurrent-secret"},
            blocked_key="GEMINI_API_KEY",
        )
        store = CredentialSecretStore(
            fallback=FileSecretStore(tmp_path / "secrets.toml"),
            keyring_client=keyring,
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            status_future = executor.submit(store.status_all)
            assert keyring.read_started.wait(timeout=5)
            delete_future = executor.submit(store.delete, "GEMINI_API_KEY")
            delete_future.result(timeout=5)
            keyring.release_read.set()
            status = status_future.result(timeout=5)

        assert status["GEMINI_API_KEY"] is False
        assert store.status_all()["GEMINI_API_KEY"] is False
        assert store._presence_cache is not None  # pyright: ignore[reportPrivateUsage]
        assert all(type(value) is bool for value in store._presence_cache.values())  # pyright: ignore[reportPrivateUsage]

    @pytest.mark.parametrize("context_change", ["injected-client", "forced-file-backend"])
    def test_context_change_during_probe_discards_old_keyring_results(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        context_change: str,
    ) -> None:
        old_keyring = _BlockingReadKeyring(
            {"GEMINI_API_KEY": "synthetic-old-context-secret"},
            blocked_key="GEMINI_API_KEY",
        )
        store = CredentialSecretStore(
            fallback=FileSecretStore(tmp_path / "secrets.toml"),
            keyring_client=old_keyring,
        )

        with ThreadPoolExecutor(max_workers=1) as executor:
            status_future = executor.submit(store.status_all)
            assert old_keyring.read_started.wait(timeout=5)
            if context_change == "injected-client":
                store.keyring_client = _FakeKeyring()
            else:
                monkeypatch.setenv("SECRET_STORE_BACKEND", "file")
            old_keyring.release_read.set()
            status = status_future.result(timeout=5)

        assert status["GEMINI_API_KEY"] is False
        assert store.status_all()["GEMINI_API_KEY"] is False
