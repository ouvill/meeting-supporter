"""Tests for _file_utils helper functions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services._file_utils import atomic_write_text


class TestAtomicWriteText:
    def test_writes_content(self, tmp_path: Path) -> None:
        path = tmp_path / "test.txt"
        atomic_write_text(path, "hello world")
        assert path.read_text(encoding="utf-8") == "hello world"

    def test_no_tmp_file_after_success(self, tmp_path: Path) -> None:
        path = tmp_path / "test.txt"
        atomic_write_text(path, "content")
        assert path.read_text(encoding="utf-8") == "content"
        # Verify no leftover temp files — only the target file remains
        dir_contents = list(tmp_path.iterdir())
        assert dir_contents == [path]

    def test_original_file_preserved_on_write_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "test.txt"
        original = "original content"
        _ = path.write_text(original, encoding="utf-8")

        # Mock NamedTemporaryFile so that write() raises an error
        mock_file = MagicMock()
        mock_file.name = str(tmp_path / "_tmp_for_test")
        mock_file.write.side_effect = OSError("write error")  # pyright: ignore[reportAny]
        with patch("app.services._file_utils.tempfile.NamedTemporaryFile", return_value=mock_file):
            with pytest.raises(OSError, match="write error"):
                atomic_write_text(path, "new content")

        # Original file must be untouched
        assert path.read_text(encoding="utf-8") == original
        # No leftover temp files
        dir_contents = list(tmp_path.iterdir())
        assert dir_contents == [path]

    def test_original_file_preserved_on_replace_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "test.txt"
        original = "original content"
        _ = path.write_text(original, encoding="utf-8")

        # Write succeeds but os.replace fails
        with patch("app.services._file_utils.os.replace", side_effect=OSError("rename error")):
            with pytest.raises(OSError, match="rename error"):
                atomic_write_text(path, "new content")

        # Original file must be untouched
        assert path.read_text(encoding="utf-8") == original
        # Temp file must be cleaned up (os.replace failure triggers cleanup)
        dir_contents = list(tmp_path.iterdir())
        assert dir_contents == [path]

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "nested" / "test.txt"
        atomic_write_text(path, "content")
        assert path.read_text(encoding="utf-8") == "content"

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "test.txt"
        _ = path.write_text("old", encoding="utf-8")
        atomic_write_text(path, "new")
        assert path.read_text(encoding="utf-8") == "new"

    def test_custom_encoding(self, tmp_path: Path) -> None:
        path = tmp_path / "test.txt"
        content = "日本語"
        atomic_write_text(path, content, encoding="utf-8")
        assert path.read_text(encoding="utf-8") == content
