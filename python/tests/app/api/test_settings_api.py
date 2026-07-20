"""Tests for app.api.settings — GET/POST /api/settings."""

import os
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI

from app.api.settings import create_router
from app.core.config import SECRET_KEYS
from app.core.event_bus import EventBus
from app.core.events import ConfigChanged
from app.core.state import AppState
from app.meetings.models import MeetingSession
from app.services.secret_store import FileSecretStore
from app.services.settings_store import SettingsStore
from app.services.usage_logger import UsageLogger
from tests.helpers.api_client import JsonObject, TypedResponse, TypedTestClient, as_json_object, as_object_array


class _ConnectionResponse:
    def __init__(self, status: int) -> None:
        self.status: int = status

    def __enter__(self) -> "_ConnectionResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        _ = exc_type, exc, traceback


class _ConnectionUrlopen:
    def __init__(self, status: int = 200) -> None:
        self.status: int = status
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> _ConnectionResponse:
        assert timeout == 5
        self.requests.append(request)
        return _ConnectionResponse(self.status)


def _make_client(
    tmp_path: Path,
    *,
    config_text: str | None = None,
) -> tuple[TypedTestClient, AppState, SettingsStore, EventBus, list[str]]:
    """Build a TypedTestClient with a fresh settings router."""
    config_path = tmp_path / "config.toml"
    default_path = tmp_path / "default.toml"
    _ = default_path.write_text(
        '[ai]\nschema_version = 2\n\n[stt]\nbackend = "whisper"\nsample_rate = 16000\n\n[audio]\nsample_rate = 16000\n',
        encoding="utf-8",
    )
    store = SettingsStore(config_path=config_path, default_config_path=default_path)
    if config_text is not None:
        _ = store.config_path.write_text(config_text, encoding="utf-8")
    secret_store = FileSecretStore(path=tmp_path / "secrets.toml")
    event_bus = EventBus()

    # Minimal AppState for settings router.
    from app.services.config_loader import ConfigLoader

    config = ConfigLoader.from_settings_store(store)
    state = AppState(config=config, secret_store=secret_store)

    events: list[str] = []

    async def capture_event(event: ConfigChanged) -> None:
        events.append(type(event).__name__)

    event_bus.subscribe(ConfigChanged, capture_event)

    app = FastAPI()
    router = create_router(state=state, store=store, event_bus=event_bus)
    app.include_router(router)

    return TypedTestClient(app), state, store, event_bus, events


class TestGetSettings:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Remove all known secret env vars before each test to prevent leakage."""
        for key in SECRET_KEYS:
            monkeypatch.delenv(key, raising=False)

    def test_provider_summaries_include_data_locations_without_secret_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_text = """
[providers.lmstudio]
label = "LM Studio"
kind = "openai-compatible"
base_url = "http://localhost:1234/v1"
data_location = "local"
key_ref = "PROVIDER_LMSTUDIO_API_KEY"
models = ["qwen2.5"]
"""
        monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td), config_text=config_text)

            resp = client.get("/api/settings")

            assert resp.status_code == 200
            data = resp.json_object()
            providers = as_object_array(data["providers"])
            by_id = {str(provider["id"]): provider for provider in providers}
            assert by_id["openai"]["data_location"] == "cloud"
            assert by_id["openai"]["api_key_configured"] is True
            assert by_id["lmstudio"]["data_location"] == "local"
            assert by_id["lmstudio"]["api_key_configured"] is False
            assert "openai-secret" not in resp.text

    def test_reply_settings_reflect_target_config(self) -> None:
        config_text = """
[agents]
info_enabled = false

[reply]
enabled = false
auto_generate = true
default_style = "polite"

