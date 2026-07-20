#!/usr/bin/env python3
"""Regenerate ``openapi.json`` from the FastAPI app definition.

This script uses a lightweight app factory that does **not** import
``main.py`` or initialise any runtime services (audio devices, LLM agents,
STT backends, database connections, user config files).  This makes it safe
to run in CI / headless environments where those dependencies may be missing.

The canonical invocation::

    cd python && uv run python scripts/generate_openapi.py

Or (via npm, preferred)::

    npm run generate:openapi
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the ``python/`` directory is on sys.path so that the factory
# and router modules can be imported when running as
# ``python scripts/generate_openapi.py`` from the python/ dir.
_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(_PYTHON_DIR))


def main() -> None:
    # Import only the lightweight factory — no runtime service initialisation.
    from app.factory import create_openapi_app  # noqa: PLC0415

    app = create_openapi_app()

    repo_root = _PYTHON_DIR.parent
    output_path = repo_root / "openapi.json"

    from app.openapi_utils import write_openapi_json  # noqa: PLC0415

    _ = write_openapi_json(app, path=output_path)
    print(f"OpenAPI schema written to {output_path.resolve()}")


if __name__ == "__main__":
    main()
