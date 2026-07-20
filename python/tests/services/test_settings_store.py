from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.types import TomlTable
from app.services.settings_store import SettingsStore


class TestCfgGet:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Remove any leftover env vars that tests may set.
        for key in ("SEC_KEY", "SEC_FLAG", "SEC_COUNT", "SEC_RATIO"):
            monkeypatch.delenv(key, raising=False)

    def test_bool_true_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg: TomlTable = {}
        for val in ("true", "True", "TRUE", "1", "yes", "YES"):
            monkeypatch.setenv("SEC_FLAG", val)
            assert SettingsStore.cfg_get(cfg, "sec", "flag", False) is True

    def test_bool_false_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg: TomlTable = {}
        for val in ("false", "False", "FALSE", "0", "no", "NO", ""):
            monkeypatch.setenv("SEC_FLAG", val)
            assert SettingsStore.cfg_get(cfg, "sec", "flag", True) is False

    def test_int_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg: TomlTable = {}
        monkeypatch.setenv("SEC_COUNT", "42")
        assert SettingsStore.cfg_get(cfg, "sec", "count", 0) == 42

    def test_float_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg: TomlTable = {}
        monkeypatch.setenv("SEC_RATIO", "3.14")
        assert SettingsStore.cfg_get(cfg, "sec", "ratio", 0.0) == 3.14

    def test_str_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg: TomlTable = {}
        monkeypatch.setenv("SEC_KEY", "hello")
        assert SettingsStore.cfg_get(cfg, "sec", "key", "default") == "hello"

    def test_toml_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg: TomlTable = {"sec": {"flag": True, "count": 7}}
        # Ensure no env var shadows the TOML value.
        monkeypatch.delenv("SEC_FLAG", raising=False)
        monkeypatch.delenv("SEC_COUNT", raising=False)
        assert SettingsStore.cfg_get(cfg, "sec", "flag", False) is True
        assert SettingsStore.cfg_get(cfg, "sec", "count", 0) == 7

    def test_default_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg: TomlTable = {}
        monkeypatch.delenv("SEC_FLAG", raising=False)
        assert SettingsStore.cfg_get(cfg, "sec", "flag", False) is False
        assert SettingsStore.cfg_get(cfg, "sec", "count", 10) == 10
        assert SettingsStore.cfg_get(cfg, "sec", "ratio", 1.5) == 1.5
        assert SettingsStore.cfg_get(cfg, "sec", "key", "default") == "default"


class TestWriteSectionedToml:
    def test_writes_nested_reply_styles_array_of_tables(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"

        SettingsStore.write_sectioned_toml(
            path,
            {
                "reply": {
                    "enabled": True,
                    "auto_generate": False,
                    "default_style": "standard",
                    "styles": [
                        {"id": "standard", "label": "標準", "enabled": True, "priority": 10},
                        {"id": "custom", "label": "Custom", "enabled": False, "priority": 5},
                    ],
                },
            },
        )
        text = path.read_text(encoding="utf-8")
        assert "[reply]" in text
        assert "enabled = true" in text
        assert "auto_generate = false" in text
        assert 'default_style = "standard"' in text
        assert text.count("[[reply.styles]]") == 2
        assert 'id = "standard"' in text
        assert 'id = "custom"' in text
        assert "[[reply_agents]]" not in text

    def test_writes_scalar_arrays_inline(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"

        SettingsStore.write_sectioned_toml(
            path,
            {
                "stt": {
                    "suspicious_phrases": ["ご視聴ありがとうございました", "ありがとうございました"],
                },
            },
        )

        text = path.read_text(encoding="utf-8")
        assert 'suspicious_phrases = ["ご視聴ありがとうございました", "ありがとうございました"]' in text
        assert "[[stt.suspicious_phrases]]" not in text

    def test_no_tmp_file_left_after_write(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"

        SettingsStore.write_sectioned_toml(path, {"sec": {"key": "val"}})

        assert path.exists()
        # Verify no leftover temp files — only the target file remains
        dir_contents = list(tmp_path.iterdir())
        assert dir_contents == [path]

    def test_original_file_preserved_on_write_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        original_content = "# original"
        _ = path.write_text(original_content, encoding="utf-8")

        # Mock NamedTemporaryFile so that write() raises an error
        mock_file = MagicMock()
        mock_file.name = str(tmp_path / "_tmp_for_test")
        mock_file.write.side_effect = OSError("disk full")  # pyright: ignore[reportAny]
        with patch("app.services._file_utils.tempfile.NamedTemporaryFile", return_value=mock_file):
            with pytest.raises(OSError, match="disk full"):
                SettingsStore.write_sectioned_toml(path, {"sec": {"key": "val"}})

        # Original file must be untouched
        assert path.read_text(encoding="utf-8") == original_content
        # Temp file must be cleaned up — no leftover files
        dir_contents = list(tmp_path.iterdir())
        assert dir_contents == [path]
