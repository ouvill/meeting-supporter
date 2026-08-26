import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeGuard, TypeVar, cast

from app.core.types import TomlTable

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

from platformdirs import user_data_path
from pydantic_ai.mcp import load_mcp_toolsets
from pydantic_ai.toolsets import AbstractToolset

from app.agents.models import ReplyAgentDefinition
from app.agents.prompts import (
    REPLY_INSTRUCTION_MAIN,
    build_reply_instruction,
)
from app.agents.provider_registry import ProviderRegistry
from app.agents.route_catalog import BUILT_IN_ROUTE_IDS
from app.core.config import (
    DEFAULT_SUSPICIOUS_PHRASES,
    AgentSettings,
    AiRouteAssignments,
    DataLocation,
    ProviderDefinition,
    ProviderKind,
    RouteDefinition,
    RouteRuntime,
    SttConfig,
    UsageBudgetConfig,
    _read_agent_settings,
)
from app.services.settings_store import SettingsStore

_PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class UnsupportedAiConfigError(ValueError):
    """Raised when a pre-v2 AI assignment would otherwise be used silently."""


def _parse_provider_kind(value: object) -> ProviderKind:
    valid_kinds: set[ProviderKind] = {
        "google-gla",
        "google-vertex",
        "openai",
        "openai-chat",
        "openai-responses",
        "anthropic",
        "openai-compatible",
        "ollama",
    }
    if value in valid_kinds:
        return value  # type: ignore[return-value]
    raise ValueError(f"未知の provider kind です: {value!r}")


def _parse_data_location(value: object) -> DataLocation:
    valid: set[DataLocation] = {"cloud", "external", "local", "unknown"}
    if value in valid:
        return value  # type: ignore[return-value]
    raise ValueError(f"未知の data_location です: {value!r}")