[[reply.styles]]
id = "polite"
label = "丁寧"
enabled = false
priority = 20
"""
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td), config_text=config_text)
            resp = client.get("/api/settings")
            assert resp.status_code == 200
            data = resp.json_object()
            agents = as_json_object(data["agents"])
            reply = as_json_object(data["reply"])
            styles = as_object_array(reply["styles"])
            assert agents["info_enabled"] is False
            assert "reply_enabled" not in agents
            assert "reply_auto_generate" not in agents
            assert reply["enabled"] is False
            assert reply["auto_generate"] is True
            assert reply["default_style"] == "polite"
            assert [style["id"] for style in styles] == ["polite"]
            assert styles[0]["enabled"] is False

    def test_returns_saved_usage_budget_and_current_usage_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            client, state, store, _, _ = _make_client(tmp_path)
            state.config.user_data_dir = tmp_path / "data"
            state.current_session = MeetingSession(id="meeting-alpha", started_at=datetime.now(UTC))
            store.write_sectioned_toml(
                store.config_path,
                {"usage_budget": {"meeting_limit_jpy": 40.0, "monthly_limit_jpy": 300.0}},
            )
            usage_logger = UsageLogger(state.config.user_data_dir / "usage.jsonl")
            usage_logger.log(
                meeting_id="meeting-alpha",
                agent_id="reply_main",
                model="openai/gpt-5.4-nano",
                input_tokens=500_000,
                output_tokens=250_000,
                elapsed_s=0.5,
            )
            usage_logger.log(
                meeting_id="meeting-beta",
                agent_id="info",
                model="gemini/gemini-2.5-flash-lite",
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                elapsed_s=0.7,
            )

            resp = client.get("/api/settings")

            assert resp.status_code == 200
            usage = as_json_object(resp.json_object()["usage"])
            budget = as_json_object(usage["budget"])
            assert budget["meeting_limit_jpy"] == 40.0
            assert budget["monthly_limit_jpy"] == 300.0
            current_meeting = as_json_object(usage["current_meeting"])
            assert current_meeting["request_count"] == 1
            assert current_meeting["input_tokens"] == 500_000
            assert current_meeting["output_tokens"] == 250_000
            assert current_meeting["estimated_cost_jpy"] == 12.0
            current_month = as_json_object(usage["current_month"])
            assert current_month["request_count"] == 2
            assert current_month["estimated_cost_jpy"] == 92.0


class TestPostSettings:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Remove all known secret env vars before each test to prevent leakage."""
        for key in SECRET_KEYS:
            monkeypatch.delenv(key, raising=False)

    def test_updates_reply_settings_and_info_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={
                    "agents": {"info_enabled": False},
                    "reply": {"enabled": False, "auto_generate": True},
                },
            )
            assert resp.status_code == 200
            assert resp.json_object()["ok"] is True

            cfg = store.load_config()
            agents = cfg["agents"]
            reply = cfg["reply"]
            assert isinstance(agents, dict)
            assert isinstance(reply, dict)
            assert agents == {"info_enabled": False}
            assert reply["enabled"] is False
            assert reply["auto_generate"] is True
            text = store.config_path.read_text(encoding="utf-8")
            assert "[reply]" in text
            assert "[[reply.styles]]" in text
            assert "reply_agents" not in text
            assert "reply_main" not in text
            assert "reply_polite" not in text
            assert "reply_enabled" not in text
            assert "reply_auto_generate" not in text

    def test_updates_custom_reply_style_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, state, store, _, _ = _make_client(Path(td))
            state.config.reply_agent_definitions = [
                *state.config.reply_agent_definitions,
                state.config.reply_agent_definitions[0].__class__(
                    id="reply_custom",
                    label="Custom",
                    enabled=True,
                    priority=5,
                    instruction="Custom instruction",
                ),
            ]

            resp = client.post(
                "/api/settings",
                json={"reply": {"styles": [{"id": "reply_custom", "enabled": False}]}},
            )
            assert resp.status_code == 200
            assert resp.json_object()["ok"] is True

            cfg = store.load_config()
            reply = cfg["reply"]
            assert isinstance(reply, dict)
            styles = reply["styles"]
            assert isinstance(styles, list)
            custom = next(style for style in styles if isinstance(style, dict) and style["id"] == "reply_custom")
            assert custom["enabled"] is False
            assert custom["instruction"] == "Custom instruction"
            assert "reply_agents" not in cfg

    def test_rejects_disabling_all_reply_styles_while_reply_feature_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={
                    "reply": {
                        "enabled": True,
                        "styles": [
                            {"id": "standard", "enabled": False},
                        ],
                    }
                },
            )
            assert resp.status_code == 400
            assert "最低1つ有効" in resp.text

    def test_allows_disabling_all_reply_styles_when_reply_feature_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={
                    "reply": {
                        "enabled": False,
                        "styles": [
                            {"id": "standard", "enabled": False},
                        ],
                    }
                },
            )
            assert resp.status_code == 200

            cfg = store.load_config()
            reply = cfg["reply"]
            assert isinstance(reply, dict)
            styles = reply["styles"]
            assert isinstance(styles, list)
            assert all(isinstance(style, dict) and style["enabled"] is False for style in styles)
            assert "reply_agents" not in cfg

    def test_rejects_invalid_agents_type(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            resp = client.post("/api/settings", json={"agents": "not_a_dict"})
            # Pydantic model validation now returns 422 for type mismatches.
            assert resp.status_code == 422

    def test_rejects_coercible_string_for_reply_value(self) -> None:
        """StrictBool rejects ``"yes"`` (422) instead of coercing to ``True``."""
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={"reply": {"enabled": "yes"}},
            )
            assert resp.status_code == 422

    def test_rejects_legacy_reply_flags_in_agents_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _store, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={
                    "agents": {
                        "reply_enabled": True,
                        "reply_main": False,
                        "reply_polite": False,
                    }
                },
            )
            assert resp.status_code == 422

    def test_rejects_legacy_reply_agents_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _store, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={"reply_agents": [{"id": "reply_custom", "enabled": False}]},
            )
            assert resp.status_code == 422

    def test_updates_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={"secrets": {"DEEPGRAM_API_KEY": "secret123"}},
            )
            assert resp.status_code == 200
            assert resp.json_object()["ok"] is True

            # Secret should now be available in env.
            import os

            assert os.getenv("DEEPGRAM_API_KEY") == "secret123"

    def test_updates_config_sections(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={"stt": {"backend": "deepgram", "sample_rate": 48000}},
            )
            assert resp.status_code == 200
            assert resp.json_object()["ok"] is True

            cfg = store.load_config()
            stt = cfg["stt"]
            assert isinstance(stt, dict)
            assert stt["backend"] == "deepgram"
            assert stt["sample_rate"] == 48000

    def test_merges_config_sections(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={"stt": {"backend": "remote"}},
            )
            assert resp.status_code == 200

            cfg = store.load_config()
            stt = cfg["stt"]
            assert isinstance(stt, dict)
            assert stt["backend"] == "remote"
            # Existing keys should be preserved.
            assert stt["sample_rate"] == 16000

    def test_empty_body_returns_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            resp = client.post("/api/settings", json={})
            assert resp.status_code == 200
            assert resp.json_object()["ok"] is True


