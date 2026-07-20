"""Tests for Ollama settings, model discovery, and schema-v2 route assignment endpoints."""

import json
import tempfile
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import override
from unittest.mock import patch

import pytest
from fastapi import FastAPI

from app.agents.route_catalog import OllamaStatusProvider, RouteProbeStatus
from app.api.settings import create_router
from app.core.config import SECRET_KEYS
from app.core.event_bus import EventBus
from app.core.events import ConfigChanged
from app.core.state import AppState
from app.services.config_loader import ConfigLoader
from app.services.secret_store import FileSecretStore
from app.services.settings_store import SettingsStore
from tests.helpers.api_client import TypedTestClient, as_json_object


def _make_client(
    tmp_path: Path,
    ollama_base_url: str = "http://localhost:11434/v1",
    *,
    ollama_status: OllamaStatusProvider | None = None,
) -> tuple[TypedTestClient, AppState, SettingsStore, EventBus, list[str]]:
    """Build a TypedTestClient with a fresh settings router."""
    config_path = tmp_path / "config.toml"
    default_path = tmp_path / "default.toml"
    _ = default_path.write_text(
        (
            "[ai]\nschema_version = 2\n\n[ai.assignments]\n\n"
            '[stt]\nbackend = "whisper"\nsample_rate = 16000\n\n'
            "[audio]\nsample_rate = 16000\n\n"
            f'[ollama]\nbase_url = "{ollama_base_url}"\n'
        ),
        encoding="utf-8",
    )
    store = SettingsStore(config_path=config_path, default_config_path=default_path)
    secret_store = FileSecretStore(path=tmp_path / "secrets.toml")
    event_bus = EventBus()

    config = ConfigLoader.from_settings_store(store)
    state = AppState(config=config, secret_store=secret_store)

    events: list[str] = []

    async def capture_event(event: ConfigChanged) -> None:
        events.append(type(event).__name__)

    event_bus.subscribe(ConfigChanged, capture_event)

    app = FastAPI()
    router = create_router(state=state, store=store, event_bus=event_bus, ollama_status=ollama_status)
    app.include_router(router)

    return TypedTestClient(app), state, store, event_bus, events


class TestGetSettingsOllama:
    """GET /api/settings should include ollama base_url."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in SECRET_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    def test_returns_custom_ollama_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td), ollama_base_url="http://custom:8080/v1")
            resp = client.get("/api/settings")
            assert resp.status_code == 200
            data = resp.json_object()
            ollama = as_json_object(data["ollama"])
            assert ollama["base_url"] == "http://custom:8080/v1"


class TestPostSettingsOllama:
    """POST /api/settings should save ollama base_url."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in SECRET_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    def test_saves_ollama_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, events = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={"ollama": {"base_url": "http://new-host:11434/v1"}},
            )
            assert resp.status_code == 200
            data = resp.json_object()
            assert data["ok"] is True

            settings = as_json_object(data["settings"])
            ollama = as_json_object(settings["ollama"])
            assert ollama["base_url"] == "http://new-host:11434/v1"

            # Verify it was persisted
            cfg = store.load_config()
            ollama_section = cfg.get("ollama")
            assert isinstance(ollama_section, dict)
            assert ollama_section["base_url"] == "http://new-host:11434/v1"

            # Verify ConfigChanged was published
            assert "ConfigChanged" in events