def _coerce_str_list(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        result: list[str] = []
        for v in value:  # pyright: ignore[reportUnknownVariableType]
            if v is not None:
                result.append(str(v))  # pyright: ignore[reportUnknownArgumentType]
        return result
    return None


def _coerce_str_dict(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        result: dict[str, str] = {}
        for k, v in value.items():  # pyright: ignore[reportUnknownVariableType]
            if k is not None and v is not None:
                result[str(k)] = str(v)  # pyright: ignore[reportUnknownArgumentType]
        return result
    return None


def _coerce_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _parse_providers(cfg: TomlTable) -> list[ProviderDefinition]:
    raw = cfg.get("providers")
    if not isinstance(raw, dict):
        return []
    user_providers: list[ProviderDefinition] = []
    for provider_id, table in raw.items():
        provider_id = str(provider_id)
        if not _PROVIDER_ID_RE.match(provider_id):
            logger.warning("無効な provider id です: %s", provider_id)
            continue
        if not isinstance(table, dict):
            logger.warning("providers.%s の設定がテーブルではありません", provider_id)
            continue
        # table is already known to be a dict from the isinstance check above.
        table = cast(TomlTable, table)
        label_value = table.get("label", provider_id)
        label = provider_id if not isinstance(label_value, str) or not label_value else label_value
        try:
            kind = _parse_provider_kind(table.get("kind"))
        except ValueError as e:
            logger.warning("providers.%s の kind エラー: %s", provider_id, e)
            continue
        try:
            data_location = _parse_data_location(table.get("data_location", "unknown"))
        except ValueError as e:
            logger.warning("providers.%s の data_location エラー: %s", provider_id, e)
            continue
        if table.get("command") is not None or table.get("env") is not None:
            raise UnsupportedAiConfigError(
                f"providers.{provider_id} に runtime 設定は置けません。[ai.routes.{provider_id}] を使用してください"
            )
        base_url = _coerce_optional_str(table.get("base_url"))
        key_ref = _coerce_optional_str(table.get("key_ref"))
        models = _coerce_str_list(table.get("models"))
        experimental = bool(table.get("experimental", False))
        user_providers.append(
            ProviderDefinition(
                id=provider_id,
                label=label,
                kind=kind,
                base_url=base_url,
                key_ref=key_ref,
                models=models,
                data_location=data_location,
                experimental=experimental,
            )
        )
    return user_providers


_DEFAULT_ROUTE_MODELS: dict[str, str] = {
    "codex": "gpt-5.6-luna",
    "ollama": "qwen3",
    "gemini": "gemini-3.1-flash-lite",
    "openai": "gpt-5.4-mini",
    "anthropic": "claude-haiku-4-5-20251001",
}

_ROUTE_RUNTIMES: dict[str, RouteRuntime] = {
    "managed": "managed",
    "codex": "codex-app-server",
    "acp": "acp",
    "ollama": "pydantic-ai",
    "gemini": "pydantic-ai",
    "openai": "pydantic-ai",
    "anthropic": "pydantic-ai",
}


def _parse_ai_config(cfg: TomlTable) -> tuple[AiRouteAssignments, list[RouteDefinition]]:
    legacy_sections = [name for name in ("llm", "llm_assignments") if name in cfg]
    legacy_env = [
        name for name in ("LLM_MODEL", "LLM_MODEL_REPLY", "LLM_MODEL_INFO", "LLM_MODEL_MINUTES") if os.getenv(name)
    ]
    if legacy_sections or legacy_env:
        found = ", ".join([*[f"[{name}]" for name in legacy_sections], *legacy_env])
        raise UnsupportedAiConfigError(
            f"旧AI設定 ({found}) はschema v2では利用できません。[ai.assignments] のroute idへ移行してください"
        )

    raw_ai = cfg.get("ai")
    if raw_ai is not None and not isinstance(raw_ai, dict):
        raise UnsupportedAiConfigError("[ai] はTOMLテーブルで指定してください")
    ai = cast(TomlTable, raw_ai) if isinstance(raw_ai, dict) else {}
    schema_version = ai.get("schema_version", 2)
    if schema_version != 2:
        raise UnsupportedAiConfigError(f"未対応のAI設定schema_versionです: {schema_version!r}")

    raw_assignments = ai.get("assignments")
    if raw_assignments is not None and not isinstance(raw_assignments, dict):
        raise UnsupportedAiConfigError("[ai.assignments] はTOMLテーブルで指定してください")
    assignment_table = cast(TomlTable, raw_assignments) if isinstance(raw_assignments, dict) else {}

    def optional_route(key: str) -> str | None:
        value = assignment_table.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise UnsupportedAiConfigError(f"ai.assignments.{key} はroute idまたは省略で指定してください")
        return value

    assignments = AiRouteAssignments(
        reply=optional_route("reply"),
        info=optional_route("info"),
        minutes=optional_route("minutes"),
    )
    assigned = {
        route_id for route_id in (assignments.reply, assignments.info, assignments.minutes) if route_id is not None
    }
    unknown_assignments = sorted(assigned - set(BUILT_IN_ROUTE_IDS))
    if unknown_assignments:
        raise UnsupportedAiConfigError(f"未知のAI route idです: {', '.join(unknown_assignments)}")
    for use_case, route_id in (("info", assignments.info), ("minutes", assignments.minutes)):
        unsupported = ("managed", "acp")
        if route_id in unsupported:
            raise UnsupportedAiConfigError(f"route '{route_id}' は{use_case}をサポートしません")

    raw_routes = ai.get("routes")
    if raw_routes is not None and not isinstance(raw_routes, dict):
        raise UnsupportedAiConfigError("[ai.routes] はTOMLテーブルで指定してください")
    route_tables = cast(dict[str, object], raw_routes) if isinstance(raw_routes, dict) else {}
    unknown_routes = sorted(set(route_tables) - set(BUILT_IN_ROUTE_IDS))
    if unknown_routes:
        raise UnsupportedAiConfigError(f"未知のAI route設定です: {', '.join(unknown_routes)}")

    routes: list[RouteDefinition] = []
    for route_id in BUILT_IN_ROUTE_IDS:
        raw_route = route_tables.get(route_id)
        if raw_route is not None and not isinstance(raw_route, dict):
            raise UnsupportedAiConfigError(f"[ai.routes.{route_id}] はTOMLテーブルで指定してください")
        table = cast(TomlTable, raw_route) if isinstance(raw_route, dict) else {}
        runtime = table.get("runtime", _ROUTE_RUNTIMES[route_id])
        if runtime != _ROUTE_RUNTIMES[route_id]:
            raise UnsupportedAiConfigError(f"ai.routes.{route_id}.runtime は {_ROUTE_RUNTIMES[route_id]!r} 固定です")
        runtime = cast(RouteRuntime, runtime)
        provider_id = route_id if runtime == "pydantic-ai" else None
        model_value = table.get("model", _DEFAULT_ROUTE_MODELS.get(route_id))
        model = model_value if isinstance(model_value, str) and model_value else None
        command = _coerce_str_list(table.get("command"))
        env = _coerce_str_dict(table.get("env"))
        routes.append(
            RouteDefinition(
                id=route_id,
                runtime=runtime,
                provider_id=provider_id,
                model=model,
                command=command,
                env=env,
            )
        )
    return assignments, routes


def _is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _get_str(table: object, key: str, default: str) -> str:
    if not _is_object_mapping(table):
        return default
    value = table.get(key)
    if isinstance(value, str):
        return value
    return default


@dataclass
class ConfigLoader:
    settings_store: SettingsStore
    user_data_dir: Path
    context_dir: Path
    ollama_base_url: str
    # STT
    stt_backend: str
    stt_config: SttConfig
    # Audio
    audio_sample_rate: int
    audio_max_session_seconds: int
    audio_chunk_size: int
    # Agents
    agent_settings: AgentSettings
    reply_agent_definitions: list[ReplyAgentDefinition] = field(default_factory=list)
    # MCP
    mcp_servers: list[AbstractToolset[None]] = field(default_factory=list)
    # AI schema v2
    providers: list[ProviderDefinition] = field(default_factory=list)
    routes: list[RouteDefinition] = field(default_factory=list)
    ai_assignments: AiRouteAssignments = field(default_factory=AiRouteAssignments)
    usage_budget: UsageBudgetConfig = field(default_factory=UsageBudgetConfig)

    @classmethod
    def from_settings_store(cls, store: SettingsStore) -> "ConfigLoader":
        cfg = store.load_config()

        # user data dir
        user_data_dir = (
            Path(os.environ["APP_DATA_DIR"])
            if os.getenv("APP_DATA_DIR")
            else user_data_path("net.ouvill.meeting-supporter", appauthor=False, roaming=True)
        )
        user_data_dir.mkdir(parents=True, exist_ok=True)

        def cfg_get(section: str, key: str, default: _T) -> _T:
            return store.cfg_get(cfg, section, key, default)

        # Ollama model-provider base URL
        _ollama_raw = cfg.get("ollama")
        _ollama_cfg = _ollama_raw if isinstance(_ollama_raw, dict) else {}
        ollama_base_url = os.getenv("OLLAMA_BASE_URL")
        if not ollama_base_url:
            _ollama_base_val = _ollama_cfg.get("base_url")
            ollama_base_url = _ollama_base_val if isinstance(_ollama_base_val, str) else None
        if not ollama_base_url:
            ollama_base_url = "http://localhost:11434/v1"
        # Propagate to environment so Pydantic AI's OllamaProvider (used
        # internally by Agent("ollama:...")) can discover the base URL.
        os.environ["OLLAMA_BASE_URL"] = ollama_base_url

        providers = (
            ProviderRegistry.from_user_providers(_parse_providers(cfg)).with_ollama_base_url(ollama_base_url).as_list()
        )
        ai_assignments, routes = _parse_ai_config(cfg)

        # STT
        stt_backend = cfg_get("stt", "backend", "whisper")
        configured_stt_language = cfg_get("stt", "language", "ja")
        stt_language = "ja" if stt_backend == "reazonspeech" else configured_stt_language
        sample_rate = cfg_get("audio", "sample_rate", 16000)
        chunk_size = int(sample_rate * 0.1)

        legacy_min_voiced_ms = cfg_get("stt", "min_voiced_ms", 240)
        legacy_min_voiced_ratio = cfg_get("stt", "min_voiced_ratio", 0.35)
        legacy_min_rms_dbfs = cfg_get("stt", "min_rms_dbfs", -45.0)
        legacy_no_speech_threshold = cfg_get("stt", "no_speech_threshold", 0.6)
        legacy_log_prob_threshold = cfg_get("stt", "log_prob_threshold", -1.0)
        legacy_compression_ratio_threshold = cfg_get("stt", "compression_ratio_threshold", 2.4)
        legacy_phrases = cfg_get(
            "stt",
            "hallucination_phrase_blocklist",
            list(DEFAULT_SUSPICIOUS_PHRASES),
        )
        remote_url = _get_str(cfg.get("remote_stt"), "url", "ws://localhost:8001/ws/stt")
        remote_token = _get_str(cfg.get("remote_stt"), "token", "")
        stt_config = SttConfig(
            backend=stt_backend,
            whisper_model=cfg_get("stt", "whisper_model", "large-v3-turbo"),
            deepgram_model=cfg_get("stt", "deepgram_model", "nova-3"),
            openai_model=cfg_get("stt", "openai_model", "gpt-4o-transcribe"),
            vosk_model_path=cfg_get("stt", "vosk_model_path", "vosk-model-small-ja-0.22"),
            language=stt_language,
            vad_sensitivity=cfg_get("stt", "vad_sensitivity", 0.4),
            silence_duration=cfg_get("stt", "silence_duration", 0.8),
            vad_aggressiveness=cfg_get("stt", "vad_aggressiveness", 2),
            device=cfg_get("stt", "device", "auto"),
            remote_url=remote_url,
            remote_token=remote_token,
            sample_rate=sample_rate,
            chunk_size=chunk_size,
            min_voiced_ms=legacy_min_voiced_ms,
            min_voiced_ratio=legacy_min_voiced_ratio,
            min_rms_dbfs=legacy_min_rms_dbfs,
            decode_no_speech_threshold=cfg_get("stt", "decode_no_speech_threshold", 1.0),
            decode_log_prob_threshold=cfg_get("stt", "decode_log_prob_threshold", -10.0),
            decode_compression_ratio_threshold=cfg_get("stt", "decode_compression_ratio_threshold", 10.0),
            hard_min_voiced_ms=cfg_get("stt", "hard_min_voiced_ms", 120),
            hard_no_speech_threshold=cfg_get("stt", "hard_no_speech_threshold", 0.85),
            hard_logprob_threshold=cfg_get("stt", "hard_logprob_threshold", -2.0),
            hard_compression_ratio_threshold=cfg_get("stt", "hard_compression_ratio_threshold", 3.5),
            soft_min_voiced_ms=cfg_get("stt", "soft_min_voiced_ms", legacy_min_voiced_ms),
            soft_min_voiced_ratio=cfg_get("stt", "soft_min_voiced_ratio", legacy_min_voiced_ratio),
            soft_min_rms_dbfs=cfg_get("stt", "soft_min_rms_dbfs", legacy_min_rms_dbfs),
            soft_no_speech_threshold=cfg_get("stt", "soft_no_speech_threshold", legacy_no_speech_threshold),
            soft_logprob_threshold=cfg_get("stt", "soft_logprob_threshold", legacy_log_prob_threshold),
            soft_compression_ratio_threshold=cfg_get(
                "stt",
                "soft_compression_ratio_threshold",
                legacy_compression_ratio_threshold,
            ),
            drop_score_threshold=cfg_get("stt", "drop_score_threshold", 0.65),
            temperature=cfg_get("stt", "temperature", 0.0),
            suspicious_phrases=tuple(cfg_get("stt", "suspicious_phrases", legacy_phrases)),
        )

        usage_budget = UsageBudgetConfig(
            meeting_limit_jpy=float(cfg_get("usage_budget", "meeting_limit_jpy", 0.0)),
            monthly_limit_jpy=float(cfg_get("usage_budget", "monthly_limit_jpy", 0.0)),
        )

        # Audio
        max_session_seconds = cfg_get("audio", "max_session_seconds", 55)

        # MCP servers
        mcp_config_path = user_data_dir / "mcp.json"
        legacy_mcp_path = Path(__file__).parent.parent.parent / "mcp.json"
        mcp_servers = cls._load_mcp_servers(mcp_config_path, legacy_mcp_path)

        # Context directory
        context_dir_override: str = cfg_get("context", "dir_override", "")
        if context_dir_override:
            context_dir = Path(context_dir_override)
            if not context_dir.exists():
                logger.warning("コンテキストディレクトリが存在しません: %s", context_dir)
        else:
            context_dir = user_data_dir / "context"

        agent_settings = _read_agent_settings(cfg)
        reply_agent_definitions = cls._parse_reply_agent_definitions(cfg, agent_settings)

        return cls(
            settings_store=store,
            user_data_dir=user_data_dir,
            context_dir=context_dir,
            ollama_base_url=ollama_base_url,
            providers=providers,
            routes=routes,
            ai_assignments=ai_assignments,
            stt_backend=stt_backend,
            stt_config=stt_config,
            audio_sample_rate=sample_rate,
            audio_max_session_seconds=max_session_seconds,
            audio_chunk_size=chunk_size,
            agent_settings=agent_settings,
            reply_agent_definitions=reply_agent_definitions,
            usage_budget=usage_budget,
            mcp_servers=mcp_servers,
        )

    def reload(self) -> "ConfigLoader":
        """Return a fresh ConfigLoader built from the same SettingsStore."""
        return ConfigLoader.from_settings_store(self.settings_store)

    @staticmethod
    def _parse_reply_agent_definitions(
        cfg: TomlTable,
        agent_settings: AgentSettings,
    ) -> list[ReplyAgentDefinition]:
        del agent_settings

        def parse_entries(raw: object, section_name: str) -> list[ReplyAgentDefinition] | None:
            if not isinstance(raw, list) or not raw:
                return None
            definitions: list[ReplyAgentDefinition] = []
            entries = cast(list[object], raw)
            for entry in entries:
                if not isinstance(entry, dict):
                    logger.warning("%s のエントリが無効です: %s", section_name, type(entry).__name__)
                    continue
                table = cast(TomlTable, entry)
                entry_id = table.get("id")
                if not isinstance(entry_id, str) or not entry_id:
                    logger.warning("%s の id が無効です: %s", section_name, entry_id)
                    continue
                label = table.get("label")
                if not isinstance(label, str) or not label:
                    logger.warning("%s[%s] の label が無効です", section_name, entry_id)
                    continue
                instruction = table.get("instruction")
                if isinstance(instruction, str) and instruction:
                    resolved_instruction = instruction
                else:
                    custom_instruction = table.get("custom_instruction", "")
                    if not isinstance(custom_instruction, str):
                        logger.warning("%s[%s] の custom_instruction が無効です", section_name, entry_id)
                        continue
                    resolved_instruction = build_reply_instruction(custom_instruction)
                enabled = table.get("enabled", True)
                if not isinstance(enabled, bool):
                    enabled = True
                priority = table.get("priority", 100)
                if not isinstance(priority, int) or isinstance(priority, bool):
                    priority = 100
                if table.get("model") is not None:
                    raise UnsupportedAiConfigError(
                        f"{section_name}[{entry_id}] のmodel指定は廃止されました。[ai.assignments] を使用してください"
                    )
                definitions.append(
                    ReplyAgentDefinition(
                        id=entry_id,
                        label=label,
                        enabled=enabled,
                        priority=priority,
                        instruction=resolved_instruction,
                    )
                )
            if not definitions:
                logger.warning("%s の有効なエントリがありません", section_name)
                return None
            return definitions

        reply = cfg.get("reply")
        if isinstance(reply, dict):
            parsed = parse_entries(reply.get("styles"), "reply.styles")
            if parsed is not None:
                return parsed

        return [
            ReplyAgentDefinition(
                id="standard",
                label="標準",
                enabled=True,
                priority=10,
                instruction=REPLY_INSTRUCTION_MAIN,
            )
        ]

    @staticmethod
    def _load_mcp_servers(path: Path, fallback: Path) -> list[AbstractToolset[None]]:
        p = path if path.exists() else fallback
        if not p.exists():
            return []
        try:
            toolsets: list[AbstractToolset[None]] = list(load_mcp_toolsets(p))
            for ts in toolsets:
                logger.info("MCP サーバー登録: %s", getattr(ts, "id", "(unknown)"))
            return toolsets
        except Exception as e:
            logger.warning("mcp.json 読み込みエラー: %s", e)
            return []
