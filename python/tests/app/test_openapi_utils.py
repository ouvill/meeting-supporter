"""Tests for app.openapi_utils — shared OpenAPI write helper."""

import json
import os
import tempfile
from pathlib import Path
from typing import cast

from fastapi import FastAPI

from app.openapi_utils import write_openapi_json


def test_write_openapi_json_creates_valid_json() -> None:
    """write_openapi_json produces a valid JSON file."""
    app = FastAPI()

    @app.get("/test")
    # Registered via app.get() for OpenAPI schema generation — reportUnusedFunction expected.
    async def test_endpoint() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"ok": "yes"}

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "openapi.json")
        result = write_openapi_json(app, path=path)

        assert Path(result).resolve() == Path(path).resolve()
        assert Path(path).exists()

        with open(path, encoding="utf-8") as f:
            data: dict[str, object] = cast(dict[str, object], json.load(f))

        # Basic structural checks
        assert "openapi" in data
        assert "info" in data
        assert "paths" in data
        paths = data.get("paths")
        assert isinstance(paths, dict)
        assert "/test" in paths


def test_write_openapi_json_deterministic() -> None:
    """Repeated calls produce byte-identical output."""
    app = FastAPI()

    @app.get("/ping")
    # Registered via app.get() for OpenAPI schema generation — reportUnusedFunction expected.
    async def ping() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"pong": "ok"}

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "openapi.json")

        _ = write_openapi_json(app, path=path)
        first = Path(path).read_bytes()

        _ = write_openapi_json(app, path=path)
        second = Path(path).read_bytes()

        assert first == second


def test_write_openapi_json_ends_with_newline() -> None:
    """The file ends with a newline character."""
    app = FastAPI()

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "openapi.json")
        _ = write_openapi_json(app, path=path)
        content = Path(path).read_bytes()
        assert content.endswith(b"\n")


def test_write_openapi_json_uses_utf8_non_ascii() -> None:
    """Non-ASCII characters in the schema are not escaped."""
    app = FastAPI(title="テストAPI")

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "openapi.json")
        _ = write_openapi_json(app, path=path)
        text = Path(path).read_text(encoding="utf-8")
        raw = Path(path).read_bytes()

        # Non-ASCII characters appear literally (not \uXXXX)
        assert "テストAPI" in text
        # Confirm the raw bytes contain the UTF-8 encoding, not ASCII escapes
        assert "テストAPI".encode() in raw
        # No ASCII escape sequences for non-ASCII chars
        assert b"\\u30c6" not in raw  # \u30c6 = テ


def test_write_openapi_json_with_test_client_router() -> None:
    """The output captures routers created by the factory pattern used in this project."""
    from app.api.system import create_router

    app = FastAPI()
    app.include_router(create_router(get_input_devices=lambda: []))

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "openapi.json")
        _ = write_openapi_json(app, path=path)
        data: dict[str, object] = cast(dict[str, object], json.loads(Path(path).read_text(encoding="utf-8")))

        paths = data.get("paths")
        assert isinstance(paths, dict)
        assert "/health" in paths
        assert "/" in paths
        assert "/devices" in paths
