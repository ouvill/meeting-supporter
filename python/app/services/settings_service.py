"""Application operations for reading and transactionally saving settings."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeGuard

from app.agents.models import ReplyAgentDefinition
from app.core.config import (
    SECRET_KEYS,
    AgentSettingKey,
    AiRouteAssignments,
    EffectiveAudioSttConfig,
    SecretKey,
    UsageBudgetConfig,
    normalize_audio_stt_config,
    patch_agent_settings,
)
from app.core.event_bus import EventBus
from app.core.events import ConfigChanged
from app.core.protocols import SecretRollbackError, TransactionalSecretStore
from app.core.types import TomlTable, TomlValue
from app.services.settings_serialization import (
    LEGACY_STT_KEYS,
    canonical_stt_section,
    flatten_ai_tables,
    toml_bool,
    toml_date,
    toml_int,
    toml_number,
    toml_str,
    toml_table,
)
from app.services.settings_store import SettingsStore
from app.services.usage_logger import UsageLogger, UsageSummary

if TYPE_CHECKING:
    from app.core.config import AgentSettings as RuntimeAgentSettings
    from app.core.state import AppState


class SettingsPatchError(ValueError):
    """A semantically invalid sparse settings patch."""


class SettingsValidationError(ValueError):
    """A settings patch that fails effective runtime validation."""


class AudioSettingsLockedError(RuntimeError):
    """An audio/STT change attempted while audio is running."""


@dataclass(frozen=True)
class SaveSettingsResult:
    merged_agent_settings: "RuntimeAgentSettings"
    ollama_base_url_override: str | None
    reply_style_tables: list[TomlTable] | None


def _request_table(value: TomlValue | None) -> TomlTable | None:
    return toml_table(value)


def _is_secret_key(value: TomlValue) -> TypeGuard[SecretKey]:
    return isinstance(value, str) and value in SECRET_KEYS


def _toml_string_list(value: TomlValue | None) -> list[str] | None:
    if not isinstance(value, list):
        return None
    strings = [item for item in value if isinstance(item, str)]
    return strings if len(strings) == len(value) else None


def _usage_summary_data(summary: UsageSummary) -> dict[str, object]:
    return {
        "input_tokens": summary.input_tokens,
        "output_tokens": summary.output_tokens,
        "estimated_cost_jpy": summary.estimated_cost_jpy,
        "request_count": summary.request_count,
    }


def _reply_style_data(definitions: list[ReplyAgentDefinition]) -> list[dict[str, object]]:
    return [
        {
            "id": definition.id,
            "label": definition.label,
            "enabled": definition.enabled,
            "priority": definition.priority,
        }
        for definition in definitions
    ]


def _provider_summary_data(state: "AppState") -> list[dict[str, object]]:
    return [
        {
            "id": provider.id,
            "label": provider.label,
            "kind": provider.kind,
            "data_location": provider.data_location,
            "base_url": provider.base_url,
            "models": provider.models,
            "experimental": provider.experimental,
            "api_key_configured": state.secret_store.status(provider.key_ref) if provider.key_ref else None,
        }
        for provider in state.config.providers
    ]


def _existing_reply_style_tables(cfg: TomlTable) -> list[TomlTable]:
    reply = toml_table(cfg.get("reply"))
    raw = reply.get("styles") if reply is not None else None
    if not isinstance(raw, list):
        return []
    tables: list[TomlTable] = []
    for entry in raw:
        table = toml_table(entry)
        if table is not None and isinstance(table.get("id"), str):
            tables.append(table)
    return tables


def _fallback_reply_style_tables(definitions: list[ReplyAgentDefinition]) -> list[TomlTable]:
    return [
        {
            "id": definition.id,
            "label": definition.label,
            "enabled": definition.enabled,
            "priority": definition.priority,
            "instruction": definition.instruction,
        }
        for definition in definitions
    ]


def _apply_reply_style_enabled_patch(
    *,
    cfg: TomlTable,
    definitions: list[ReplyAgentDefinition],
    patch: dict[str, bool],
) -> list[TomlTable]:
    tables = _existing_reply_style_tables(cfg) or _fallback_reply_style_tables(definitions)
    by_id: dict[str, TomlTable] = {}
    for table in tables:
        entry_id = table.get("id")
        if isinstance(entry_id, str):
            by_id[entry_id] = table
    unknown_ids = sorted(set(patch) - set(by_id))
    if unknown_ids:
        raise SettingsPatchError(f"未知の reply.styles id です: {', '.join(unknown_ids)}")
    for entry_id, enabled in patch.items():
        by_id[entry_id]["enabled"] = enabled
    return tables


def build_settings_response_data(
    *,
    state: "AppState",
    store: SettingsStore,
    merged_agent_settings: "RuntimeAgentSettings | None" = None,
    ollama_base_url_override: str | None = None,
    reply_style_tables: list[TomlTable] | None = None,
) -> dict[str, object]:
    """Build transport-neutral data for the full settings response."""
    cfg = store.load_config()
    stt_section = toml_table(cfg.get("stt"))
    audio_section = toml_table(cfg.get("audio"))
    usage_budget_section = toml_table(cfg.get("usage_budget"))
    recording_retention_section = toml_table(cfg.get("recording_retention"))

    if ollama_base_url_override is not None:
        ollama_base_url = ollama_base_url_override
    else:
        ollama_section = toml_table(cfg.get("ollama"))
        ollama_base_url = (
            toml_str(ollama_section.get("base_url"), state.config.ollama_base_url)
            if ollama_section is not None
            else state.config.ollama_base_url
        )

    if reply_style_tables is not None:
        reply_styles = [
            {
                "id": toml_str(table.get("id"), ""),
                "label": toml_str(table.get("label"), ""),
                "enabled": toml_bool(table.get("enabled"), True),
                "priority": toml_int(table.get("priority"), 100),
            }
            for table in reply_style_tables
            if isinstance(table.get("id"), str)
        ]
    else:
        configured_reply_styles = _existing_reply_style_tables(cfg)
        reply_styles = (
            [
                {
                    "id": toml_str(entry.get("id"), ""),
                    "label": toml_str(entry.get("label"), ""),
                    "enabled": toml_bool(entry.get("enabled"), True),
                    "priority": toml_int(entry.get("priority"), 100),
                }
                for entry in configured_reply_styles
                if isinstance(entry.get("id"), str)
            ]
            if configured_reply_styles
            else _reply_style_data(state.config.reply_agent_definitions)
        )

    agent_settings = merged_agent_settings if merged_agent_settings is not None else state.config.agent_settings
    reply_section = toml_table(cfg.get("reply"))
    default_style = toml_str(reply_section.get("default_style"), "standard") if reply_section else "standard"
    usage_budget = UsageBudgetConfig(
        meeting_limit_jpy=(
            toml_number(usage_budget_section.get("meeting_limit_jpy"), state.config.usage_budget.meeting_limit_jpy)
            if usage_budget_section is not None
            else state.config.usage_budget.meeting_limit_jpy
        ),
        monthly_limit_jpy=(
            toml_number(usage_budget_section.get("monthly_limit_jpy"), state.config.usage_budget.monthly_limit_jpy)
            if usage_budget_section is not None
            else state.config.usage_budget.monthly_limit_jpy
        ),
    )
    recording_retention = {
        "cutoff_date": (
            toml_date(recording_retention_section.get("cutoff_date"))
            if recording_retention_section is not None
            else None
        ),
        "max_total_bytes": (
            value if (value := toml_int(recording_retention_section.get("max_total_bytes"), 0)) > 0 else None
        )
        if recording_retention_section is not None
        else None,
    }

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

    ai_section = toml_table(cfg.get("ai"))
    route_section = toml_table(ai_section.get("routes")) if ai_section is not None else None
    acp_section = toml_table(route_section.get("acp")) if route_section is not None else None
    raw_acp_command = acp_section.get("command") if acp_section is not None else None
    acp_command = _toml_string_list(raw_acp_command)
    if acp_command is None:
        acp_route = next((route for route in state.config.routes if route.id == "acp"), None)
        acp_command = list(acp_route.command or ()) if acp_route is not None else []

    return {
        "ollama": {"base_url": ollama_base_url},
        "acp": {"command": acp_command},
        "stt": canonical_stt_section(stt_section),
        "audio": audio_section if audio_section is not None else {},
        "agents": {"info_enabled": agent_settings["info_enabled"]},
        "reply": {
            "enabled": agent_settings["reply_enabled"],
            "auto_generate": agent_settings["reply_auto_generate"],
            "default_style": default_style,
            "styles": reply_styles,
        },
        "providers": _provider_summary_data(state),
        "secrets": {key: value for key, value in state.secret_store.status_all().items() if key in SECRET_KEYS},
        "data_dir": str(state.config.user_data_dir),
        "context_dir": str(state.config.context_dir),
        "usage": {
            "budget": usage_budget,
            "current_meeting": _usage_summary_data(current_meeting_summary),
            "current_month": _usage_summary_data(current_month_summary),
            "billing_mode": billing_mode,
        },
        "recording_retention": recording_retention,
    }


def _merged_audio_stt_config(body: TomlTable, state: "AppState") -> EffectiveAudioSttConfig:
    candidate_stt = replace(state.config.stt_config)
    stt_patch = _request_table(body.get("stt"))
    if stt_patch is not None:
        for field_name, value in stt_patch.items():
            if field_name == "suspicious_phrases":
                phrases = _toml_string_list(value)
                if phrases is None:
                    raise ValueError("stt.suspicious_phrases must be a list of strings")
                setattr(candidate_stt, field_name, tuple(phrases))
                continue
            setattr(candidate_stt, field_name, value)

    sample_rate = state.config.audio_sample_rate
    max_session_seconds = state.config.audio_max_session_seconds
    audio_patch = _request_table(body.get("audio"))
    if audio_patch is not None:
        raw_sample_rate = audio_patch.get("sample_rate")
        if isinstance(raw_sample_rate, int) and not isinstance(raw_sample_rate, bool):
            sample_rate = raw_sample_rate
        raw_max_session_seconds = audio_patch.get("max_session_seconds")
        if isinstance(raw_max_session_seconds, int) and not isinstance(raw_max_session_seconds, bool):
            max_session_seconds = raw_max_session_seconds

    return normalize_audio_stt_config(
        candidate_stt,
        audio_sample_rate=sample_rate,
        audio_max_session_seconds=max_session_seconds,
    )


def _merge_agent_settings(
    body: TomlTable,
    state: "AppState",
    store: SettingsStore,
) -> tuple["RuntimeAgentSettings", list[TomlTable] | None, TomlTable | None]:
    merged = state.config.agent_settings
    agents = _request_table(body.get("agents"))
    if agents is not None:
        patch: dict[AgentSettingKey, bool] = {}
        info_enabled = agents.get("info_enabled")
        if isinstance(info_enabled, bool):
            patch["info_enabled"] = info_enabled
        result = patch_agent_settings(state.config.agent_settings, patch)
        if isinstance(result, str):
            raise SettingsPatchError(result)
        merged = result

    raw_reply = _request_table(body.get("reply"))
    if raw_reply is None:
        return merged, None, None

    reply_patch: dict[AgentSettingKey, bool] = {}
    enabled = raw_reply.get("enabled")
    if isinstance(enabled, bool):
        reply_patch["reply_enabled"] = enabled
    auto_generate = raw_reply.get("auto_generate")
    if isinstance(auto_generate, bool):
        reply_patch["reply_auto_generate"] = auto_generate
    result = patch_agent_settings(merged, reply_patch)
    if isinstance(result, str):
        raise SettingsPatchError(result)
    merged = result

    existing_cfg = store.load_config()
    style_patch: dict[str, bool] = {}
    raw_styles = raw_reply.get("styles")
    if isinstance(raw_styles, list):
        for raw_style in raw_styles:
            style = _request_table(raw_style)
            if style is None:
                continue
            style_id = style.get("id")
            style_enabled = style.get("enabled")
            if isinstance(style_id, str) and isinstance(style_enabled, bool):
                style_patch[style_id] = style_enabled
    reply_style_tables = _apply_reply_style_enabled_patch(
        cfg=existing_cfg,
        definitions=state.config.reply_agent_definitions,
        patch=style_patch,
    )
    if merged["reply_enabled"] and not any(toml_bool(table.get("enabled"), True) for table in reply_style_tables):
        raise SettingsPatchError("返答案のスタイルは最低1つ有効にしてください")

    existing_reply = toml_table(existing_cfg.get("reply"))
    default_style = (
        toml_str(raw_reply.get("default_style"), "")
        or (toml_str(existing_reply.get("default_style"), "") if existing_reply else "")
        or "standard"
    )
    reply_style_values: list[TomlValue] = [table for table in reply_style_tables]
    reply_section: TomlTable = {
        "enabled": merged["reply_enabled"],
        "auto_generate": merged["reply_auto_generate"],
        "default_style": default_style,
        "styles": reply_style_values,
    }
    return merged, reply_style_tables, reply_section


def _config_sections(
    body: TomlTable,
    merged_agent_settings: "RuntimeAgentSettings",
    reply_section: TomlTable | None,
) -> tuple[TomlTable, list[str] | None]:
    sections: TomlTable = {}
    for section in ("stt", "audio", "ollama"):
        values = _request_table(body.get(section))
        if values:
            sections[section] = values
    context = _request_table(body.get("context"))
    if context:
        sections["context"] = context
    if "usage_budget" in body:
        sections["usage_budget"] = _request_table(body.get("usage_budget")) or {}
    if "recording_retention" in body:
        sections["recording_retention"] = _request_table(body.get("recording_retention")) or {}
    if "agents" in body:
        sections["agents"] = {"info_enabled": merged_agent_settings["info_enabled"]}
    if reply_section is not None:
        sections["reply"] = reply_section

    acp = _request_table(body.get("acp"))
    raw_command = acp.get("command") if acp is not None else None
    acp_command = _toml_string_list(raw_command)
    if acp_command is not None:
        sections["ai"] = {}
    return sections, acp_command


def _write_config_sections(
    *,
    store: SettingsStore,
    sections: TomlTable,
    acp_command: list[str] | None,
) -> None:
    with store.locked():
        existing_cfg = store.load_config()
        if acp_command is not None:
            ai = toml_table(existing_cfg.get("ai")) or {}
            routes = toml_table(ai.get("routes")) or {}
            acp = toml_table(routes.get("acp")) or {}
            acp_command_values: list[TomlValue] = [argument for argument in acp_command]
            acp["runtime"] = "acp"
            acp["command"] = acp_command_values
            routes["acp"] = acp
            ai["routes"] = routes
            existing_cfg["ai"] = ai

        for section, values in sections.items():
            if section == "ai":
                continue
            if section == "recording_retention":
                existing_cfg[section] = values
                continue
            existing = existing_cfg.get(section)
            if isinstance(existing, dict) and isinstance(values, dict):
                incoming = toml_table(values) or {}
                if section == "stt":
                    for legacy_key, canonical_key in LEGACY_STT_KEYS.items():
                        if legacy_key in existing and canonical_key not in existing and canonical_key not in incoming:
                            existing[canonical_key] = existing[legacy_key]
                        _ = existing.pop(legacy_key, None)
                existing.update(incoming)
            else:
                existing_cfg[section] = values

        if "reply" in sections:
            _ = existing_cfg.pop("reply_agents", None)
            agents_section = existing_cfg.get("agents")
            if isinstance(agents_section, dict):
                _ = agents_section.pop("reply_enabled", None)
                _ = agents_section.pop("reply_auto_generate", None)
                _ = agents_section.pop("reply_main", None)
                _ = agents_section.pop("reply_polite", None)
        flatten_ai_tables(existing_cfg)
        store.write_sectioned_toml(store.config_path, existing_cfg)


async def save_settings(
    *,
    body: TomlTable,
    state: "AppState",
    store: SettingsStore,
    event_bus: EventBus,
    secret_store: TransactionalSecretStore,
) -> SaveSettingsResult:
    """Validate, merge, and transactionally persist a sparse settings patch."""
    ollama = _request_table(body.get("ollama"))
    ollama_override = (
        toml_str(ollama.get("base_url"), state.config.ollama_base_url) if ollama is not None and ollama else None
    )
    merged_agent_settings, reply_style_tables, reply_section = _merge_agent_settings(body, state, store)

    updates: dict[SecretKey, str] = {}
    secrets = _request_table(body.get("secrets"))
    if secrets is not None:
        for key in SECRET_KEYS:
            value = secrets.get(key)
            if isinstance(value, str) and value:
                updates[key] = value
    raw_deleted_secrets = body.get("delete_secrets")
    deleted_secrets: set[SecretKey] = (
        {key for key in raw_deleted_secrets if _is_secret_key(key)} if isinstance(raw_deleted_secrets, list) else set()
    )

    sections, acp_command = _config_sections(body, merged_agent_settings, reply_section)
    has_mutations = bool(updates or deleted_secrets or sections)
    effective_audio_stt_changed = False

    async with state.audio_lifecycle_lock:
        try:
            current_audio_stt = normalize_audio_stt_config(
                state.config.stt_config,
                audio_sample_rate=state.config.audio_sample_rate,
                audio_max_session_seconds=state.config.audio_max_session_seconds,
            )
            candidate_audio_stt = _merged_audio_stt_config(body, state)
        except ValueError as exc:
            raise SettingsValidationError(str(exc)) from exc
        effective_audio_stt_changed = candidate_audio_stt != current_audio_stt
        if state.is_running and effective_audio_stt_changed:
            raise AudioSettingsLockedError

        affected_secret_keys: set[SecretKey] = set(updates) | deleted_secrets
        secret_snapshot = secret_store.snapshot(affected_secret_keys) if affected_secret_keys else None
        try:
            if updates:
                secret_store.set_secrets({str(key): value for key, value in updates.items()})
            for key in deleted_secrets:
                secret_store.delete(key)
            if sections:
                _write_config_sections(store=store, sections=sections, acp_command=acp_command)
        except Exception as original_error:
            if secret_snapshot is not None:
                try:
                    secret_store.restore(secret_snapshot)
                except SecretRollbackError as rollback_error:
                    raise rollback_error from original_error
                except Exception as rollback_error:
                    raise SecretRollbackError((rollback_error,)) from original_error
            raise

        if has_mutations and effective_audio_stt_changed:
            await event_bus.publish(ConfigChanged(audio_lifecycle_lock_held=True))

    if has_mutations and not effective_audio_stt_changed:
        await event_bus.publish(ConfigChanged())

    return SaveSettingsResult(
        merged_agent_settings=merged_agent_settings,
        ollama_base_url_override=ollama_override,
        reply_style_tables=reply_style_tables,
    )


def write_ai_assignments(store: SettingsStore, assignments: AiRouteAssignments) -> None:
    """Persist a full route-assignment replacement without losing route config."""
    with store.locked():
        cfg = store.load_config()
        flatten_ai_tables(cfg, assignments)
        store.write_sectioned_toml(store.config_path, cfg)
