"""Request and response models for the settings API."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter, field_validator

from app.core.config import DataLocation, ProviderKind, SecretKey, UsageBudgetConfig
from app.core.types import JsonValue, TomlTable

_TOML_TABLE_ADAPTER: TypeAdapter[TomlTable] = TypeAdapter(TomlTable)


def _exclude_nested_none(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: _exclude_nested_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_exclude_nested_none(item) for item in value if item is not None]
    return value


class OllamaConfigPayload(BaseModel):
    """Ollama configuration — base_url for now."""

    model_config = ConfigDict(extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    base_url: str | None = None


class AcpConfigPayload(BaseModel):
    """ACP process command, represented as argv without shell evaluation."""

    model_config = ConfigDict(extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    command: list[str] | None = None

    @field_validator("command")
    @classmethod
    def validate_command(cls, command: list[str] | None) -> list[str] | None:
        if command is None:
            return None
        if len(command) > 32:
            raise ValueError("ACP command must contain at most 32 arguments")
        if any(not argument.strip() for argument in command):
            raise ValueError("ACP command arguments must not be empty")
        return command


class AgentSettingsPayload(BaseModel):
    """Non-reply agent flags that the client may send as a patch."""

    model_config = ConfigDict(extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    info_enabled: StrictBool | None = None


class ReplyStyleEnabledPatch(BaseModel):
    """One entry in a ``reply.styles`` patch array."""

    model_config = ConfigDict(extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    id: str
    enabled: StrictBool


class ReplySettingsPayload(BaseModel):
    """Reply feature settings and style enablement patches."""

    model_config = ConfigDict(extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    enabled: StrictBool | None = None
    auto_generate: StrictBool | None = None
    default_style: str | None = None
    styles: list[ReplyStyleEnabledPatch] | None = None


class SecretsPayload(BaseModel):
    """API key values the client may send to persist.

    Only non-empty strings will be saved; ``None`` / missing means "no update".
    """

    model_config = ConfigDict(extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    GEMINI_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    XAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    GOOGLE_CLOUD_PROJECT: str | None = None
    GOOGLE_APPLICATION_CREDENTIALS: str | None = None
    DEEPGRAM_API_KEY: str | None = None


class RecordingRetentionSettings(BaseModel):
    """Saved inputs for a user-requested recording cleanup; never auto-runs."""

    model_config = ConfigDict(extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    cutoff_date: date | None = None
    max_total_bytes: int | None = Field(default=None, gt=0)

    @field_validator("max_total_bytes", mode="before")
    @classmethod
    def zero_disables_capacity(cls, value: object) -> object:
        """Accept the UI's zero value as the disabled state."""
        return None if value == 0 else value


type ConnectionProvider = Literal["openai", "deepgram", "xai", "gemini", "anthropic"]


