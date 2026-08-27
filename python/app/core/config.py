"""Application configuration types and helpers."""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from math import isfinite
from typing import Literal, TypedDict, cast

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


type VadEngine = Literal["silero", "webrtc"]


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
    vad_engine: VadEngine = "silero"


# ── LLM / Audio typed configs ─────────────────────────────────────────────────


class UsageBudgetConfig(BaseModel):
    meeting_limit_jpy: float = 0.0
    monthly_limit_jpy: float = 0.0


class AudioConfig(TypedDict):
    sample_rate: int
    max_session_seconds: int
    chunk_size: int


@dataclass(frozen=True)
class EffectiveAudioSttConfig:
    """Normalized runtime values used to compare and validate settings patches."""

    stt: SttConfig
    audio_sample_rate: int
    audio_max_session_seconds: int
    audio_chunk_size: int


def _normalized_int(value: int, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    normalized = int(value)
    if normalized != value:
        raise ValueError(f"{field_name} must be an integer")
    return normalized


def _normalized_float(value: float, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number")
    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"{field_name} must be finite")
    return normalized


def normalize_audio_stt_config(
    stt_config: SttConfig,
    *,
    audio_sample_rate: int,
    audio_max_session_seconds: int,
) -> EffectiveAudioSttConfig:
    """Return one canonical effective audio/STT configuration.

    TOML arrays load as lists while the runtime STT configuration uses tuples,
    and TOML numeric values may be represented as either integers or floats.
    Canonicalizing both here keeps lifecycle comparisons independent of those
    representation details and centralizes the VAD/audio invariant.
    """

    sample_rate = _normalized_int(audio_sample_rate, "audio.sample_rate")
    max_session_seconds = _normalized_int(audio_max_session_seconds, "audio.max_session_seconds")
    if sample_rate <= 0:
        raise ValueError("audio.sample_rate must be positive")
    if max_session_seconds <= 0:
        raise ValueError("audio.max_session_seconds must be positive")
    if stt_config.vad_engine not in ("silero", "webrtc"):
        raise ValueError(f"未知のVADエンジン: {stt_config.vad_engine!r}")
    if stt_config.vad_engine == "silero" and sample_rate != 16_000:
        raise ValueError("Silero VADは16 kHz mono PCMを必要とします。")
    external_suspicious_phrases = cast(object, stt_config.suspicious_phrases)
    if not isinstance(external_suspicious_phrases, (list, tuple)):
        raise ValueError("stt.suspicious_phrases must be a list of strings")
    raw_suspicious_phrases = cast(Iterable[object], external_suspicious_phrases)
    if not all(isinstance(phrase, str) for phrase in raw_suspicious_phrases):
        raise ValueError("stt.suspicious_phrases must be a list of strings")
    suspicious_phrases = cast(Iterable[str], raw_suspicious_phrases)

    normalized_stt = replace(
        stt_config,
        vad_sensitivity=_normalized_float(stt_config.vad_sensitivity, "stt.vad_sensitivity"),
        silence_duration=_normalized_float(stt_config.silence_duration, "stt.silence_duration"),
        vad_aggressiveness=_normalized_int(stt_config.vad_aggressiveness, "stt.vad_aggressiveness"),
        sample_rate=sample_rate,
        chunk_size=max(1, int(sample_rate * 0.1)),
        min_voiced_ms=_normalized_int(stt_config.min_voiced_ms, "stt.min_voiced_ms"),
        min_voiced_ratio=_normalized_float(stt_config.min_voiced_ratio, "stt.min_voiced_ratio"),
        min_rms_dbfs=_normalized_float(stt_config.min_rms_dbfs, "stt.min_rms_dbfs"),
        decode_no_speech_threshold=_normalized_float(
            stt_config.decode_no_speech_threshold, "stt.decode_no_speech_threshold"
        ),
        decode_log_prob_threshold=_normalized_float(
            stt_config.decode_log_prob_threshold, "stt.decode_log_prob_threshold"
        ),
        decode_compression_ratio_threshold=_normalized_float(
            stt_config.decode_compression_ratio_threshold,
            "stt.decode_compression_ratio_threshold",
        ),
        hard_min_voiced_ms=_normalized_int(stt_config.hard_min_voiced_ms, "stt.hard_min_voiced_ms"),
        hard_no_speech_threshold=_normalized_float(stt_config.hard_no_speech_threshold, "stt.hard_no_speech_threshold"),
        hard_logprob_threshold=_normalized_float(stt_config.hard_logprob_threshold, "stt.hard_logprob_threshold"),
        hard_compression_ratio_threshold=_normalized_float(
            stt_config.hard_compression_ratio_threshold,
            "stt.hard_compression_ratio_threshold",
        ),
        soft_min_voiced_ms=_normalized_int(stt_config.soft_min_voiced_ms, "stt.soft_min_voiced_ms"),
        soft_min_voiced_ratio=_normalized_float(stt_config.soft_min_voiced_ratio, "stt.soft_min_voiced_ratio"),
        soft_min_rms_dbfs=_normalized_float(stt_config.soft_min_rms_dbfs, "stt.soft_min_rms_dbfs"),
        soft_no_speech_threshold=_normalized_float(stt_config.soft_no_speech_threshold, "stt.soft_no_speech_threshold"),
        soft_logprob_threshold=_normalized_float(stt_config.soft_logprob_threshold, "stt.soft_logprob_threshold"),
        soft_compression_ratio_threshold=_normalized_float(
            stt_config.soft_compression_ratio_threshold,
            "stt.soft_compression_ratio_threshold",
        ),
        drop_score_threshold=_normalized_float(stt_config.drop_score_threshold, "stt.drop_score_threshold"),
        temperature=_normalized_float(stt_config.temperature, "stt.temperature"),
        suspicious_phrases=tuple(suspicious_phrases),
    )
    return EffectiveAudioSttConfig(
        stt=normalized_stt,
        audio_sample_rate=sample_rate,
        audio_max_session_seconds=max_session_seconds,
        audio_chunk_size=normalized_stt.chunk_size,
    )


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
    "VadEngine",
    "EffectiveAudioSttConfig",
    "normalize_audio_stt_config",
    "AGENT_SETTING_KEYS",
    "SECRET_KEYS",
    "_build_agent_settings_message",
    "_read_agent_settings",
    "patch_agent_settings",
]