class TestSettingsApiPostReturnsSavedValues:
    """POST /api/settings should return actual saved values in the response."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Remove all known secret env vars before each test to prevent leakage."""
        for key in SECRET_KEYS:
            monkeypatch.delenv(key, raising=False)

    # ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _ok_settings(resp: TypedResponse) -> JsonObject:
        data = resp.json_object()
        assert data.get("ok") is True
        settings_val = data.get("settings", {})
        return as_json_object(settings_val)

    # ── tests ──────────────────────────────────────────────────────────────────
    def test_saving_secret_updates_runtime_environment(self) -> None:
        previous = os.environ.get("GEMINI_API_KEY")
        try:
            with tempfile.TemporaryDirectory() as td:
                client, state, _, _, events = _make_client(Path(td))
                resp = client.post(
                    "/api/settings",
                    json={"secrets": {"GEMINI_API_KEY": "runtime-gemini-key"}},
                )

                assert resp.status_code == 200
                settings = self._ok_settings(resp)
                secrets = as_json_object(settings["secrets"])
                assert secrets["GEMINI_API_KEY"] is True
                assert state.secret_store.get("GEMINI_API_KEY") == "runtime-gemini-key"
                assert os.environ["GEMINI_API_KEY"] == "runtime-gemini-key"
                assert "ConfigChanged" in events
        finally:
            if previous is None:
                _ = os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = previous

    def test_xai_secret_is_persisted_and_responses_expose_only_status(self) -> None:
        secret = "xai-secret-that-must-not-leak"
        previous = os.environ.get("XAI_API_KEY")
        try:
            with tempfile.TemporaryDirectory() as td:
                client, state, _, _, _ = _make_client(Path(td))

                before = client.get("/api/settings")
                assert before.status_code == 200
                before_secrets = as_json_object(before.json_object()["secrets"])
                assert before_secrets["XAI_API_KEY"] is False

                saved = client.post("/api/settings", json={"secrets": {"XAI_API_KEY": secret}})
                assert saved.status_code == 200
                saved_settings = self._ok_settings(saved)
                saved_secrets = as_json_object(saved_settings["secrets"])
                assert saved_secrets["XAI_API_KEY"] is True
                assert isinstance(saved_secrets["XAI_API_KEY"], bool)
                assert secret not in saved.text
                assert state.secret_store.get("XAI_API_KEY") == secret

                loaded = client.get("/api/settings")
                assert loaded.status_code == 200
                loaded_secrets = as_json_object(loaded.json_object()["secrets"])
                assert loaded_secrets["XAI_API_KEY"] is True
                assert isinstance(loaded_secrets["XAI_API_KEY"], bool)
                assert secret not in loaded.text
        finally:
            if previous is None:
                _ = os.environ.pop("XAI_API_KEY", None)
            else:
                os.environ["XAI_API_KEY"] = previous

    def test_post_returns_saved_stt_settings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, state, _, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={"stt": {"backend": "deepgram"}},
            )
            assert resp.status_code == 200
            settings = self._ok_settings(resp)
            stt = as_json_object(settings["stt"])
            assert stt["backend"] == "deepgram"
            # Existing keys in the section should be preserved (merged).
            assert stt["sample_rate"] == 16000

            agents = as_json_object(settings["agents"])
            reply = as_json_object(settings["reply"])
            assert agents["info_enabled"] is True
            assert "reply_enabled" not in agents
            assert reply["enabled"] is True
            assert "reply_agents" not in settings

            secrets = as_json_object(settings["secrets"])
            # Keys should always be booleans (never leak actual values).
            for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPGRAM_API_KEY"):
                assert isinstance(secrets.get(key), bool), f"{key} should be a bool"

            assert settings["data_dir"] == str(state.config.user_data_dir)
            assert settings["context_dir"] == str(state.config.context_dir)

    def test_acp_argv_round_trips_and_updates_route_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, state, store, event_bus, events = _make_client(
                Path(td),
                config_text=(
                    "[ai]\nschema_version = 2\n\n"
                    '[ai.assignments]\nreply = "codex"\n\n'
                    '[ai.routes.codex]\nruntime = "codex-app-server"\n\n'
                    '[ai.routes.acp]\nruntime = "acp"\ncommand = ["old-agent"]\n\n'
                    '[ai.routes.acp.env]\nKEEP = "yes"\n'
                ),
            )
            from app.services.config_loader import ConfigLoader

            async def reload_runtime_config(event: ConfigChanged) -> None:
                _ = event
                state.config = ConfigLoader.from_settings_store(store)

            event_bus.subscribe(ConfigChanged, reload_runtime_config)
            command = ["python", "/opt/meeting supporter/acp_agent.py", "--stdio"]

            saved = client.post("/api/settings", json={"acp": {"command": command}})

            assert saved.status_code == 200
            saved_acp = as_json_object(self._ok_settings(saved)["acp"])
            assert saved_acp == {
                "command": command,
                "runtime": "acp",
                "capabilities": ["reply"],
            }
            loaded_acp = as_json_object(client.get("/api/settings").json_object()["acp"])
            assert loaded_acp["command"] == command
            catalog = client.get("/api/ai/routes").json_object()
            acp_route = next(route for route in as_object_array(catalog["routes"]) if route["id"] == "acp")
            assert acp_route["readiness"] == "ready"
            assert acp_route["selectable"] is True
            stored_text = store.config_path.read_text(encoding="utf-8")
            assert '[ai.routes.acp.env]\nKEEP = "yes"' in stored_text
            assert "[ai.routes.codex]" in stored_text
            assert 'reply = "codex"' in stored_text
            assert "ConfigChanged" in events

    def test_post_returns_settings_with_reply_and_agent_patch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={
                    "reply": {"enabled": False},
                    "agents": {"info_enabled": False},
                    "stt": {"backend": "remote"},
                },
            )
            assert resp.status_code == 200
            settings = self._ok_settings(resp)
            agents = as_json_object(settings["agents"])
            reply = as_json_object(settings["reply"])
            assert agents["info_enabled"] is False
            assert "reply_enabled" not in agents
            assert reply["enabled"] is False
            stt = as_json_object(settings["stt"])
            assert stt["backend"] == "remote"

    def test_post_reply_styles_patch_reflected_in_response(self) -> None:
        """POST reply.styles patch must return the updated enabled states."""
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={
                    "reply": {
                        "enabled": False,
                        "styles": [
                            {"id": "standard", "enabled": False},
                        ],
                    }
                },
            )
            assert resp.status_code == 200
            settings = self._ok_settings(resp)
            reply = as_json_object(settings["reply"])
            styles = as_object_array(reply["styles"])
            assert reply["enabled"] is False
            assert [style["id"] for style in styles] == ["standard"]
            assert styles[0]["enabled"] is False
            assert "reply_agents" not in settings

    def test_post_reply_settings_with_agents_together(self) -> None:
        """Combined agents + reply patch returns merged values."""
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={
                    "agents": {"info_enabled": False},
                    "reply": {
                        "enabled": True,
                        "styles": [
                            {"id": "standard", "enabled": True},
                        ],
                    },
                },
            )
            assert resp.status_code == 200
            settings = self._ok_settings(resp)
            agents = as_json_object(settings["agents"])
            reply = as_json_object(settings["reply"])
            styles = as_object_array(reply["styles"])
            assert agents["info_enabled"] is False
            assert "reply_enabled" not in agents
            assert reply["enabled"] is True
            assert [style["id"] for style in styles] == ["standard"]
            assert styles[0]["enabled"] is True

    def test_post_secrets_status_only_no_value_leak(self) -> None:
        """POST response must not leak secret values, only boolean status."""
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={"secrets": {"DEEPGRAM_API_KEY": "my-secret-value"}},
            )
            assert resp.status_code == 200
            settings = self._ok_settings(resp)
            secrets = as_json_object(settings["secrets"])
            assert secrets.get("DEEPGRAM_API_KEY") is True
            # No raw value should appear anywhere in the response
            raw = resp.text
            assert "my-secret-value" not in str(raw)

    def test_post_usage_budget_reflected_in_response_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={"usage_budget": {"meeting_limit_jpy": 120.0, "monthly_limit_jpy": 2500.0}},
            )
            assert resp.status_code == 200
            settings = self._ok_settings(resp)
            usage = as_json_object(settings["usage"])
            budget = as_json_object(usage["budget"])
            assert budget["meeting_limit_jpy"] == 120.0
            assert budget["monthly_limit_jpy"] == 2500.0

            cfg = store.load_config()
            usage_budget = cfg["usage_budget"]
            assert isinstance(usage_budget, dict)
            assert usage_budget["meeting_limit_jpy"] == 120.0
            assert usage_budget["monthly_limit_jpy"] == 2500.0

    def test_recording_retention_policy_round_trips_through_saved_settings(self) -> None:
        """A saved cleanup policy is returned by both the save response and a later settings read."""
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            policy = {"cutoff_date": "2025-02-01", "max_total_bytes": 262_144}

            saved = client.post("/api/settings", json={"recording_retention": policy})

            assert saved.status_code == 200
            saved_settings = self._ok_settings(saved)
            assert as_json_object(saved_settings["recording_retention"]) == policy

            loaded = client.get("/api/settings")

            assert loaded.status_code == 200
            assert as_json_object(loaded.json_object()["recording_retention"]) == policy


