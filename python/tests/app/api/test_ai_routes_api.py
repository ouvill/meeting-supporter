"""Black-box contracts for schema-v2 AI route discovery and assignment."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI

from app.agents.route_catalog import CodexStatusProvider, OllamaStatusProvider, RouteProbeStatus
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
    codex_status: CodexStatusProvider | None = None,
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
            codex_status=codex_status,
            ollama_status=ollama_status,
        )
    )
    return TypedTestClient(app), store, events


def _routes(data: JsonObject) -> dict[str, JsonObject]:
    return {str(route["id"]): route for route in as_object_array(data["routes"])}


def test_catalog_exposes_unassigned_routes_and_non_selectable_managed_and_unready_runtimes(tmp_path: Path) -> None:
    """A fresh configuration must advertise unavailable choices without selecting or emulating one."""
    client, _, _ = _make_client(tmp_path)

    response = client.get("/api/ai/routes")

    assert response.status_code == 200
    data = response.json_object()
    assert as_json_object(data["assignments"]) == {"reply": None, "info": None, "minutes": None}
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
    codex = routes["codex"]
    assert (codex["availability"], codex["readiness"], codex["selectable"], codex["action"]) == (
        "experimental",
        "unknown",
        False,
        "login",
    )
    assert "minutes" in as_json_array(codex["capabilities"])
    assert "info" in as_json_array(codex["capabilities"])
    acp = routes["acp"]
    assert (acp["availability"], acp["readiness"], acp["selectable"], acp["reason_code"]) == (
        "experimental",
        "setup_required",
        False,
        "ACP_COMMAND_NOT_CONFIGURED",
    )


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


def test_runtime_probes_make_codex_and_acp_selectable_only_when_ready_and_report_ollama_readiness(
    tmp_path: Path,
) -> None:
    """Runtime readiness, rather than provider kind, controls whether experimental routes can be selected."""

    async def ready(requested_model: str) -> RouteProbeStatus:
        _ = requested_model
        return RouteProbeStatus(
            readiness="ready",
            reason_code="",
            message="利用できます。",
            service_tier="priority",
        )

    async def ollama_ready() -> RouteProbeStatus:
        return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")

    client, _, _ = _make_client(
        tmp_path,
        config_text="""[ai]
schema_version = 2

[ai.routes.acp]
command = ["acp-agent", "--stdio"]
""",
        codex_status=ready,
        ollama_status=ollama_ready,
    )

    response = client.get("/api/ai/routes")

    assert response.status_code == 200
    routes = _routes(response.json_object())
    assert (routes["codex"]["readiness"], routes["codex"]["selectable"]) == ("ready", True)
    assert routes["codex"]["service_tier"] == "priority"
    assert (routes["acp"]["readiness"], routes["acp"]["selectable"]) == ("ready", True)
    assert (routes["ollama"]["readiness"], routes["ollama"]["selectable"]) == ("ready", True)


def test_catalog_selects_codex_only_when_its_configured_luna_model_is_available(tmp_path: Path) -> None:
    """A configured Luna route becomes selectable only after its own model availability probe succeeds."""

    async def luna_available(requested_model: str) -> RouteProbeStatus:
        if requested_model == "gpt-5.6-luna":
            return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")
        return RouteProbeStatus(
            readiness="setup_required",
            reason_code="CODEX_MODEL_UNAVAILABLE",
            message="設定したCodexモデルを利用できません。",
            action="configure",
        )

    client, _, _ = _make_client(
        tmp_path,
        config_text="""[ai]
schema_version = 2

[ai.routes.codex]
model = "gpt-5.6-luna"
""",
        codex_status=luna_available,
    )

    response = client.get("/api/ai/routes")

    assert response.status_code == 200
    codex = _routes(response.json_object())["codex"]
    assert (codex["readiness"], codex["selectable"], codex["reason_code"]) == ("ready", True, None)


def test_catalog_keeps_a_protocol_valid_newer_codex_selectable_and_exposes_its_warning(tmp_path: Path) -> None:
    """A ready newer CLI remains selectable while clients receive its compatibility warning."""

    async def newer_codex_ready(requested_model: str) -> RouteProbeStatus:
        _ = requested_model
        return RouteProbeStatus(
            readiness="ready",
            reason_code="untested_newer_version",
            message="このCodex CLIバージョンは未検証です。",
        )

    client, _, _ = _make_client(
        tmp_path,
        config_text="""[ai]
schema_version = 2

