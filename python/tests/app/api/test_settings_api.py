"""Tests for app.api.settings — GET/POST /api/settings."""

import asyncio
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from threading import Event, Thread
from typing import override
from unittest.mock import patch

import pytest
from fastapi import FastAPI

from app.api.settings import create_router
from app.core.config import SECRET_KEYS
from app.core.event_bus import EventBus
from app.core.events import ConfigChanged
from app.core.protocols import SecretStore
from app.core.state import AppState
from app.meetings.models import MeetingSession
from app.services.secret_store import CredentialSecretStore, FileSecretStore
from app.services.settings_store import SettingsStore
from app.services.usage_logger import UsageLogger, UsageRecord
from tests.helpers.api_client import JsonObject, TypedResponse, TypedTestClient, as_json_object, as_object_array


class _MemoryKeyring:
    def __init__(self, initial: dict[str, str], *, fail_reads: bool = False) -> None:
        self.passwords: dict[str, str] = dict(initial)
        self.fail_reads: bool = fail_reads
        self.set_calls: list[str] = []
        self.delete_calls: list[str] = []

    def get_password(self, service_name: str, username: str) -> str | None:
        _ = service_name
        if self.fail_reads:
            raise RuntimeError("injected credential read failure")
        return self.passwords.get(username)

    def set_password(self, service_name: str, username: str, password: str) -> None:
        _ = service_name
        self.set_calls.append(username)
        self.passwords[username] = password

    def delete_password(self, service_name: str, username: str) -> None:
        _ = service_name
        self.delete_calls.append(username)
        _ = self.passwords.pop(username, None)


class _NonTransactionalSecretStore(SecretStore):
    """Minimal broad secret-store fake without settings compensation APIs."""

    @override
    def get(self, key: str) -> str | None:
        _ = key
        return None

    @override
    def set_secrets(self, updates: dict[str, str]) -> None:
        _ = updates

    @override
    def delete(self, key: str) -> None:
        _ = key

    @override
    def status(self, key: str) -> bool:
        _ = key
        return False

    @override
    def status_all(self) -> dict[str, bool]:
        return {}

    @override
    def apply_secrets_to_env(self, keys: Iterable[str] | None = None) -> None:
        _ = keys


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
    configure_app: Callable[[FastAPI, AppState], None] | None = None,
    secret_store: SecretStore | None = None,
) -> tuple[TypedTestClient, AppState, SettingsStore, EventBus, list[str]]:
    """Build a TypedTestClient with a fresh settings router."""
    config_path = tmp_path / "config.toml"
    default_path = tmp_path / "default.toml"
    _ = default_path.write_text(
        "".join(
            (
                '[ai]\nschema_version = 2\n\n[stt]\nbackend = "whisper"\n',
                'vad_engine = "silero"\n\n[audio]\nsample_rate = 16000\n',
            )
        ),
        encoding="utf-8",
    )
    store = SettingsStore(config_path=config_path, default_config_path=default_path)
    if config_text is not None:
        _ = store.config_path.write_text(config_text, encoding="utf-8")
    if secret_store is None:
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
    if configure_app is not None:
        configure_app(app, state)
    router = create_router(state=state, store=store, event_bus=event_bus)
    app.include_router(router)

    return TypedTestClient(app), state, store, event_bus, events


