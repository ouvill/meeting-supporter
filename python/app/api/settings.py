"""Settings endpoints: GET/POST /api/settings."""

import json
import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Literal, cast

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.agents.models import ReplyAgentDefinition
from app.agents.route_catalog import (
    CodexStatusProvider,
    ManagedStatusProvider,
    OllamaStatusProvider,
    RouteCatalog,
    RouteCatalogResponse,
    route_supports,
)
from app.core.config import (
    SECRET_KEYS,
    AgentSettingKey,
    AiRouteAssignments,
    DataLocation,
    ProviderKind,
    RouteCapability,
    SecretKey,
    UsageBudgetConfig,
    patch_agent_settings,
)
from app.core.config import (
    AgentSettings as RuntimeAgentSettings,
)
from app.core.event_bus import EventBus
from app.core.events import ConfigChanged
from app.core.types import TomlTable, TomlValue
from app.services.settings_store import SettingsStore
from app.services.usage_logger import UsageLogger, UsageSummary

if TYPE_CHECKING:
    from app.core.state import AppState

# ── TOML safe accessor helpers ─────────────────────────────────────────────────


def _toml_str(val: object, default: str) -> str:
    """Return ``val`` if it is a ``str``, else ``default``."""
    return val if isinstance(val, str) else default


def _toml_number(val: object, default: float) -> float:
    """Return ``val`` if it is numeric, else ``default``."""
    return float(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else default


def _toml_int(val: object, default: int) -> int:
    """Return ``val`` if it is an ``int`` (but not ``bool``), else ``default``."""
    return val if isinstance(val, int) and not isinstance(val, bool) else default


def _toml_bool(val: object, default: bool) -> bool:
    """Return ``val`` if it is a ``bool``, else ``default``."""
    return val if isinstance(val, bool) else default


def _toml_table(val: object) -> TomlTable | None:
    """Return ``val`` if it is a ``dict[str, object]`` (coerced to TomlTable), else ``None``."""
    if not isinstance(val, dict):
        return None
    result: TomlTable = {}
    for k, v in val.items():  # pyright: ignore[reportUnknownVariableType]
        if isinstance(v, (str, int, float, bool, list, dict)):
            result[str(k)] = cast("TomlValue", v)  # pyright: ignore[reportUnknownArgumentType]
    return result


def _toml_date(val: object) -> date | None:
    """Parse a stored ISO date, treating malformed values as disabled."""
    if not isinstance(val, str):
        return None
    try:
        return date.fromisoformat(val)
    except ValueError:
        return None


# ── Request body models ────────────────────────────────────────────────────────


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


_CONNECTION_ENDPOINTS: dict[ConnectionProvider, tuple[str, str, dict[str, str]]] = {
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1/models", {"Authorization": "Bearer {api_key}"}),
    "deepgram": ("DEEPGRAM_API_KEY", "https://api.deepgram.com/v1/projects", {"Authorization": "Token {api_key}"}),
    "xai": ("XAI_API_KEY", "https://api.x.ai/v1/models", {"Authorization": "Bearer {api_key}"}),
    "gemini": (
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/models",
        {"x-goog-api-key": "{api_key}"},
    ),
    "anthropic": (
        "ANTHROPIC_API_KEY",
        "https://api.anthropic.com/v1/models",
        {"x-api-key": "{api_key}", "anthropic-version": "2023-06-01"},
    ),
}


def _connection_test_response(
    provider: ConnectionProvider,
    api_key: str | None,
) -> ConnectionTestResponse:
    """Verify a provider credential with a side-effect-free models request."""
    _secret_key, url, header_templates = _CONNECTION_ENDPOINTS[provider]
    if not api_key:
        return ConnectionTestResponse(ok=False, status="invalid", message="APIキーが設定されていません。")

    headers = {"Accept": "application/json"}
    headers.update({name: value.format(api_key=api_key) for name, value in header_templates.items()})
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # pyright: ignore[reportAny]
            status_code: int = response.status  # pyright: ignore[reportAny]
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return ConnectionTestResponse(ok=False, status="invalid", message="APIキーを確認してください。")
        return ConnectionTestResponse(ok=False, status="unavailable", message="サービスに接続できませんでした。")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return ConnectionTestResponse(ok=False, status="unavailable", message="サービスに接続できませんでした。")

    if 200 <= status_code < 300:
        return ConnectionTestResponse(ok=True, status="verified", message="接続を確認しました。")
    if status_code in (401, 403):
        return ConnectionTestResponse(ok=False, status="invalid", message="APIキーを確認してください。")
    return ConnectionTestResponse(ok=False, status="unavailable", message="サービスに接続できませんでした。")


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
    stt: dict[str, object] | None = None
    audio: dict[str, object] | None = None
    context: dict[str, object] | None = None
    usage_budget: UsageBudgetConfig | None = None
    recording_retention: RecordingRetentionSettings | None = None
    delete_secrets: list[SecretKey] | None = None


# ── Response models ────────────────────────────────────────────────────────────


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


def _usage_summary_response(summary: UsageSummary) -> UsageSummaryResponse:
    return UsageSummaryResponse(
        input_tokens=summary.input_tokens,
        output_tokens=summary.output_tokens,
        estimated_cost_jpy=summary.estimated_cost_jpy,
        request_count=summary.request_count,
    )


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


# ── Internal helpers ───────────────────────────────────────────────────────────


def _reply_style_settings(definitions: list[ReplyAgentDefinition]) -> list[ReplyStyleSettings]:
    return [
        ReplyStyleSettings(
            id=d.id,
            label=d.label,
            enabled=d.enabled,
            priority=d.priority,
        )
        for d in definitions
    ]


def _provider_summaries(state: "AppState") -> list[ProviderSummary]:
    return [
        ProviderSummary(
            id=p.id,
            label=p.label,
            kind=p.kind,
            data_location=p.data_location,
            base_url=p.base_url,
            models=p.models,
            experimental=p.experimental,
            api_key_configured=state.secret_store.status(p.key_ref) if p.key_ref else None,
        )
        for p in state.config.providers
    ]


def _existing_reply_style_tables(cfg: TomlTable) -> list[TomlTable]:
    """Return configured ``reply.styles`` entries."""
    reply = _toml_table(cfg.get("reply"))
    raw = reply.get("styles") if reply is not None else None
    if not isinstance(raw, list):
        return []
    tables: list[TomlTable] = []
    for entry in raw:
        tbl = _toml_table(entry)
        if tbl is not None and isinstance(tbl.get("id"), str):
            tables.append(tbl)
    return tables


def _fallback_reply_style_tables(definitions: list[ReplyAgentDefinition]) -> list[TomlTable]:
    return [
        {
            "id": d.id,
            "label": d.label,
            "enabled": d.enabled,
            "priority": d.priority,
            "instruction": d.instruction,
        }
        for d in definitions
    ]


def _apply_reply_style_enabled_patch(
    *,
    cfg: TomlTable,
    definitions: list[ReplyAgentDefinition],
    patch: dict[str, bool],
) -> list[TomlTable] | str:
    tables = _existing_reply_style_tables(cfg) or _fallback_reply_style_tables(definitions)
    by_id: dict[str, TomlTable] = {}
    for table in tables:
        eid = table.get("id")
        if isinstance(eid, str):
            by_id[eid] = table
    unknown_ids = sorted(set(patch) - set(by_id))
    if unknown_ids:
        return f"未知の reply.styles id です: {', '.join(unknown_ids)}"
    for entry_id, enabled in patch.items():
        by_id[entry_id]["enabled"] = enabled
    return tables


def _build_settings_response(
    *,
    state: "AppState",
    store: SettingsStore,
    merged_agent_settings: RuntimeAgentSettings | None = None,
    ollama_override: OllamaConfig | None = None,
    reply_style_tables: list[TomlTable] | None = None,
) -> SettingsResponse:
    """Build a SettingsResponse from current state and config store.

    Parameters
    ----------
    state:
        The application state (LLM config, agent settings, etc.).
    store:
        The settings store (for config file data such as stt/audio).
    merged_agent_settings:
        Optional overridden agent settings (e.g. after a save patch).
        Falls back to ``state.config.agent_settings`` when ``None``.
    ollama_override:
        Optional Ollama config override. Falls back to config file, then state.config.
    reply_style_tables:
        Optional reply style table overrides (e.g. after saving patched tables).
        Falls back to target ``reply.styles`` from the config file, then to
        ``state.config.reply_agent_definitions``.
    """
    cfg = store.load_config()
    stt_section = _toml_table(cfg.get("stt"))
    audio_section = _toml_table(cfg.get("audio"))
    usage_budget_section = _toml_table(cfg.get("usage_budget"))
    recording_retention_section = _toml_table(cfg.get("recording_retention"))

    # Ollama: override → config file → state.config fallback
    if ollama_override is not None:
        ollama = ollama_override
    else:
        ollama_section = _toml_table(cfg.get("ollama"))
        if ollama_section is not None:
            base_url = _toml_str(ollama_section.get("base_url"), state.config.ollama_base_url)
            ollama = OllamaConfig(base_url=base_url)
        else:
            ollama = OllamaConfig(base_url=state.config.ollama_base_url)

    # Reply styles: override → target config → state.config definitions
    if reply_style_tables is not None:
        reply_styles = [
            ReplyStyleSettings(
                id=_toml_str(t.get("id"), ""),
                label=_toml_str(t.get("label"), ""),
                enabled=_toml_bool(t.get("enabled"), True),
                priority=_toml_int(t.get("priority"), 100),
            )
            for t in reply_style_tables
            if isinstance(t.get("id"), str)
        ]
    else:
        cfg_reply_styles = _existing_reply_style_tables(cfg)
        if cfg_reply_styles:
            reply_styles = [
                ReplyStyleSettings(
                    id=_toml_str(entry.get("id"), ""),
                    label=_toml_str(entry.get("label"), ""),
                    enabled=_toml_bool(entry.get("enabled"), True),
                    priority=_toml_int(entry.get("priority"), 100),
                )
                for entry in cfg_reply_styles
                if isinstance(entry.get("id"), str)
            ]
        else:
            reply_styles = _reply_style_settings(state.config.reply_agent_definitions)

    agent_settings = merged_agent_settings if merged_agent_settings is not None else state.config.agent_settings
    reply_section = _toml_table(cfg.get("reply"))
    default_style = _toml_str(reply_section.get("default_style"), "standard") if reply_section else "standard"

    usage_budget = UsageBudgetConfig(
        meeting_limit_jpy=_toml_number(
            usage_budget_section.get("meeting_limit_jpy"), state.config.usage_budget.meeting_limit_jpy
        )
        if usage_budget_section is not None
        else state.config.usage_budget.meeting_limit_jpy,
        monthly_limit_jpy=_toml_number(
            usage_budget_section.get("monthly_limit_jpy"), state.config.usage_budget.monthly_limit_jpy
        )
        if usage_budget_section is not None
        else state.config.usage_budget.monthly_limit_jpy,
    )
    recording_retention = RecordingRetentionSettings(
        cutoff_date=_toml_date(recording_retention_section.get("cutoff_date"))
        if recording_retention_section is not None
        else None,
        max_total_bytes=(
            value if (value := _toml_int(recording_retention_section.get("max_total_bytes"), 0)) > 0 else None
        )
        if recording_retention_section is not None
        else None,
    )
    usage_logger = UsageLogger(state.config.user_data_dir / "usage.jsonl")
    current_session = state.current_session
    current_meeting_id = current_session.id if current_session is not None else None
    current_meeting_summary = (
        usage_logger.summarize(meeting_id=current_meeting_id)
        if current_meeting_id
        else usage_logger.summarize(meeting_id="")
    )
    current_month_summary = usage_logger.summarize(month=datetime.now(UTC))
    reply_route = state.config.ai_assignments.reply
    billing_mode = (
        "unassigned"
        if reply_route is None
        else "external_subscription"
        if reply_route in ("codex", "acp")
        else "local"
        if reply_route == "ollama"
        else "byok"
    )

    ai_section = _toml_table(cfg.get("ai"))
    route_section = _toml_table(ai_section.get("routes")) if ai_section is not None else None
    acp_section = _toml_table(route_section.get("acp")) if route_section is not None else None
    raw_acp_command = acp_section.get("command") if acp_section is not None else None
    if isinstance(raw_acp_command, list) and all(isinstance(item, str) for item in raw_acp_command):
        acp_command = cast(list[str], raw_acp_command)
    else:
        acp_route = next((route for route in state.config.routes if route.id == "acp"), None)
        acp_command = list(acp_route.command or ()) if acp_route is not None else []

    return SettingsResponse(
        ollama=ollama,
        acp=AcpConfig(command=acp_command),
        stt=stt_section if stt_section is not None else {},
        audio=audio_section if audio_section is not None else {},
        agents=AgentSettings(info_enabled=agent_settings["info_enabled"]),
        reply=ReplySettings(
            enabled=agent_settings["reply_enabled"],
            auto_generate=agent_settings["reply_auto_generate"],
            default_style=default_style,
            styles=reply_styles,
        ),
        providers=_provider_summaries(state),
        secrets=SecretsStatus(**{k: v for k, v in state.secret_store.status_all().items() if k in SECRET_KEYS}),
        data_dir=str(state.config.user_data_dir),
        context_dir=str(state.config.context_dir),
        usage=UsageSettingsResponse(
            budget=usage_budget,
            current_meeting=_usage_summary_response(current_meeting_summary),
            current_month=_usage_summary_response(current_month_summary),
            billing_mode=billing_mode,
        ),
        recording_retention=recording_retention,
    )


def _merge_ollama_from_body(
    body_ollama: OllamaConfigPayload | None,
    state: "AppState",
) -> OllamaConfig | None:
    """Build an ``OllamaConfig`` override from the request body, if base_url was provided."""
    if body_ollama is None:
        return None
    raw = body_ollama.model_dump(exclude_none=True)
    if not raw:
        return None
    base_url = _toml_str(raw.get("base_url"), state.config.ollama_base_url)
    return OllamaConfig(base_url=base_url)


def _route_catalog(
    *,
    state: "AppState",
    assignments: AiRouteAssignments | None = None,
    managed_status: ManagedStatusProvider | None = None,
    codex_status: CodexStatusProvider | None = None,
    ollama_status: OllamaStatusProvider | None = None,
) -> RouteCatalog:
    return RouteCatalog(
        providers=state.config.providers,
        routes=state.config.routes,
        assignments=assignments or state.config.ai_assignments,
        secret_store=state.secret_store,
        managed_status=managed_status,
        codex_status=codex_status,
        ollama_status=ollama_status,
    )


def flatten_ai_tables(
    cfg: TomlTable,
    assignments: AiRouteAssignments | None = None,
) -> None:
    """Convert nested parsed AI tables to dotted sections for the TOML writer."""

    raw_ai = cfg.pop("ai", None)
    ai = cast(TomlTable, raw_ai) if isinstance(raw_ai, dict) else {}
    raw_assignments = ai.get("assignments")
    existing_assignments = cast(TomlTable, raw_assignments) if isinstance(raw_assignments, dict) else {}
    raw_routes = ai.get("routes")
    route_tables = cast(dict[str, object], raw_routes) if isinstance(raw_routes, dict) else {}

    cfg["ai"] = {"schema_version": 2}
    if assignments is None:
        assignment_table: TomlTable = {}
        reply = existing_assignments.get("reply")
        if isinstance(reply, str) and reply:
            assignment_table["reply"] = reply
        for key in ("info", "minutes"):
            value = existing_assignments.get(key)
            if isinstance(value, str) and value:
                assignment_table[key] = value
    else:
        assignment_table = {}
        if assignments.reply is not None:
            assignment_table["reply"] = assignments.reply
        if assignments.info is not None:
            assignment_table["info"] = assignments.info
        if assignments.minutes is not None:
            assignment_table["minutes"] = assignments.minutes
    cfg["ai.assignments"] = assignment_table

    for route_id, raw_route in route_tables.items():
        if not isinstance(raw_route, dict):
            continue
        route_table = cast(TomlTable, raw_route)
        scalar_route = {key: value for key, value in route_table.items() if key != "env"}
        if scalar_route:
            cfg[f"ai.routes.{route_id}"] = scalar_route
        env = route_table.get("env")
        if isinstance(env, dict):
            cfg[f"ai.routes.{route_id}.env"] = cast(TomlTable, env)


def _write_ai_assignments(store: SettingsStore, assignments: AiRouteAssignments) -> None:
    """Persist a full route-assignment replacement without losing route config."""

    with store.locked():
        cfg = store.load_config()
        flatten_ai_tables(cfg, assignments)
        store.write_sectioned_toml(store.config_path, cfg)


def _route_assignment_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": message, "retryable": False},
    )