class TestPostSettingsPydanticValidation:
    """Tests for the Pydantic model-based request validation."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Remove all known secret env vars before each test to prevent leakage."""
        for key in SECRET_KEYS:
            monkeypatch.delenv(key, raising=False)

    def test_rejects_unknown_fields(self) -> None:
        """extra='forbid' rejects undeclared fields with 422."""
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            resp = client.post("/api/settings", json={"unknown_field": "value"})
            assert resp.status_code == 422

    def test_rejects_unknown_nested_under_agents(self) -> None:
        """extra='forbid' on AgentSettingsPayload rejects reply settings under agents."""
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={
                    "agents": {"reply_enabled": True},
                },
            )
            assert resp.status_code == 422

    def test_rejects_bad_reply_style_entry(self) -> None:
        """ReplyStyleEnabledPatch requires id (str) and enabled (bool)."""
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={"reply": {"styles": [{"id": 123, "enabled": "not_bool"}]}},
            )
            assert resp.status_code == 422

    def test_rejects_blank_acp_argv_argument(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))

            response = client.post(
                "/api/settings",
                json={"acp": {"command": ["python", "  "]}},
            )

            assert response.status_code == 422

    def test_rejects_legacy_llm_payload(self) -> None:
        """AI selection moved to the routes API; a model-string patch must not be accepted silently."""
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            response = client.post("/api/settings", json={"llm": {"model": "gpt-4o"}})

            assert response.status_code == 422


