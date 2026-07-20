"""Shared utilities for generating the OpenAPI schema from the FastAPI app.

This module factors out the ``write_openapi_json`` logic used by both the
runtime ``lifespan.py`` (DEBUG startup side-effect) and the standalone
``generate_openapi.py`` script so that the write behaviour is identical
in both paths.

The canonical way to regenerate ``openapi.json`` is the npm script::

    npm run generate:openapi

which invokes ``python/scripts/generate_openapi.py``.
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