class TestGetSettings:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Remove all known secret env vars before each test to prevent leakage."""
        for key in SECRET_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv("PROVIDER_LMSTUDIO_API_KEY", raising=False)

    def test_router_rejects_secret_store_without_transaction_support(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with pytest.raises(TypeError, match="transactional secret store"):
                _ = _make_client(Path(td), secret_store=_NonTransactionalSecretStore())

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

            records_calls = 0
            original_records = UsageLogger.records

            def counting_records(logger: UsageLogger) -> list[UsageRecord]:
                nonlocal records_calls
                records_calls += 1
                return original_records(logger)

            with patch.object(UsageLogger, "records", counting_records):
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
            assert records_calls == 1


class TestPostSettings:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Remove all known secret env vars before each test to prevent leakage."""
        for key in SECRET_KEYS:
            monkeypatch.delenv(key, raising=False)

    def test_rejects_audio_changes_during_active_meeting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, state, store, _, events = _make_client(Path(td))
            state.current_session = MeetingSession(id="meeting-active", started_at=datetime.now(UTC))

            resp = client.post("/api/settings", json={"stt": {"backend": "deepgram"}})

            assert resp.status_code == 409
            detail = as_json_object(resp.json_object()["detail"])
            assert detail["code"] == "AUDIO_SETTINGS_LOCKED"
            assert not store.config_path.exists()
            assert events == []

    def test_allows_non_audio_changes_with_unchanged_stt_during_active_meeting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, state, store, _, _ = _make_client(Path(td))
            state.current_session = MeetingSession(id="meeting-active", started_at=datetime.now(UTC))

            resp = client.post(
                "/api/settings",
                json={
                    "stt": {"backend": "whisper"},
                    "audio": {"sample_rate": 16000},
                    "agents": {"info_enabled": False},
                },
            )

            assert resp.status_code == 200
            cfg = store.load_config()
            assert cfg["agents"] == {"info_enabled": False}

    def test_full_get_payload_round_trips_while_active(self) -> None:
        config_text = """
[ai]
schema_version = 2

[stt]
backend = "whisper"
whisper_model = "large-v3-turbo"
deepgram_model = "nova-3"
openai_model = "gpt-4o-transcribe"
vosk_model_path = "vosk-model-small-ja-0.22"
language = "ja"
vad_engine = "silero"
vad_sensitivity = 0.4
silence_duration = 0.4
vad_aggressiveness = 2
device = "auto"
min_voiced_ms = 240
min_voiced_ratio = 0.35
min_rms_dbfs = -45.0
decode_no_speech_threshold = 1.0
decode_log_prob_threshold = -10.0
decode_compression_ratio_threshold = 10.0
hard_min_voiced_ms = 120
hard_no_speech_threshold = 0.85
hard_logprob_threshold = -2.0
hard_compression_ratio_threshold = 3.5
soft_min_voiced_ms = 240
soft_min_voiced_ratio = 0.35
soft_min_rms_dbfs = -45.0
soft_no_speech_threshold = 0.6
soft_logprob_threshold = -1.0
soft_compression_ratio_threshold = 2.4
drop_score_threshold = 0.65
temperature = 0.0
suspicious_phrases = ["synthetic phrase", "another phrase"]

[audio]
sample_rate = 16000
max_session_seconds = 55
"""
        with tempfile.TemporaryDirectory() as td:
            client, state, _, _, events = _make_client(Path(td), config_text=config_text)
            state.current_session = MeetingSession(id="meeting-active", started_at=datetime.now(UTC))
            before = client.get("/api/settings").json_object()
            stt = as_json_object(before["stt"])
            audio = as_json_object(before["audio"])

            saved = client.post("/api/settings", json={"stt": stt, "audio": audio})

            assert saved.status_code == 200
            saved_settings = as_json_object(saved.json_object()["settings"])
            assert as_json_object(saved_settings["stt"]) == stt
            assert as_json_object(saved_settings["audio"]) == audio
            assert events == ["ConfigChanged"]

    def test_legacy_stt_get_post_round_trip_migrates_to_canonical_keys(self) -> None:
        config_text = """
[stt]
backend = "whisper"
no_speech_threshold = 0.42
log_prob_threshold = -0.75
compression_ratio_threshold = 1.9
hallucination_phrase_blocklist = ["legacy phrase"]
private_backend_option = "not-part-of-settings-api"
"""
        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, events = _make_client(Path(td), config_text=config_text)

            before = client.get("/api/settings")

            assert before.status_code == 200
            stt = as_json_object(before.json_object()["stt"])
            assert stt == {
                "backend": "whisper",
                "soft_no_speech_threshold": 0.42,
                "soft_logprob_threshold": -0.75,
                "soft_compression_ratio_threshold": 1.9,
                "suspicious_phrases": ["legacy phrase"],
            }

            saved = client.post("/api/settings", json={"stt": stt})

            assert saved.status_code == 200
            assert as_json_object(saved.json_object()["settings"])["stt"] == stt
            persisted_stt = store.load_config()["stt"]
            assert isinstance(persisted_stt, dict)
            for legacy_key in (
                "no_speech_threshold",
                "log_prob_threshold",
                "compression_ratio_threshold",
                "hallucination_phrase_blocklist",
            ):
                assert legacy_key not in persisted_stt
            assert persisted_stt["soft_no_speech_threshold"] == 0.42
            assert persisted_stt["soft_logprob_threshold"] == -0.75
            assert persisted_stt["soft_compression_ratio_threshold"] == 1.9
            assert persisted_stt["suspicious_phrases"] == ["legacy phrase"]
            assert events == ["ConfigChanged"]

    def test_sparse_stt_save_preserves_and_migrates_legacy_values(self) -> None:
        config_text = """
[stt]
backend = "whisper"
no_speech_threshold = 0.23
hallucination_phrase_blocklist = ["preserve me"]
"""
        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, _ = _make_client(Path(td), config_text=config_text)

            saved = client.post("/api/settings", json={"stt": {"backend": "deepgram"}})

            assert saved.status_code == 200
            persisted_stt = store.load_config()["stt"]
            assert isinstance(persisted_stt, dict)
            assert persisted_stt["backend"] == "deepgram"
            assert persisted_stt["soft_no_speech_threshold"] == 0.23
            assert persisted_stt["suspicious_phrases"] == ["preserve me"]
            assert "no_speech_threshold" not in persisted_stt
            assert "hallucination_phrase_blocklist" not in persisted_stt

    def test_full_form_defaults_match_sparse_config_while_active(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, state, _, _, _ = _make_client(Path(td))
            state.current_session = MeetingSession(id="meeting-active", started_at=datetime.now(UTC))

            response = client.post(
                "/api/settings",
                json={
                    "stt": {
                        "backend": "whisper",
                        "whisper_model": "large-v3-turbo",
                        "deepgram_model": "nova-3",
                        "openai_model": "gpt-4o-transcribe",
                        "vosk_model_path": "vosk-model-small-ja-0.22",
                        "language": "ja",
                        "vad_engine": "silero",
                        "vad_sensitivity": 0.4,
                        "vad_aggressiveness": 2,
                        "silence_duration": 0.8,
                    },
                    "audio": {"sample_rate": 16000, "max_session_seconds": 55},
                },
            )

            assert response.status_code == 200

    def test_meeting_start_and_settings_save_are_serialized_by_audio_mutex(self) -> None:
        meeting_start_entered = Event()
        allow_meeting_start = Event()

        def configure_app(app: FastAPI, state: AppState) -> None:
            async def start_meeting() -> dict[str, bool]:
                async with state.audio_lifecycle_lock:
                    meeting_start_entered.set()
                    _ = await asyncio.to_thread(allow_meeting_start.wait)
                    state.current_session = MeetingSession(id="serialized-meeting", started_at=datetime.now(UTC))
                return {"ok": True}

            app.add_api_route("/test/meeting/start", start_meeting, methods=["POST"])

        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, events = _make_client(Path(td), configure_app=configure_app)
            start_responses: list[TypedResponse] = []
            save_responses: list[TypedResponse] = []

            def call_start() -> None:
                start_responses.append(client.post("/test/meeting/start"))

            def call_save() -> None:
                save_responses.append(client.post("/api/settings", json={"stt": {"backend": "deepgram"}}))

            with client:
                start_thread = Thread(target=call_start)
                start_thread.start()
                assert meeting_start_entered.wait(timeout=2)
                save_thread = Thread(target=call_save)
                save_thread.start()
                allow_meeting_start.set()
                start_thread.join(timeout=2)
                save_thread.join(timeout=2)

            assert [response.status_code for response in start_responses] == [200]
            assert [response.status_code for response in save_responses] == [409]
            assert not store.config_path.exists()
            assert events == []

    def test_audio_save_establishes_reload_before_queued_meeting_start(self) -> None:
        handler_entered = Event()
        allow_handler = Event()
        meeting_start_attempted = Event()
        meeting_start_entered = Event()
        reload_pending = Event()
        handler_lock_flags: list[bool] = []
        handler_saw_lock_held: list[bool] = []
        start_saw_reload_pending: list[bool] = []

        def configure_app(app: FastAPI, state: AppState) -> None:
            async def start_meeting() -> dict[str, bool]:
                async def acquire_audio_lifecycle() -> None:
                    async with state.audio_lifecycle_lock:
                        meeting_start_entered.set()
                        start_saw_reload_pending.append(reload_pending.is_set())
                        state.current_session = MeetingSession(id="queued-meeting", started_at=datetime.now(UTC))

                acquire_task = asyncio.create_task(acquire_audio_lifecycle())
                await asyncio.sleep(0)
                meeting_start_attempted.set()
                await acquire_task
                return {"ok": True}

            app.add_api_route("/test/meeting/start", start_meeting, methods=["POST"])

        with tempfile.TemporaryDirectory() as td:
            client, state, _, event_bus, events = _make_client(Path(td), configure_app=configure_app)

            async def establish_reload_or_pending(event: ConfigChanged) -> None:
                handler_lock_flags.append(event.audio_lifecycle_lock_held)
                handler_entered.set()
                _ = await asyncio.to_thread(allow_handler.wait)
                if event.audio_lifecycle_lock_held:
                    handler_saw_lock_held.append(state.audio_lifecycle_lock.locked())
                    reload_pending.set()
                    return
                async with state.audio_lifecycle_lock:
                    reload_pending.set()

            event_bus.subscribe(ConfigChanged, establish_reload_or_pending)
            save_responses: list[TypedResponse] = []
            start_responses: list[TypedResponse] = []

            def call_save() -> None:
                save_responses.append(client.post("/api/settings", json={"stt": {"backend": "deepgram"}}))

            def call_start() -> None:
                start_responses.append(client.post("/test/meeting/start"))

            with client:
                save_thread = Thread(target=call_save)
                save_thread.start()
                assert handler_entered.wait(timeout=2)

                start_thread = Thread(target=call_start)
                start_thread.start()
                assert meeting_start_attempted.wait(timeout=2)
                start_entered_before_handler_finished = meeting_start_entered.is_set()

                allow_handler.set()
                save_thread.join(timeout=2)
                start_thread.join(timeout=2)

            assert not save_thread.is_alive()
            assert not start_thread.is_alive()
            assert start_entered_before_handler_finished is False
            assert [response.status_code for response in save_responses] == [200]
            assert [response.status_code for response in start_responses] == [200]
            assert handler_lock_flags == [True]
            assert handler_saw_lock_held == [True]
            assert start_saw_reload_pending == [True]
            assert events == ["ConfigChanged"]

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

    def test_batches_secret_and_config_changes_into_one_event(self) -> None:
        try:
            with tempfile.TemporaryDirectory() as td:
                client, state, store, _, events = _make_client(Path(td))

                response = client.post(
                    "/api/settings",
                    json={
                        "secrets": {"GEMINI_API_KEY": "synthetic-key"},
                        "agents": {"info_enabled": False},
                    },
                )

                assert response.status_code == 200
                assert state.secret_store.get("GEMINI_API_KEY") == "synthetic-key"
                assert store.load_config()["agents"] == {"info_enabled": False}
                assert events == ["ConfigChanged"]
        finally:
            _ = os.environ.pop("GEMINI_API_KEY", None)

    def test_secret_update_with_null_only_context_skips_config_write(self) -> None:
        config_text = """
[context]
dir_override = "existing-context"
"""
        try:
            with tempfile.TemporaryDirectory() as td:
                client, state, store, _, events = _make_client(Path(td), config_text=config_text)
                original_config = store.config_path.read_bytes()

                with patch.object(
                    store,
                    "write_sectioned_toml",
                    side_effect=OSError("config write must not run"),
                ) as write_config:
                    response = client.post(
                        "/api/settings",
                        json={
                            "secrets": {"GEMINI_API_KEY": "synthetic-key"},
                            "context": {"dir_override": None},
                        },
                    )

                assert response.status_code == 200
                assert state.secret_store.get("GEMINI_API_KEY") == "synthetic-key"
                write_config.assert_not_called()
                assert store.config_path.read_bytes() == original_config
                assert events == ["ConfigChanged"]
        finally:
            _ = os.environ.pop("GEMINI_API_KEY", None)

    def test_keyring_read_failure_aborts_before_any_settings_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            secret_path = root / "secrets.toml"
            keyring = _MemoryKeyring(
                {"DEEPGRAM_API_KEY": "synthetic-original"},
                fail_reads=True,
            )
            credential_store = CredentialSecretStore(
                fallback=FileSecretStore(secret_path),
                keyring_client=keyring,
            )
            client, _, store, _, events = _make_client(root, secret_store=credential_store)

            with pytest.raises(RuntimeError, match="credential.*read"):
                _ = client.post(
                    "/api/settings",
                    json={
                        "secrets": {"DEEPGRAM_API_KEY": "synthetic-replacement"},
                        "agents": {"info_enabled": False},
                    },
                )

            assert keyring.passwords == {"DEEPGRAM_API_KEY": "synthetic-original"}
            assert keyring.set_calls == []
            assert keyring.delete_calls == []
            assert os.environ.get("DEEPGRAM_API_KEY") is None
            assert not secret_path.exists()
            assert not store.config_path.exists()
            assert events == []

    def test_config_failure_after_deleting_absent_secret_removes_new_fallback_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            secret_path = root / "secrets.toml"
            keyring = _MemoryKeyring({})
            credential_store = CredentialSecretStore(
                fallback=FileSecretStore(secret_path),
                keyring_client=keyring,
            )
            client, _, store, _, events = _make_client(root, secret_store=credential_store)

            with (
                patch.object(store, "write_sectioned_toml", side_effect=OSError("injected config failure")),
                pytest.raises(OSError, match="injected config failure"),
            ):
                _ = client.post(
                    "/api/settings",
                    json={
                        "delete_secrets": ["ANTHROPIC_API_KEY"],
                        "agents": {"info_enabled": False},
                    },
                )

            assert keyring.passwords == {}
            assert keyring.set_calls == []
            assert keyring.delete_calls == ["ANTHROPIC_API_KEY"]
            assert os.environ.get("ANTHROPIC_API_KEY") is None
            assert not secret_path.exists()
            assert events == []

    def test_config_and_secret_rollback_failure_exposes_rollback_and_publishes_no_event(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, state, store, _, events = _make_client(Path(td))

            with (
                patch.object(store, "write_sectioned_toml", side_effect=OSError("injected config failure")),
                patch.object(
                    state.secret_store,
                    "restore",
                    side_effect=RuntimeError("injected secret rollback failure"),
                ),
                pytest.raises(RuntimeError, match="rollback"),
            ):
                _ = client.post(
                    "/api/settings",
                    json={
                        "secrets": {"GEMINI_API_KEY": "synthetic-replacement"},
                        "agents": {"info_enabled": False},
                    },
                )

            assert events == []

    def test_config_write_failure_restores_exact_file_bytes_and_state_without_event(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            secret_path = root / "secrets.toml"
            original_file = (
                b"# synthetic formatting preserved across compensation\n"
                b'OPENAI_API_KEY = "synthetic-unrelated-original"\n'
                b"\n"
                b'DEEPGRAM_API_KEY    = "synthetic-deepgram-original"  # harmless comment\n'
            )
            _ = secret_path.write_bytes(original_file)
            client, state, store, _, events = _make_client(root)
            os.environ["OPENAI_API_KEY"] = "synthetic-environment-override"

            with (
                patch.object(store, "write_sectioned_toml", side_effect=OSError("injected write failure")),
                pytest.raises(OSError, match="injected write failure"),
            ):
                _ = client.post(
                    "/api/settings",
                    json={
                        "secrets": {"OPENAI_API_KEY": "synthetic-openai-replacement-before-delete"},
                        "delete_secrets": ["OPENAI_API_KEY"],
                        "agents": {"info_enabled": False},
                    },
                )

            assert state.secret_store.get("OPENAI_API_KEY") == "synthetic-environment-override"
            assert os.environ.get("OPENAI_API_KEY") == "synthetic-environment-override"
            assert secret_path.read_bytes() == original_file
            assert state.secret_store.get("DEEPGRAM_API_KEY") == "synthetic-deepgram-original"
            assert events == []

    def test_config_load_failure_restores_deleted_secret_without_event(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, state, store, _, events = _make_client(Path(td))
            state.secret_store.set_secrets(
                {
                    "DEEPGRAM_API_KEY": "original-deepgram-key",
                    "OPENAI_API_KEY": "unrelated-key",
                }
            )

            with (
                patch.object(store, "load_config", side_effect=OSError("injected load failure")),
                pytest.raises(OSError, match="injected load failure"),
            ):
                _ = client.post(
                    "/api/settings",
                    json={
                        "secrets": {"DEEPGRAM_API_KEY": "replacement-before-delete"},
                        "delete_secrets": ["DEEPGRAM_API_KEY"],
                        "agents": {"info_enabled": False},
                    },
                )

            assert state.secret_store.get("DEEPGRAM_API_KEY") == "original-deepgram-key"
            assert os.environ.get("DEEPGRAM_API_KEY") == "original-deepgram-key"
            assert state.secret_store.get("OPENAI_API_KEY") == "unrelated-key"
            assert events == []

    def test_config_failure_restores_exact_credential_fallback_bytes_and_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SECRET_STORE_BACKEND", raising=False)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            secret_path = root / "secrets.toml"
            original_file = (
                b"# synthetic fallback formatting preserved across compensation\n"
                b'OPENAI_API_KEY = "synthetic-unrelated-fallback"\n'
                b"\n"
                b'DEEPGRAM_API_KEY    = "synthetic-deepgram-fallback"  # harmless comment\n'
            )
            _ = secret_path.write_bytes(original_file)
            keyring = _MemoryKeyring(
                {
                    "OPENAI_API_KEY": "synthetic-openai-credential",
                    "DEEPGRAM_API_KEY": "synthetic-unrelated-credential",
                }
            )
            credential_store = CredentialSecretStore(
                fallback=FileSecretStore(secret_path),
                keyring_client=keyring,
            )
            client, _, store, _, events = _make_client(root, secret_store=credential_store)
            monkeypatch.setenv("OPENAI_API_KEY", "synthetic-environment-override")

            with (
                patch.object(store, "write_sectioned_toml", side_effect=OSError("injected write failure")),
                pytest.raises(OSError, match="injected write failure"),
            ):
                _ = client.post(
                    "/api/settings",
                    json={
                        "secrets": {"OPENAI_API_KEY": "synthetic-openai-replacement-before-delete"},
                        "delete_secrets": ["OPENAI_API_KEY"],
                        "agents": {"info_enabled": False},
                    },
                )

            assert keyring.passwords == {
                "OPENAI_API_KEY": "synthetic-openai-credential",
                "DEEPGRAM_API_KEY": "synthetic-unrelated-credential",
            }
            assert secret_path.read_bytes() == original_file
            assert os.environ.get("OPENAI_API_KEY") == "synthetic-environment-override"
            monkeypatch.delenv("OPENAI_API_KEY")
            assert credential_store.get("OPENAI_API_KEY") == "synthetic-openai-credential"
            assert events == []

    def test_updates_config_sections(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, _ = _make_client(Path(td))
            resp = client.post(
                "/api/settings",
                json={
                    "stt": {"backend": "deepgram", "vad_engine": "webrtc"},
                    "audio": {"sample_rate": 48000},
                },
            )
            assert resp.status_code == 200
            assert resp.json_object()["ok"] is True

            cfg = store.load_config()
            stt = cfg["stt"]
            assert isinstance(stt, dict)
            assert stt["backend"] == "deepgram"
            assert stt["vad_engine"] == "webrtc"
            audio = cfg["audio"]
            assert isinstance(audio, dict)
            assert audio["sample_rate"] == 48000

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
            assert stt["vad_engine"] == "silero"

    def test_nested_context_null_is_noop_without_config_write_or_event(self) -> None:
        config_text = """
[context]
dir_override = "existing-context"
"""
        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, events = _make_client(Path(td), config_text=config_text)
            original_config = store.config_path.read_bytes()

            with patch.object(store, "write_sectioned_toml") as write_config:
                response = client.post("/api/settings", json={"context": {"dir_override": None}})

            assert response.status_code == 200
            assert response.json_object()["ok"] is True
            write_config.assert_not_called()
            assert store.config_path.read_bytes() == original_config
            assert events == []

    def test_context_value_is_saved_and_reloaded(self) -> None:
        config_text = """
[context]
dir_override = "existing-context"
"""
        with tempfile.TemporaryDirectory() as td:
            client, state, store, event_bus, events = _make_client(Path(td), config_text=config_text)

            async def reload_runtime_config(event: ConfigChanged) -> None:
                _ = event
                state.config = state.config.reload()

            event_bus.subscribe(ConfigChanged, reload_runtime_config)

            response = client.post(
                "/api/settings",
                json={"context": {"dir_override": "updated-context"}},
            )

            assert response.status_code == 200
            assert store.load_config()["context"] == {"dir_override": "updated-context"}
            assert state.config.context_dir == Path("updated-context")
            assert events == ["ConfigChanged"]

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
            assert stt["vad_engine"] == "silero"

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

    @pytest.mark.parametrize(
        ("section", "field_name"),
        [
            ("stt", "vad_sensitivity"),
            ("stt", "silence_duration"),
            ("stt", "vad_aggressiveness"),
            ("stt", "min_voiced_ms"),
            ("stt", "min_voiced_ratio"),
            ("stt", "min_rms_dbfs"),
            ("stt", "decode_no_speech_threshold"),
            ("stt", "decode_log_prob_threshold"),
            ("stt", "decode_compression_ratio_threshold"),
            ("stt", "hard_min_voiced_ms"),
            ("stt", "hard_no_speech_threshold"),
            ("stt", "hard_logprob_threshold"),
            ("stt", "hard_compression_ratio_threshold"),
            ("stt", "soft_min_voiced_ms"),
            ("stt", "soft_min_voiced_ratio"),
            ("stt", "soft_min_rms_dbfs"),
            ("stt", "soft_no_speech_threshold"),
            ("stt", "soft_logprob_threshold"),
            ("stt", "soft_compression_ratio_threshold"),
            ("stt", "drop_score_threshold"),
            ("stt", "temperature"),
            ("audio", "sample_rate"),
            ("audio", "max_session_seconds"),
        ],
    )
    @pytest.mark.parametrize("invalid_value", [True, "1"])
    def test_rejects_coerced_numeric_values_without_persistence_or_event(
        self,
        section: str,
        field_name: str,
        invalid_value: bool | str,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, state, store, _, events = _make_client(Path(td))

            response = client.post(
                "/api/settings",
                json={
                    section: {field_name: invalid_value},
                    "secrets": {"DEEPGRAM_API_KEY": "must-not-be-saved"},
                },
            )

            assert response.status_code == 422
            assert state.secret_store.get("DEEPGRAM_API_KEY") is None
            assert os.getenv("DEEPGRAM_API_KEY") is None
            assert not store.config_path.exists()
            assert events == []

    def test_rejects_unknown_stt_and_audio_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, events = _make_client(Path(td))

            stt_response = client.post("/api/settings", json={"stt": {"unknown_vad_option": True}})
            audio_response = client.post("/api/settings", json={"audio": {"channels": 2}})

            assert stt_response.status_code == 422
            assert audio_response.status_code == 422
            assert not store.config_path.exists()
            assert events == []

    def test_rejects_unknown_vad_engine_without_persistence_or_event(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, store, _, events = _make_client(Path(td))

            response = client.post("/api/settings", json={"stt": {"vad_engine": "unknown"}})

            assert response.status_code == 422
            assert not store.config_path.exists()
            assert events == []

    def test_rejects_silero_with_48khz_before_secret_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, state, store, _, events = _make_client(Path(td))

            response = client.post(
                "/api/settings",
                json={
                    "stt": {"vad_engine": "silero"},
                    "audio": {"sample_rate": 48000},
                    "secrets": {"DEEPGRAM_API_KEY": "must-not-be-saved"},
                },
            )

            assert response.status_code == 422
            detail = as_object_array(response.json_object()["detail"])
            assert detail == [
                {
                    "type": "value_error",
                    "loc": ["body", "audio"],
                    "msg": "Silero VADは16 kHz mono PCMを必要とします。",
                }
            ]
            assert state.secret_store.get("DEEPGRAM_API_KEY") is None
            assert os.getenv("DEEPGRAM_API_KEY") is None
            assert not store.config_path.exists()
            assert events == []

    def test_settings_conflict_response_is_declared_in_openapi(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _, _, _ = _make_client(Path(td))

            schema = client.get("/openapi.json").json_object()

            settings_path = as_json_object(as_json_object(schema["paths"])["/api/settings"])
            responses = as_json_object(as_json_object(settings_path["post"])["responses"])
            assert "SettingsConflictResponse" in str(responses["409"])

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