class TestOllamaRouteAssignment:
    """Schema-v2 route assignment is independent from Ollama endpoint settings."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in SECRET_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    def test_assigns_ollama_reply_route_and_persists_nullable_assignments(self) -> None:
        """A ready Ollama route can be selected for reply while other use cases remain unassigned."""

        async def ollama_ready() -> RouteProbeStatus:
            return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")

        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, events = _make_client(Path(td), ollama_status=ollama_ready)

            response = client.put(
                "/api/ai/routes/assignments",
                json={"reply": "ollama", "info": None, "minutes": None},
            )

            assert response.status_code == 200
            data = response.json_object()
            assert as_json_object(data["assignments"]) == {"reply": "ollama", "info": None, "minutes": None}
            assert events == ["ConfigChanged"]

            reloaded = ConfigLoader.from_settings_store(store)
            assert reloaded.ai_assignments.reply == "ollama"
            assert reloaded.ai_assignments.info is None
            assert reloaded.ai_assignments.minutes is None

            persisted = store.load_config()
            ai = persisted.get("ai")
            assert isinstance(ai, dict)
            assert ai["schema_version"] == 2
            assignments = ai.get("assignments")
            assert isinstance(assignments, dict)
            assert assignments == {"reply": "ollama"}


class _MockOllamaHandler(BaseHTTPRequestHandler):
    """Mock HTTP handler for Ollama /v1/models endpoint."""

    response_data: dict[str, object] = {"data": [{"id": "qwen3"}, {"id": "llama3"}]}
    status_code: int = 200

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/v1/models":
            self.send_response(self.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps(self.response_data).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    @override
    def log_message(self, format: str, *args: object) -> None:
        # Suppress log output during tests
        pass


class TestGetOllamaModels:
    """GET /api/settings/ollama/models should fetch and parse models."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in SECRET_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    def test_parses_openai_compatible_response(self) -> None:
        """Should parse {\"data\": [{\"id\": \"...\"}]} format."""
        # Start mock server
        _MockOllamaHandler.response_data = {"data": [{"id": "qwen3"}, {"id": "llama3"}]}
        _MockOllamaHandler.status_code = 200
        server = HTTPServer(("127.0.0.1", 0), _MockOllamaHandler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with tempfile.TemporaryDirectory() as td:
                client, _, _, _, _ = _make_client(Path(td))
                resp = client.get(f"/api/settings/ollama/models?base_url=http://127.0.0.1:{port}/v1")
                assert resp.status_code == 200
                data = resp.json_object()
                assert data["ok"] is True
                assert data["base_url"] == f"http://127.0.0.1:{port}/v1"
                models = data["models"]
                assert models == ["qwen3", "llama3"]
                assert data["message"] is None
        finally:
            server.shutdown()
            server.server_close()

    def test_handles_connection_error(self) -> None:
        """Should return ok=false with friendly message on connection failure."""
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            # Use a port that is unlikely to be in use
            resp = client.get("/api/settings/ollama/models?base_url=http://127.0.0.1:59999/v1")
            assert resp.status_code == 200
            data = resp.json_object()
            assert data["ok"] is False
            assert data["base_url"] == "http://127.0.0.1:59999/v1"
            models = data["models"]
            assert models == []
            message = data["message"]
            assert "接続" in str(message) or "通信" in str(message)

    def test_handles_timeout_error(self) -> None:
        """Should return ok=false with timeout-specific message when URLError wraps TimeoutError."""
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.side_effect = urllib.error.URLError(
                    reason=TimeoutError("timed out"),
                )
                resp = client.get(
                    "/api/settings/ollama/models?base_url=http://127.0.0.1:59999/v1",
                )
                assert resp.status_code == 200
                data = resp.json_object()
                assert data["ok"] is False
                message = data["message"]
                assert "タイムアウト" in str(message)

    def test_uses_configured_base_url_when_not_specified(self) -> None:
        """Should use state.config.ollama_base_url when base_url query param is omitted."""
        # Start mock server
        _MockOllamaHandler.response_data = {"data": [{"id": "test-model"}]}
        _MockOllamaHandler.status_code = 200
        server = HTTPServer(("127.0.0.1", 0), _MockOllamaHandler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with tempfile.TemporaryDirectory() as td:
                client, _, _, _, _ = _make_client(Path(td), ollama_base_url=f"http://127.0.0.1:{port}/v1")
                resp = client.get("/api/settings/ollama/models")
                assert resp.status_code == 200
                data = resp.json_object()
                assert data["ok"] is True
                models = data["models"]
                assert models == ["test-model"]
        finally:
            server.shutdown()
            server.server_close()

    def test_handles_invalid_json_response(self) -> None:
        """Should return ok=false when response is not valid JSON."""

        # Create a handler that returns invalid JSON
        class InvalidJsonHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/v1/models":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    _ = self.wfile.write(b"not valid json {{{")
                else:
                    self.send_response(404)
                    self.end_headers()

            @override
            def log_message(self, format: str, *args: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), InvalidJsonHandler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with tempfile.TemporaryDirectory() as td:
                client, _, _, _, _ = _make_client(Path(td))
                resp = client.get(f"/api/settings/ollama/models?base_url=http://127.0.0.1:{port}/v1")
                assert resp.status_code == 200
                data = resp.json_object()
                assert data["ok"] is False
                message = data["message"]
                assert "解析" in str(message)
        finally:
            server.shutdown()
            server.server_close()

    def test_handles_missing_data_field(self) -> None:
        """Should return ok=false when response lacks 'data' field."""
        _MockOllamaHandler.response_data = {"models": []}  # Wrong structure
        _MockOllamaHandler.status_code = 200
        server = HTTPServer(("127.0.0.1", 0), _MockOllamaHandler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with tempfile.TemporaryDirectory() as td:
                client, _, _, _, _ = _make_client(Path(td))
                resp = client.get(f"/api/settings/ollama/models?base_url=http://127.0.0.1:{port}/v1")
                assert resp.status_code == 200
                data = resp.json_object()
                assert data["ok"] is False
                message = data["message"]
                assert "モデル一覧" in str(message)
        finally:
            server.shutdown()
            server.server_close()

    def test_handles_http_error_status(self) -> None:
        """Should return ok=false when server returns non-200 status."""
        _MockOllamaHandler.response_data = {"data": []}
        _MockOllamaHandler.status_code = 500
        server = HTTPServer(("127.0.0.1", 0), _MockOllamaHandler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with tempfile.TemporaryDirectory() as td:
                client, _, _, _, _ = _make_client(Path(td))
                resp = client.get(f"/api/settings/ollama/models?base_url=http://127.0.0.1:{port}/v1")
                assert resp.status_code == 200
                data = resp.json_object()
                assert data["ok"] is False
                message = data["message"]
                # HTTPError (a subclass of URLError) handles non‑200 HTTP status codes
                assert (
                    "接続" in str(message)
                    or "通信" in str(message)
                    or "エラー" in str(message)
                    or "500" in str(message)
                )
        finally:
            server.shutdown()
            server.server_close()