class TestSettingsSecretDeletion:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in SECRET_KEYS:
            monkeypatch.delenv(key, raising=False)

    def test_delete_secrets_removes_persistence_environment_and_status(self) -> None:
        secret = "delete-me-without-leaking"
        with tempfile.TemporaryDirectory() as td:
            client, state, _, _, events = _make_client(Path(td))
            state.secret_store.set_secrets({"DEEPGRAM_API_KEY": secret})

            response = client.post("/api/settings", json={"delete_secrets": ["DEEPGRAM_API_KEY"]})

            assert response.status_code == 200
            settings = as_json_object(response.json_object()["settings"])
            secrets = as_json_object(settings["secrets"])
            assert secrets["DEEPGRAM_API_KEY"] is False
            assert state.secret_store.get("DEEPGRAM_API_KEY") is None
            assert os.getenv("DEEPGRAM_API_KEY") is None
            assert "ConfigChanged" in events
            assert secret not in response.text

    def test_delete_secrets_rejects_unknown_secret_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))

            response = client.post("/api/settings", json={"delete_secrets": ["NOT_A_SECRET_KEY"]})

            assert response.status_code == 422


class TestConnectionSettingsApi:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in SECRET_KEYS:
            monkeypatch.delenv(key, raising=False)

    @pytest.mark.parametrize(
        ("provider", "secret_key", "url", "authorization"),
        [
            ("openai", "OPENAI_API_KEY", "https://api.openai.com/v1/models", "Bearer draft-secret"),
            ("deepgram", "DEEPGRAM_API_KEY", "https://api.deepgram.com/v1/projects", "Token draft-secret"),
            ("xai", "XAI_API_KEY", "https://api.x.ai/v1/models", "Bearer draft-secret"),
            (
                "gemini",
                "GEMINI_API_KEY",
                "https://generativelanguage.googleapis.com/v1beta/models",
                "draft-secret",
            ),
            ("anthropic", "ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/models", "draft-secret"),
        ],
    )
    def test_draft_connection_uses_provider_endpoint_and_auth_header_without_persisting(
        self,
        provider: str,
        secret_key: str,
        url: str,
        authorization: str,
    ) -> None:
        urlopen = _ConnectionUrlopen()
        with tempfile.TemporaryDirectory() as td:
            client, state, _, _, _ = _make_client(Path(td))
            with patch("app.api.settings.urllib.request.urlopen", side_effect=urlopen):
                response = client.post(
                    "/api/settings/connections/test",
                    json={"provider": provider, "api_key": "draft-secret"},
                )

            assert response.status_code == 200
            result = response.json_object()
            assert result == {"ok": True, "status": "verified", "message": "接続を確認しました。"}
            assert state.secret_store.status(secret_key) is False
            assert "draft-secret" not in response.text
            assert len(urlopen.requests) == 1
            request = urlopen.requests[0]
            assert request.full_url == url
            headers = request.header_items()
            if provider in {"openai", "deepgram", "xai"}:
                assert ("Authorization", authorization) in headers
            elif provider == "gemini":
                assert ("X-goog-api-key", authorization) in headers
            else:
                assert ("X-api-key", authorization) in headers
                assert ("Anthropic-version", "2023-06-01") in headers

    def test_connection_uses_stored_key_when_draft_is_omitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        urlopen = _ConnectionUrlopen()
        with tempfile.TemporaryDirectory() as td:
            client, state, _, _, _ = _make_client(Path(td))
            state.secret_store.set_secrets({"OPENAI_API_KEY": "stored-secret"})
            with patch("app.api.settings.urllib.request.urlopen", side_effect=urlopen):
                response = client.post("/api/settings/connections/test", json={"provider": "openai"})

            assert response.status_code == 200
            assert response.json_object()["status"] == "verified"
            assert ("Authorization", "Bearer stored-secret") in urlopen.requests[0].header_items()
            assert "stored-secret" not in response.text
            monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def test_connection_maps_unauthorized_response_to_invalid_without_leaking_draft(self) -> None:
        secret = "unauthorized-draft-secret"
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))
            error = urllib.error.HTTPError(
                "https://api.openai.com/v1/models",
                401,
                "Unauthorized",
                hdrs=Message(),
                fp=None,
            )
            with patch("app.api.settings.urllib.request.urlopen", side_effect=error):
                response = client.post(
                    "/api/settings/connections/test",
                    json={"provider": "openai", "api_key": secret},
                )

            assert response.status_code == 200
            assert response.json_object()["status"] == "invalid"
            assert secret not in response.text

    def test_connection_maps_timeout_to_unavailable_without_leaking_stored_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "stored-secret-that-must-not-leak"
        with tempfile.TemporaryDirectory() as td:
            client, state, _, _, _ = _make_client(Path(td))
            state.secret_store.set_secrets({"ANTHROPIC_API_KEY": secret})
            with patch(
                "app.api.settings.urllib.request.urlopen",
                side_effect=urllib.error.URLError(TimeoutError()),
            ):
                response = client.post("/api/settings/connections/test", json={"provider": "anthropic"})

            assert response.status_code == 200
            assert response.json_object()["status"] == "unavailable"
            assert secret not in response.text
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def test_connection_endpoint_is_typed_in_openapi(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))

            schema = client.get("/openapi.json").json_object()

            operation = as_json_object(as_json_object(schema["paths"])["/api/settings/connections/test"])
            request_body = as_json_object(operation["post"])["requestBody"]
            response = as_json_object(as_json_object(operation["post"])["responses"])["200"]
            assert "ConnectionTestRequest" in str(request_body)
            assert "ConnectionTestResponse" in str(response)