class ConnectionTestRequest(BaseModel):
    """A non-persistent provider credential check."""

    model_config = ConfigDict(extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    provider: ConnectionProvider
    api_key: str | None = None


class ConnectionTestResponse(BaseModel):
    """Credential-check result without exposing stored or draft secret material."""

    ok: bool
    status: Literal["verified", "invalid", "unavailable"]
    message: str


class SttSettingsPatch(BaseModel):
    """Typed patch surface for persisted ``[stt]`` settings."""

    model_config = ConfigDict(extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    backend: str | None = None
    whisper_model: str | None = None
    deepgram_model: str | None = None
    openai_model: str | None = None
    vosk_model_path: str | None = None
    language: str | None = None
    vad_engine: Literal["silero", "webrtc"] | None = None
    vad_sensitivity: float | None = Field(default=None, ge=0.05, le=0.95)
    silence_duration: float | None = Field(default=None, ge=0.1, le=5.0)
    vad_aggressiveness: int | None = Field(default=None, ge=0, le=3)
    device: str | None = None
    min_voiced_ms: int | None = Field(default=None, ge=0)
    min_voiced_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    min_rms_dbfs: float | None = None
    decode_no_speech_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    decode_log_prob_threshold: float | None = None
    decode_compression_ratio_threshold: float | None = Field(default=None, gt=0.0)
    hard_min_voiced_ms: int | None = Field(default=None, ge=0)
    hard_no_speech_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    hard_logprob_threshold: float | None = None
    hard_compression_ratio_threshold: float | None = Field(default=None, gt=0.0)
    soft_min_voiced_ms: int | None = Field(default=None, ge=0)
    soft_min_voiced_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    soft_min_rms_dbfs: float | None = None
    soft_no_speech_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    soft_logprob_threshold: float | None = None
    soft_compression_ratio_threshold: float | None = Field(default=None, gt=0.0)
    drop_score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    temperature: float | None = Field(default=None, ge=0.0)
    suspicious_phrases: list[str] | None = None

    @field_validator(
        "vad_sensitivity",
        "silence_duration",
        "vad_aggressiveness",
        "min_voiced_ms",
        "min_voiced_ratio",
        "min_rms_dbfs",
        "decode_no_speech_threshold",
        "decode_log_prob_threshold",
        "decode_compression_ratio_threshold",
        "hard_min_voiced_ms",
        "hard_no_speech_threshold",
        "hard_logprob_threshold",
        "hard_compression_ratio_threshold",
        "soft_min_voiced_ms",
        "soft_min_voiced_ratio",
        "soft_min_rms_dbfs",
        "soft_no_speech_threshold",
        "soft_logprob_threshold",
        "soft_compression_ratio_threshold",
        "drop_score_threshold",
        "temperature",
        mode="before",
    )
    @classmethod
    def reject_coerced_numeric_values(cls, value: object) -> object:
        if isinstance(value, (bool, str)):
            raise ValueError("must be a JSON number")
        return value


class AudioSettingsPatch(BaseModel):
    """Typed patch surface for persisted ``[audio]`` settings."""

    model_config = ConfigDict(extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    sample_rate: int | None = Field(default=None, gt=0, le=192_000)
    max_session_seconds: int | None = Field(default=None, gt=0, le=60)

    @field_validator("sample_rate", "max_session_seconds", mode="before")
    @classmethod
    def reject_coerced_numeric_values(cls, value: object) -> object:
        if isinstance(value, (bool, str)):
            raise ValueError("must be a JSON number")
        return value


class SettingsSaveRequest(BaseModel):
    """Request body for ``POST /api/settings``.

    Each field is optional; only provided fields are processed.
    """

    model_config = ConfigDict(extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    ollama: OllamaConfigPayload | None = None
    acp: AcpConfigPayload | None = None
    agents: AgentSettingsPayload | None = None
    reply: ReplySettingsPayload | None = None
    secrets: SecretsPayload | None = None
    stt: SttSettingsPatch | None = None
    audio: AudioSettingsPatch | None = None
    context: dict[str, object] | None = None
    usage_budget: UsageBudgetConfig | None = None
    recording_retention: RecordingRetentionSettings | None = None
    delete_secrets: list[SecretKey] | None = None

    def model_dump_toml(self) -> TomlTable:
        """Serialize the validated request into the service's persisted value domain."""
        json_value = _exclude_nested_none(self.model_dump(mode="json", exclude_none=True))
        return _TOML_TABLE_ADAPTER.validate_python(
            json_value,
            strict=True,
        )


class AgentSettings(BaseModel):
    """Non-reply agent settings returned in the settings response."""

    info_enabled: bool


class SecretsStatus(BaseModel):
    """Boolean presence indicators — never leak actual secret values."""

    GEMINI_API_KEY: bool = False
    OPENAI_API_KEY: bool = False
    XAI_API_KEY: bool = False
    ANTHROPIC_API_KEY: bool = False
    GOOGLE_CLOUD_PROJECT: bool = False
    GOOGLE_APPLICATION_CREDENTIALS: bool = False
    DEEPGRAM_API_KEY: bool = False


class ReplyStyleSettings(BaseModel):
    id: str
    label: str
    enabled: bool
    priority: int


class ReplySettings(BaseModel):
    """Reply feature settings returned in the settings response."""

    enabled: bool
    auto_generate: bool
    default_style: str
    styles: list[ReplyStyleSettings]


class UsageSummaryResponse(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_jpy: float = 0.0
    request_count: int = 0


class UsageSettingsResponse(BaseModel):
    budget: UsageBudgetConfig
    current_meeting: UsageSummaryResponse
    current_month: UsageSummaryResponse
    billing_mode: str


class ProviderSummary(BaseModel):
    """Provider metadata safe for Settings UI display."""

    id: str
    label: str
    kind: ProviderKind
    data_location: DataLocation
    base_url: str | None = None
    models: list[str] | None = None
    experimental: bool = False
    api_key_configured: bool | None = None


class OllamaConfig(BaseModel):
    """Ollama configuration returned in settings response."""

    base_url: str


class AcpConfig(BaseModel):
    """ACP runtime configuration safe for Advanced Settings."""

    command: list[str]
    runtime: Literal["acp"] = "acp"
    capabilities: list[Literal["reply"]] = ["reply"]


class SettingsResponse(BaseModel):
    """Full settings object returned to the frontend."""

    ollama: OllamaConfig
    acp: AcpConfig
    stt: TomlTable = {}
    audio: TomlTable = {}
    agents: AgentSettings
    reply: ReplySettings
    secrets: SecretsStatus
    data_dir: str
    context_dir: str
    providers: list[ProviderSummary]
    usage: UsageSettingsResponse
    recording_retention: RecordingRetentionSettings


class SaveSettingsResponse(BaseModel):
    """Response from a successful POST /api/settings."""

    ok: bool
    settings: SettingsResponse


class SettingsConflictDetail(BaseModel):
    code: Literal["AUDIO_SETTINGS_LOCKED"]
    message: str


class SettingsConflictResponse(BaseModel):
    """Structured response returned when audio settings are locked."""

    detail: SettingsConflictDetail


class RouteAssignmentsUpdate(BaseModel):
    """Full replacement of schema-v2 use-case route assignments."""

    model_config = ConfigDict(extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    reply: str | None
    info: str | None
    minutes: str | None


class OllamaModelsResponse(BaseModel):
    """Response from GET /api/settings/ollama/models."""

    ok: bool
    base_url: str
    models: list[str]
    message: str | None = None
