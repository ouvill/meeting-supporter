"""Application configuration types and helpers."""

from dataclasses import dataclass
from typing import Literal, TypedDict

from pydantic import BaseModel

from app.core.messages import AgentSettingsMsg, ReplyAgentSettingsItem
from app.core.types import TomlTable, TomlValue

type ProviderKind = Literal[
    "google-gla",
    "google-vertex",
    "openai",
    "openai-chat",
    "openai-responses",
    "anthropic",
    "openai-compatible",
    "ollama",
]

type DataLocation = Literal["cloud", "external", "local", "unknown"]
type RouteRuntime = Literal["pydantic-ai", "codex-app-server", "acp", "managed"]
type RouteKind = Literal["managed", "subscription_app", "local", "byok"]
type RouteAvailability = Literal["available", "experimental", "planned", "unavailable"]
type RouteReadiness = Literal["ready", "setup_required", "unavailable", "error", "not_offered", "unknown"]
type RouteCapability = Literal["reply", "info", "minutes", "stream", "cancel"]
type BillingOwner = Literal["app", "external_subscription", "user", "none"]
type RouteAction = Literal[
    "none",
    "configure",
    "install",
    "login",
    "start",
    "sign_in",
    "subscribe",
    "manage_billing",
    "view_usage",
    "retry",
]
type RouteServiceTier = Literal["priority", "standard", "unknown"]


@dataclass(frozen=True)
class ProviderDefinition:
    """A model API provider; process runtimes are deliberately excluded."""

    id: str
    label: str
    kind: ProviderKind
    base_url: str | None
    key_ref: str | None
    models: list[str] | None
    data_location: DataLocation
    experimental: bool


@dataclass(frozen=True)
class RouteDefinition:
    """Runtime route configuration, separate from model provider metadata."""

    id: str
    runtime: RouteRuntime
    provider_id: str | None = None
    model: str | None = None
    command: list[str] | None = None
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class AiRouteAssignments:
    """Schema-v2 canonical route selection for each AI use case."""

    reply: str | None = None
    info: str | None = None
    minutes: str | None = None


# ── STT configuration ─────────────────────────────────────────────────────────


DEFAULT_SUSPICIOUS_PHRASES = (
    "ご視聴ありがとうございました",
    "ありがとうございました",
    "おやすみなさい",
)


@dataclass
class SttConfig:
    backend: str
    whisper_model: str
    deepgram_model: str
    language: str
    vad_sensitivity: float
    silence_duration: float
    vad_aggressiveness: int
    device: str
    remote_url: str
    remote_token: str
    sample_rate: int
    chunk_size: int
    min_voiced_ms: int = 240
    min_voiced_ratio: float = 0.35
    min_rms_dbfs: float = -45.0
    decode_no_speech_threshold: float = 1.0
    decode_log_prob_threshold: float = -10.0
    decode_compression_ratio_threshold: float = 10.0
    hard_min_voiced_ms: int = 120
    hard_no_speech_threshold: float = 0.85
    hard_logprob_threshold: float = -2.0
    hard_compression_ratio_threshold: float = 3.5
    soft_min_voiced_ms: int = 240
    soft_min_voiced_ratio: float = 0.35
    soft_min_rms_dbfs: float = -45.0
    soft_no_speech_threshold: float = 0.6
    soft_logprob_threshold: float = -1.0
    soft_compression_ratio_threshold: float = 2.4
    drop_score_threshold: float = 0.65
    temperature: float = 0.0
    suspicious_phrases: tuple[str, ...] = DEFAULT_SUSPICIOUS_PHRASES
    openai_model: str = "gpt-4o-transcribe"
    vosk_model_path: str = "vosk-model-small-ja-0.22"


# ── LLM / Audio typed configs ─────────────────────────────────────────────────


class UsageBudgetConfig(BaseModel):
    meeting_limit_jpy: float = 0.0
    monthly_limit_jpy: float = 0.0


class AudioConfig(TypedDict):
    sample_rate: int
    max_session_seconds: int
    chunk_size: int


# ── Secrets ───────────────────────────────────────────────────────────────────

type SecretKey = Literal[
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "DEEPGRAM_API_KEY",
    "XAI_API_KEY",
]

SECRET_KEYS: tuple[SecretKey, ...] = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "DEEPGRAM_API_KEY",
    "XAI_API_KEY",
)


# ── Agent settings ────────────────────────────────────────────────────────────

AgentSettingKey = Literal["reply_enabled", "reply_auto_generate", "info_enabled"]


class AgentSettings(TypedDict):
    reply_enabled: bool
    reply_auto_generate: bool
    info_enabled: bool


AGENT_SETTING_KEYS: tuple[AgentSettingKey, ...] = (
    "reply_enabled",
    "reply_auto_generate",
    "info_enabled",
)


def _read_agent_settings(cfg: TomlTable) -> AgentSettings:
    agents_raw = cfg.get("agents")
    agents_section: dict[str, TomlValue] = agents_raw if isinstance(agents_raw, dict) else {}
    reply_raw = cfg.get("reply")
    reply_section: dict[str, TomlValue] = reply_raw if isinstance(reply_raw, dict) else {}

    def _get_agent_bool(k: str, default: bool) -> bool:
        v = agents_section.get(k)
        return v if isinstance(v, bool) else default

    def _get_reply_bool(k: str, legacy_key: str, default: bool) -> bool:
        v = reply_section.get(k)
        if isinstance(v, bool):
            return v
        legacy = agents_section.get(legacy_key)
        return legacy if isinstance(legacy, bool) else default

    return AgentSettings(
        reply_enabled=_get_reply_bool("enabled", "reply_enabled", True),
        reply_auto_generate=_get_reply_bool("auto_generate", "reply_auto_generate", False),
        info_enabled=_get_agent_bool("info_enabled", True),
    )


def patch_agent_settings(
    base: AgentSettings,
    patch: dict[AgentSettingKey, bool],
) -> "AgentSettings | str":
    """Merge patch into base, returning merged AgentSettings or an error message."""
    return AgentSettings(
        reply_enabled=patch.get("reply_enabled", base["reply_enabled"]),
        reply_auto_generate=patch.get("reply_auto_generate", base["reply_auto_generate"]),
        info_enabled=patch.get("info_enabled", base["info_enabled"]),
    )


def _build_agent_settings_message(
    agent_settings: AgentSettings,
    reply_agents: list[ReplyAgentSettingsItem] | None = None,
) -> AgentSettingsMsg:
    return AgentSettingsMsg(
        reply_enabled=agent_settings["reply_enabled"],
        reply_auto_generate=agent_settings["reply_auto_generate"],
        reply_agents=[] if reply_agents is None else reply_agents,
        info_enabled=agent_settings["info_enabled"],
    )


__all__ = [
    "AiRouteAssignments",
    "AgentSettingKey",
    "AgentSettings",
    "AudioConfig",
    "BillingOwner",
    "DataLocation",
    "ProviderDefinition",
    "ProviderKind",
    "RouteAction",
    "RouteAvailability",
    "RouteCapability",
    "RouteDefinition",
    "RouteKind",
    "RouteReadiness",
    "RouteRuntime",
    "RouteServiceTier",
    "SecretKey",
    "SttConfig",
    "UsageBudgetConfig",
    "AGENT_SETTING_KEYS",
    "SECRET_KEYS",
    "_build_agent_settings_message",
    "_read_agent_settings",
    "patch_agent_settings",
]
