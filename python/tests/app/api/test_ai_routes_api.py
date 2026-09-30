"""Black-box contracts for schema-v2 AI route discovery and assignment."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI

from app.agents.route_catalog import OllamaStatusProvider, RouteProbeStatus
from app.api.settings import create_router
from app.core.event_bus import EventBus
from app.core.events import ConfigChanged
from app.core.state import AppState
from app.services.config_loader import ConfigLoader
from app.services.secret_store import FileSecretStore
from app.services.settings_store import SettingsStore
from tests.helpers.api_client import JsonObject, TypedTestClient, as_json_array, as_json_object, as_object_array


def _make_client(
    tmp_path: Path,
    *,
    config_text: str = "[ai]\nschema_version = 2\n",
    ollama_status: OllamaStatusProvider | None = None,
) -> tuple[TypedTestClient, SettingsStore, list[str]]:
    config_path = tmp_path / "config.toml"
    default_path = tmp_path / "default.toml"
    _ = default_path.write_text("[ai]\nschema_version = 2\n", encoding="utf-8")
    _ = config_path.write_text(config_text, encoding="utf-8")
    store = SettingsStore(config_path=config_path, default_config_path=default_path)
    state = AppState(
        config=ConfigLoader.from_settings_store(store),
        secret_store=FileSecretStore(path=tmp_path / "secrets.toml"),
    )
    event_bus = EventBus()
    events: list[str] = []

    async def capture(event: ConfigChanged) -> None:
        events.append(type(event).__name__)

    event_bus.subscribe(ConfigChanged, capture)
    app = FastAPI()
    app.include_router(
        create_router(
            state=state,
            store=store,
            event_bus=event_bus,
            ollama_status=ollama_status,
        )
    )
    return TypedTestClient(app), store, events


def _routes(data: JsonObject) -> dict[str, JsonObject]:
    return {str(route["id"]): route for route in as_object_array(data["routes"])}


def test_retired_info_settings_are_not_exposed_or_writable(tmp_path: Path) -> None:
    client, store, events = _make_client(
        tmp_path,
        config_text=(
            '[ai]\nschema_version = 2\n[ai.assignments]\ninfo = "retired-route"\n[agents]\ninfo_enabled = true\n'
        ),
    )
    original_config = store.config_path.read_text(encoding="utf-8")

    assert "agents" not in client.get("/api/settings").json_object()
    catalog = client.get("/api/ai/routes").json_object()
    assert as_json_object(catalog["assignments"]) == {"reply": None}
    assert all("info" not in as_json_array(route["capabilities"]) for route in _routes(catalog).values())
    assert client.put("/api/ai/routes/assignments", json={"reply": None, "info": "openai"}).status_code == 422
    assert client.post("/api/settings", json={"agents": {"info_enabled": True}}).status_code == 422
    assert store.config_path.read_text(encoding="utf-8") == original_config
    assert events == []


def test_catalog_exposes_unassigned_routes_and_non_selectable_managed_and_unready_runtimes(tmp_path: Path) -> None:
    """A fresh configuration must advertise unavailable choices without selecting or emulating one."""
    client, _, _ = _make_client(tmp_path)

    response = client.get("/api/ai/routes")

    assert response.status_code == 200
    data = response.json_object()
    assert as_json_object(data["assignments"]) == {"reply": None}
    routes = _routes(data)
    managed = routes["managed"]
    assert {
        "availability": managed["availability"],
        "readiness": managed["readiness"],
        "selectable": managed["selectable"],
        "reason_code": managed["reason_code"],
    } == {
        "availability": "experimental",
        "readiness": "not_offered",
        "selectable": False,
        "reason_code": "MANAGED_SERVICE_NOT_CONFIGURED",
    }


@pytest.mark.parametrize(
    ("base_url", "expected_location"),
    [
        ("http://localhost:11434/v1", "local"),
        ("http://127.0.0.1:11434/v1", "local"),
        ("http://[::1]:11434/v1", "local"),
        ("https://ollama.example.com/v1", "external"),
        ("not-a-url", "unknown"),
    ],
)
def test_ollama_route_location_follows_the_configured_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
    expected_location: str,
) -> None:
    """The public route must not describe a custom remote Ollama endpoint as local."""

    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    async def ollama_ready() -> RouteProbeStatus:
        return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")

    client, _, _ = _make_client(
        tmp_path,
        config_text=(f'[ai]\nschema_version = 2\n\n[ollama]\nbase_url = "{base_url}"\n'),
        ollama_status=ollama_ready,
    )

    response = client.get("/api/ai/routes")

    assert response.status_code == 200
    assert _routes(response.json_object())["ollama"]["data_location"] == expected_location


def test_saving_byok_secret_changes_route_readiness_without_returning_the_secret(tmp_path: Path) -> None:
    """BYOK credentials are write-only while their presence enables the corresponding route."""
    client, _, _ = _make_client(tmp_path)

    before = _routes(client.get("/api/ai/routes").json_object())["openai"]
    saved = client.post("/api/settings", json={"secrets": {"OPENAI_API_KEY": "route-test-secret"}})
    after = _routes(client.get("/api/ai/routes").json_object())["openai"]

    assert before["readiness"] == "setup_required"
    assert before["reason_code"] == "API_CREDENTIAL_NOT_CONFIGURED"
    assert saved.status_code == 200
    assert "route-test-secret" not in saved.text
    assert after["readiness"] == "ready"
    assert after["selectable"] is True


def test_assignment_update_persists_an_openai_selection_across_reload(tmp_path: Path) -> None:
    """A full assignment replacement must survive reload and mark the selected route in its response."""

    client, store, events = _make_client(tmp_path)

    response = client.put("/api/ai/routes/assignments", json={"reply": "openai"})

    assert response.status_code == 200
    data = response.json_object()
    assert as_json_object(data["assignments"]) == {"reply": "openai"}
    assert _routes(data)["openai"]["selected"] is True
    assert events == ["ConfigChanged"]
    reloaded = ConfigLoader.from_settings_store(store)
    assert reloaded.ai_assignments.reply == "openai"


def test_retired_minutes_assignment_is_rejected(tmp_path: Path) -> None:
    client, _state, _store = _make_client(tmp_path)
    response = client.put("/api/ai/routes/assignments", json={"reply": None, "minutes": "openai"})
    assert response.status_code == 422


def test_assignment_update_rejects_unknown_or_unsupported_or_not_offered_routes(tmp_path: Path) -> None:
    """Invalid route choices must be rejected before persisting an unusable assignment."""

    client, store, events = _make_client(tmp_path)
    cases = (
        ("unknown route", {"reply": "missing"}, "AI_ROUTE_NOT_FOUND"),
        ("planned managed reply", {"reply": "managed"}, "AI_ROUTE_NOT_SELECTABLE"),
    )

    for name, body, code in cases:
        response = client.put("/api/ai/routes/assignments", json=body)
        assert response.status_code == 422, name
        detail = as_json_object(response.json_object()["detail"])
        assert detail["code"] == code
        assert detail["retryable"] is False

    assert events == []
    reloaded = ConfigLoader.from_settings_store(store)
    assert reloaded.ai_assignments.reply is None


@pytest.mark.parametrize("route_id", ["codex", "acp"])
def test_retired_routes_are_ignored_on_load_and_rejected_on_save(tmp_path: Path, route_id: str) -> None:
    client, store, events = _make_client(
        tmp_path,
        config_text=(
            f'[ai]\nschema_version = 2\n[ai.assignments]\nreply = "{route_id}"\nminutes = "{route_id}"\n'
            '[ai.routes.codex]\nruntime = "codex-app-server"\n'
            '[ai.routes.acp]\ncommand = ["synthetic-agent"]\n'
        ),
    )
    original = store.config_path.read_text(encoding="utf-8")
    catalog = client.get("/api/ai/routes").json_object()
    assert not {"codex", "acp"}.intersection(_routes(catalog))
    assert as_json_object(catalog["assignments"]) == {"reply": None}
    assert "acp" not in client.get("/api/settings").json_object()
    assert client.put("/api/ai/routes/assignments", json={"reply": route_id}).status_code == 422
    assert store.config_path.read_text(encoding="utf-8") == original
    assert events == []
