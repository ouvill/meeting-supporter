"""Pure transformations for persisted settings TOML data."""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import TypeGuard

from app.core.config import AiRouteAssignments
from app.core.types import TomlTable, TomlValue

LEGACY_STT_KEYS: dict[str, str] = {
    "no_speech_threshold": "soft_no_speech_threshold",
    "log_prob_threshold": "soft_logprob_threshold",
    "compression_ratio_threshold": "soft_compression_ratio_threshold",
    "hallucination_phrase_blocklist": "suspicious_phrases",
}

STT_PUBLIC_KEYS = frozenset(
    {
        "backend",
        "whisper_model",
        "deepgram_model",
        "openai_model",
        "vosk_model_path",
        "language",
        "vad_engine",
        "vad_sensitivity",
        "silence_duration",
        "vad_aggressiveness",
        "device",
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
        "suspicious_phrases",
    }
)


def toml_str(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def toml_number(value: object, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def toml_int(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def toml_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _is_object_list(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, list)


def _is_object_dict(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, dict)


def _is_toml_value(value: object) -> TypeGuard[TomlValue]:
    if isinstance(value, (str, int, float, bool)):
        return True
    if _is_object_list(value):
        return all(_is_toml_value(item) for item in value)
    if _is_object_dict(value):
        return all(isinstance(key, str) and _is_toml_value(item) for key, item in value.items())
    return False


def toml_table(value: object) -> TomlTable | None:
    if not _is_object_dict(value):
        return None
    result: TomlTable = {}
    for key, item in value.items():
        if _is_toml_value(item):
            result[str(key)] = item
    return result


def toml_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def canonical_stt_section(section: TomlTable | None) -> TomlTable:
    """Filter persisted STT data to the typed API and map supported legacy keys."""
    if section is None:
        return {}
    normalized = {key: value for key, value in section.items() if key in STT_PUBLIC_KEYS}
    for legacy_key, canonical_key in LEGACY_STT_KEYS.items():
        if canonical_key not in normalized and legacy_key in section:
            normalized[canonical_key] = section[legacy_key]
    return normalized


def flatten_ai_tables(
    cfg: TomlTable,
    assignments: AiRouteAssignments | None = None,
) -> None:
    """Convert nested parsed AI tables to dotted sections for the TOML writer."""
    raw_ai = cfg.pop("ai", None)
    ai = toml_table(raw_ai) or {}
    raw_assignments = ai.get("assignments")
    existing_assignments = toml_table(raw_assignments) or {}
    raw_routes = ai.get("routes")
    route_tables = toml_table(raw_routes) or {}

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
        route_table = toml_table(raw_route)
        if route_table is None:
            continue
        scalar_route = {key: value for key, value in route_table.items() if key != "env"}
        if scalar_route:
            cfg[f"ai.routes.{route_id}"] = scalar_route
        env = toml_table(route_table.get("env"))
        if env is not None:
            cfg[f"ai.routes.{route_id}.env"] = env