# ── Router factory ─────────────────────────────────────────────────────────────


def create_router(
    *,
    state: "AppState",
    store: SettingsStore,
    event_bus: EventBus,
    managed_status: ManagedStatusProvider | None = None,
    codex_status: CodexStatusProvider | None = None,
    ollama_status: OllamaStatusProvider | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/ai/routes")
    async def get_ai_routes() -> RouteCatalogResponse:  # pyright: ignore[reportUnusedFunction]
        return await _route_catalog(
            state=state,
            managed_status=managed_status,
            codex_status=codex_status,
            ollama_status=ollama_status,
        ).read()

    @router.put("/ai/routes/assignments")
    async def replace_ai_route_assignments(  # pyright: ignore[reportUnusedFunction]
        body: RouteAssignmentsUpdate,
    ) -> RouteCatalogResponse:
        candidate = AiRouteAssignments(reply=body.reply, info=body.info, minutes=body.minutes)
        current = await _route_catalog(
            state=state,
            managed_status=managed_status,
            assignments=candidate,
            codex_status=codex_status,
            ollama_status=ollama_status,
        ).read()
        by_id = {route.id: route for route in current.routes}
        assignments_by_use_case: tuple[tuple[RouteCapability, str | None], ...] = (
            ("reply", candidate.reply),
            ("info", candidate.info),
            ("minutes", candidate.minutes),
        )
        for use_case, route_id in assignments_by_use_case:
            if route_id is None:
                continue
            route = by_id.get(route_id)
            if route is None:
                raise _route_assignment_error("AI_ROUTE_NOT_FOUND", "指定されたAI経路は存在しません。")
            if not route_supports(route, use_case):
                raise _route_assignment_error(
                    "AI_ROUTE_NOT_SELECTABLE",
                    f"指定されたAI経路は{use_case}に選択できません。",
                )
        _write_ai_assignments(store, candidate)
        await event_bus.publish(ConfigChanged())
        return current

    @router.get("/settings")
    async def get_settings() -> SettingsResponse:  # pyright: ignore[reportUnusedFunction]
        return _build_settings_response(state=state, store=store)

    @router.post("/settings", response_model=SaveSettingsResponse)
    async def save_settings(  # pyright: ignore[reportUnusedFunction]
        body: SettingsSaveRequest,
    ) -> SaveSettingsResponse:
        ollama_override = _merge_ollama_from_body(body.ollama, state)

        # ── Agents / Reply ──
        # StrictBool on payload models guarantees values are bool; no manual check needed.
        merged_agent_settings = state.config.agent_settings
        if body.agents is not None:
            raw_agents: dict[str, object] = body.agents.model_dump(exclude_none=True)
            patch: dict[AgentSettingKey, bool] = {}
            if "info_enabled" in raw_agents:
                patch["info_enabled"] = cast("bool", raw_agents["info_enabled"])
            result = patch_agent_settings(state.config.agent_settings, patch)
            if isinstance(result, str):
                raise HTTPException(status_code=400, detail=result)
            merged_agent_settings = result

        reply_style_tables_override: list[TomlTable] | None = None
        reply_section_override: TomlTable | None = None
        if body.reply is not None:
            raw_reply: dict[str, object] = body.reply.model_dump(exclude_none=True)
            reply_patch: dict[AgentSettingKey, bool] = {}
            if "enabled" in raw_reply:
                reply_patch["reply_enabled"] = cast("bool", raw_reply["enabled"])
            if "auto_generate" in raw_reply:
                reply_patch["reply_auto_generate"] = cast("bool", raw_reply["auto_generate"])
            result = patch_agent_settings(merged_agent_settings, reply_patch)
            if isinstance(result, str):
                raise HTTPException(status_code=400, detail=result)
            merged_agent_settings = result

            existing_cfg = store.load_config()
            style_patch: dict[str, bool] = {}
            if body.reply.styles is not None:
                style_patch = {p.id: p.enabled for p in body.reply.styles}
            reply_style_tables = _apply_reply_style_enabled_patch(
                cfg=existing_cfg,
                definitions=state.config.reply_agent_definitions,
                patch=style_patch,
            )
            if isinstance(reply_style_tables, str):
                raise HTTPException(status_code=400, detail=reply_style_tables)
            if merged_agent_settings["reply_enabled"] and not any(
                _toml_bool(table.get("enabled"), True) for table in reply_style_tables
            ):
                raise HTTPException(status_code=400, detail="返答案のスタイルは最低1つ有効にしてください")
            reply_style_tables_override = reply_style_tables
            existing_reply = _toml_table(existing_cfg.get("reply"))
            default_style = (
                _toml_str(raw_reply.get("default_style"), "")
                or (_toml_str(existing_reply.get("default_style"), "") if existing_reply else "")
                or "standard"
            )
            reply_section_override = cast(
                TomlTable,
                {
                    "enabled": merged_agent_settings["reply_enabled"],
                    "auto_generate": merged_agent_settings["reply_auto_generate"],
                    "default_style": default_style,
                    "styles": reply_style_tables,
                },
            )

        # ── Secrets ──
        secrets_changed = False
        if body.secrets is not None:
            typed_secrets: dict[str, object] = body.secrets.model_dump(exclude_none=True)
            updates: dict[SecretKey, str] = {}
            for k in SECRET_KEYS:
                v = typed_secrets.get(k)
                if isinstance(v, str) and v:
                    updates[k] = v
            if updates:
                state.secret_store.set_secrets({str(key): value for key, value in updates.items()})
                secrets_changed = True

        if body.delete_secrets is not None:
            for key in set(body.delete_secrets):
                state.secret_store.delete(key)
            secrets_changed = secrets_changed or bool(body.delete_secrets)
        if secrets_changed:
            await event_bus.publish(ConfigChanged())

        # ── Config sections (stt, audio, ollama, context, etc.) ──
        cfg_sections: dict[str, object] = {}
        acp_command_override = body.acp.command if body.acp is not None and body.acp.command is not None else None

        if body.stt is not None:
            cfg_sections["stt"] = body.stt
        if body.audio is not None:
            cfg_sections["audio"] = body.audio
        if body.context is not None:
            cfg_sections["context"] = body.context
        if body.usage_budget is not None:
            cfg_sections["usage_budget"] = body.usage_budget.model_dump()
        if body.recording_retention is not None:
            cfg_sections["recording_retention"] = body.recording_retention.model_dump(mode="json", exclude_none=True)
        if body.ollama is not None:
            ollama_dict = body.ollama.model_dump(exclude_none=True)
            if ollama_dict:
                cfg_sections["ollama"] = ollama_dict
        if body.agents is not None:
            cfg_sections["agents"] = {"info_enabled": merged_agent_settings["info_enabled"]}
        if reply_section_override is not None:
            cfg_sections["reply"] = reply_section_override
        if acp_command_override is not None:
            cfg_sections["ai"] = {}

        if cfg_sections:
            with store.locked():
                existing_cfg = store.load_config()
                if acp_command_override is not None:
                    raw_ai = existing_cfg.get("ai")
                    ai = cast(dict[str, TomlValue], raw_ai) if isinstance(raw_ai, dict) else {}
                    raw_routes = ai.get("routes")
                    routes = cast(dict[str, TomlValue], raw_routes) if isinstance(raw_routes, dict) else {}
                    raw_acp = routes.get("acp")
                    acp = cast(dict[str, TomlValue], raw_acp) if isinstance(raw_acp, dict) else {}
                    acp["runtime"] = "acp"
                    acp["command"] = cast("TomlValue", acp_command_override)
                    routes["acp"] = cast("TomlValue", acp)
                    ai["routes"] = cast("TomlValue", routes)
                    existing_cfg["ai"] = cast("TomlValue", ai)
                for section, values in cfg_sections.items():
                    if section == "ai":
                        continue
                    if section == "recording_retention":
                        # This is a complete policy replacement, so clearing an
                        # input actually disables it instead of reviving a stale
                        # TOML key through the usual section merge.
                        existing_cfg[section] = cast("TomlValue", values)
                        continue
                    existing = existing_cfg.get(section)
                    if isinstance(existing, dict) and isinstance(values, dict):
                        existing.update(cast("dict[str, TomlValue]", values))  # type: ignore[typeddict-item]
                    else:
                        existing_cfg[section] = cast("TomlValue", values)
                if "reply" in cfg_sections:
                    _ = existing_cfg.pop("reply_agents", None)
                    agents_section = existing_cfg.get("agents")
                    if isinstance(agents_section, dict):
                        _ = agents_section.pop("reply_enabled", None)
                        _ = agents_section.pop("reply_auto_generate", None)
                        _ = agents_section.pop("reply_main", None)
                        _ = agents_section.pop("reply_polite", None)
                flatten_ai_tables(existing_cfg)
                store.write_sectioned_toml(store.config_path, existing_cfg)
            await event_bus.publish(ConfigChanged())

        # ── Build response with actual saved/merged values ──
        return SaveSettingsResponse(
            ok=True,
            settings=_build_settings_response(
                state=state,
                store=store,
                merged_agent_settings=merged_agent_settings,
                ollama_override=ollama_override,
                reply_style_tables=reply_style_tables_override,
            ),
        )

    @router.post("/settings/connections/test", response_model=ConnectionTestResponse)
    def test_connection(  # pyright: ignore[reportUnusedFunction]
        body: ConnectionTestRequest,
    ) -> ConnectionTestResponse:
        """Test an unsaved draft key, or a configured credential, without persisting it."""
        secret_key, _, _ = _CONNECTION_ENDPOINTS[body.provider]
        api_key = body.api_key if body.api_key else state.secret_store.get(secret_key)
        return _connection_test_response(body.provider, api_key)

    @router.get("/settings/ollama/models")
    def get_ollama_models(  # pyright: ignore[reportUnusedFunction]
        base_url: str | None = None,
    ) -> OllamaModelsResponse:
        """Fetch available models from an Ollama server.

        Calls ``GET {base_url}/models`` which for the default base_url
        ``http://localhost:11434/v1`` becomes ``http://localhost:11434/v1/models``.

        Returns a typed response with ok: bool, base_url, models: list[str], message: str | None.
        On connection failure, returns 200 with ok=false and a user-friendly Japanese message.

        Query parameter ``base_url`` overrides the configured Ollama base URL.
        """
        effective_base_url = base_url if base_url else state.config.ollama_base_url
        # Strip trailing slash for consistent URL construction
        effective_base_url = effective_base_url.rstrip("/")
        models_url = f"{effective_base_url}/models"

        raw_body: str
        try:
            req = urllib.request.Request(models_url, method="GET")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=5) as response:  # pyright: ignore[reportAny]
                status_code: int = response.status  # pyright: ignore[reportAny]
                if status_code != 200:
                    return OllamaModelsResponse(
                        ok=False,
                        base_url=effective_base_url,
                        models=[],
                        message=f"Ollamaサーバーからエラー応答がありました (HTTP {status_code})",
                    )
                raw_body = response.read().decode("utf-8")  # pyright: ignore[reportAny]
        except urllib.error.HTTPError as e:
            return OllamaModelsResponse(
                ok=False,
                base_url=effective_base_url,
                models=[],
                message=f"Ollamaサーバーからエラー応答がありました (HTTP {e.code})",
            )
        except urllib.error.URLError as e:
            # urllib wraps TimeoutError in URLError, so check e.reason for it
            if isinstance(e.reason, TimeoutError):
                return OllamaModelsResponse(
                    ok=False,
                    base_url=effective_base_url,
                    models=[],
                    message="Ollamaサーバーへの接続がタイムアウトしました",
                )
            return OllamaModelsResponse(
                ok=False,
                base_url=effective_base_url,
                models=[],
                message="Ollamaサーバーに接続できませんでした",
            )
        except (OSError, ValueError):
            return OllamaModelsResponse(
                ok=False,
                base_url=effective_base_url,
                models=[],
                message="Ollamaサーバーとの通信に失敗しました",
            )

        # Parse OpenAI-compatible response: {"data": [{"id": "..."}, ...]}
        try:
            parsed_raw: object = json.loads(raw_body)  # pyright: ignore[reportAny]
        except json.JSONDecodeError:
            return OllamaModelsResponse(
                ok=False,
                base_url=effective_base_url,
                models=[],
                message="Ollamaサーバーからの応答を解析できませんでした",
            )

        if not isinstance(parsed_raw, dict):
            return OllamaModelsResponse(
                ok=False,
                base_url=effective_base_url,
                models=[],
                message="Ollamaサーバーからの応答形式が不正です",
            )

        # Cast to dict[str, object] after isinstance check
        parsed: dict[str, object] = parsed_raw  # pyright: ignore[reportUnknownVariableType]
        data: object = parsed.get("data")
        if not isinstance(data, list):
            return OllamaModelsResponse(
                ok=False,
                base_url=effective_base_url,
                models=[],
                message="Ollamaサーバーからの応答にモデル一覧が含まれていません",
            )

        model_ids: list[str] = []
        for entry in data:  # pyright: ignore[reportUnknownVariableType]
            if isinstance(entry, dict):
                entry_dict: dict[str, object] = entry  # pyright: ignore[reportUnknownVariableType]
                model_id: object = entry_dict.get("id")
                if isinstance(model_id, str) and model_id:
                    model_ids.append(model_id)

        return OllamaModelsResponse(
            ok=True,
            base_url=effective_base_url,
            models=model_ids,
            message=None,
        )

    return router


__all__ = ["create_router"]