[ai.routes.codex]
model = "gpt-5.6-luna"
""",
        codex_status=newer_codex_ready,
    )

    response = client.get("/api/ai/routes")

    assert response.status_code == 200
    codex = _routes(response.json_object())["codex"]
    assert (codex["readiness"], codex["selectable"], codex["reason_code"]) == (
        "ready",
        True,
        "untested_newer_version",
    )


def test_catalog_does_not_substitute_an_available_legacy_model_when_luna_is_unavailable(tmp_path: Path) -> None:
    """An unavailable configured Luna model blocks selection even if a legacy model would be accepted."""

    async def luna_unavailable(requested_model: str) -> RouteProbeStatus:
        if requested_model == "gpt-5.4-mini":
            return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")
        return RouteProbeStatus(
            readiness="setup_required",
            reason_code="CODEX_MODEL_UNAVAILABLE",
            message="設定したCodexモデルを利用できません。",
            action="configure",
        )

    client, _, _ = _make_client(
        tmp_path,
        config_text="""[ai]
schema_version = 2

[ai.routes.codex]
model = "gpt-5.6-luna"
""",
        codex_status=luna_unavailable,
    )

    response = client.get("/api/ai/routes")

    assert response.status_code == 200
    codex = _routes(response.json_object())["codex"]
    assert (codex["readiness"], codex["selectable"], codex["reason_code"]) == (
        "setup_required",
        False,
        "CODEX_MODEL_UNAVAILABLE",
    )


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


def test_assignment_update_persists_a_ready_codex_selection_across_reload(tmp_path: Path) -> None:
    """A full assignment replacement must survive reload and mark the selected route in its response."""

    async def codex_ready(requested_model: str) -> RouteProbeStatus:
        _ = requested_model
        return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")

    client, store, events = _make_client(tmp_path, codex_status=codex_ready)

    response = client.put("/api/ai/routes/assignments", json={"reply": "codex", "info": None, "minutes": None})

    assert response.status_code == 200
    data = response.json_object()
    assert as_json_object(data["assignments"]) == {"reply": "codex", "info": None, "minutes": None}
    assert _routes(data)["codex"]["selected"] is True
    assert events == ["ConfigChanged"]
    reloaded = ConfigLoader.from_settings_store(store)
    assert reloaded.ai_assignments.reply == "codex"
    assert reloaded.ai_assignments.info is None
    assert reloaded.ai_assignments.minutes is None


def test_assignment_update_persists_a_ready_codex_info_selection_across_reload(tmp_path: Path) -> None:
    async def codex_ready(requested_model: str) -> RouteProbeStatus:
        _ = requested_model
        return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")

    client, store, events = _make_client(tmp_path, codex_status=codex_ready)

    response = client.put("/api/ai/routes/assignments", json={"reply": None, "info": "codex", "minutes": None})

    assert response.status_code == 200
    data = response.json_object()
    assert as_json_object(data["assignments"]) == {"reply": None, "info": "codex", "minutes": None}
    assert _routes(data)["codex"]["selected"] is True
    assert events == ["ConfigChanged"]
    reloaded = ConfigLoader.from_settings_store(store)
    assert reloaded.ai_assignments.reply is None
    assert reloaded.ai_assignments.info == "codex"
    assert reloaded.ai_assignments.minutes is None


def test_assignment_update_persists_a_ready_codex_minutes_selection_across_reload(tmp_path: Path) -> None:
    """Minutes assignment selects Codex directly and must not depend on a reply assignment."""

    async def codex_ready(requested_model: str) -> RouteProbeStatus:
        _ = requested_model
        return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")

    client, store, events = _make_client(tmp_path, codex_status=codex_ready)

    response = client.put("/api/ai/routes/assignments", json={"reply": None, "info": None, "minutes": "codex"})

    assert response.status_code == 200
    data = response.json_object()
    assert as_json_object(data["assignments"]) == {"reply": None, "info": None, "minutes": "codex"}
    assert _routes(data)["codex"]["selected"] is True
    assert events == ["ConfigChanged"]
    reloaded = ConfigLoader.from_settings_store(store)
    assert reloaded.ai_assignments.reply is None
    assert reloaded.ai_assignments.info is None
    assert reloaded.ai_assignments.minutes == "codex"


def test_assignment_update_rejects_unknown_or_unsupported_or_not_offered_routes(tmp_path: Path) -> None:
    """Invalid route choices must be rejected before persisting an unusable assignment."""

    async def codex_ready(requested_model: str) -> RouteProbeStatus:
        _ = requested_model
        return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")

    client, store, events = _make_client(tmp_path, codex_status=codex_ready)
    cases = (
        ("unknown route", {"reply": "missing", "info": None, "minutes": None}, "AI_ROUTE_NOT_FOUND"),
        ("planned managed reply", {"reply": "managed", "info": None, "minutes": None}, "AI_ROUTE_NOT_SELECTABLE"),
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
    assert reloaded.ai_assignments.info is None
    assert reloaded.ai_assignments.minutes is None
