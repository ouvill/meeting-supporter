"""Shared file I/O utilities (internal to services package)."""

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write *content* to *path* atomically.

    Writes to a unique temporary file in the same directory first,
    then uses ``os.replace()`` to atomically replace the target file.
    If the write or rename fails the temporary file is cleaned up and
    the original file is left intact.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding=encoding,
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(tmp.name)
    try:
        _ = tmp.write(content)
        tmp.close()
        os.replace(tmp_path, path)
    except (OSError, UnicodeEncodeError):
        # Ensure the temp file is closed before attempting cleanup.
        # On Windows, open files cannot be unlinked.
        try:
            tmp.close()
        except OSError:
            pass
        # Clean up the temp file on failure.
        # The original file is untouched: either os.replace() was never
        # reached, or it failed before making any change.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
