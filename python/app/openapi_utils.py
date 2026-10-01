"""Diagnostic OpenAPI export for the legacy FastAPI app.

``python/scripts/generate_openapi.py`` calls this helper with an explicit
output path. The desktop ``openapi.json`` is generated from Rust via
``npm run generate:api``; Python startup does not write schema files.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI


def write_openapi_json(app: FastAPI, *, path: str | Path) -> Path:
    """Serialize ``app.openapi()`` to *path* as stable, deterministic JSON.

    The output uses ``ensure_ascii=False`` (preserves non-ASCII),
    ``indent=2``, and ends with a trailing newline.  Repeated calls with the
    same ``app`` produce byte-identical output.

    Returns the resolved *path* for caller convenience.
    """
    path = Path(path)
    schema = app.openapi()
    text = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
    _ = path.write_text(text, encoding="utf-8")
    return path


__all__ = [
    "write_openapi_json",
]
