# pyright: reportPrivateUsage=false
"""Tests for ConfigLoader reply_agent_definitions parsing and config loading."""

import os
import unittest
from pathlib import Path
from typing import cast, override
from unittest.mock import patch

from app.agents.prompts import REPLY_BASE_INSTRUCTION, REPLY_STYLE_POLITE
from app.core.config import AgentSettings
from app.core.types import TomlTable
from app.services.config_loader import ConfigLoader, UnsupportedAiConfigError
from app.services.settings_store import SettingsStore

_LEGACY_AGENT_SETTINGS: AgentSettings = {
    "reply_enabled": True,
    "reply_auto_generate": False,
    "info_enabled": True,
}


def _toml_table(**kwargs: object) -> TomlTable:
    return cast(TomlTable, dict(kwargs))


class ParseReplyAgentDefinitionsTest(unittest.TestCase):
    def test_from_settings_store_reads_target_reply_section(self) -> None:
        config: TomlTable = cast(
            TomlTable,
            {
                "agents": {"info_enabled": False},
                "reply": {
                    "enabled": False,
                    "auto_generate": True,
                    "styles": [
                        {
                            "id": "custom",
                            "label": "Custom",
                            "enabled": False,
                            "priority": 5,
                            "instruction": "Be custom",
                        }
                    ],
                },
            },
        )

        loader = ConfigLoader.from_settings_store(_dummy_settings_store(config=config))

        self.assertEqual(
            {"reply_enabled": False, "reply_auto_generate": True, "info_enabled": False},
            loader.agent_settings,
        )
        self.assertEqual(1, len(loader.reply_agent_definitions))
        d = loader.reply_agent_definitions[0]
        self.assertEqual("custom", d.id)
        self.assertEqual("Custom", d.label)
        self.assertFalse(d.enabled)
        self.assertEqual(5, d.priority)
        self.assertEqual("Be custom", d.instruction)

    def test_custom_instruction_is_combined_with_base_reply_instruction(self) -> None:
        cfg = _toml_table(
            reply={
                "styles": [
                    {
                        "id": "polite",
                        "label": "Polite",
                        "enabled": True,
                        "priority": 5,
                        "custom_instruction": REPLY_STYLE_POLITE,
                    },
                ]
            }
        )

        defs = ConfigLoader._parse_reply_agent_definitions(cfg, _LEGACY_AGENT_SETTINGS)

        self.assertEqual(1, len(defs))
        self.assertIn(REPLY_BASE_INSTRUCTION, defs[0].instruction)
        self.assertIn(REPLY_STYLE_POLITE, defs[0].instruction)

    def test_target_reply_styles_override_legacy_reply_agents_and_flags(self) -> None:
        cfg = _toml_table(
            agents={"reply_main": True, "reply_polite": True},
            reply={
                "styles": [
                    {
                        "id": "standard",
                        "label": "Standard",
                        "enabled": False,
                        "priority": 10,
                        "instruction": "Standard",
                    },
                    {
                        "id": "custom",
                        "label": "Custom",
                        "enabled": True,
                        "priority": 5,
                        "instruction": "Custom",
                    },
                ]
            },
            reply_agents=[
                {
                    "id": "legacy",
                    "label": "Legacy",
                    "enabled": True,
                    "priority": 1,
                    "instruction": "Legacy",
                },
            ],
        )

        defs = ConfigLoader._parse_reply_agent_definitions(cfg, _LEGACY_AGENT_SETTINGS)

        self.assertEqual(["standard", "custom"], [d.id for d in defs])
        self.assertEqual(["custom"], [d.id for d in defs if d.enabled])


class FromSettingsStoreSttTest(unittest.TestCase):
    """Tests for STT config loading in ConfigLoader.from_settings_store."""

    def test_whisper_model_from_config(self) -> None:
        """When [stt] whisper_model is set in config, it is used."""
        config: TomlTable = cast(TomlTable, {"stt": {"whisper_model": "tiny"}})
        store = _dummy_settings_store(config=config)
        loader = ConfigLoader.from_settings_store(store)
        self.assertEqual("tiny", loader.stt_config.whisper_model)

    def test_openai_model_from_config(self) -> None:
        config: TomlTable = cast(TomlTable, {"stt": {"backend": "openai", "openai_model": "gpt-4o-mini-transcribe"}})
        loader = ConfigLoader.from_settings_store(_dummy_settings_store(config=config))

        self.assertEqual("openai", loader.stt_config.backend)
        self.assertEqual("gpt-4o-mini-transcribe", loader.stt_config.openai_model)

    def test_silero_is_the_default_vad_without_changing_explicit_webrtc(self) -> None:
        default_loader = ConfigLoader.from_settings_store(_dummy_settings_store(config=cast(TomlTable, {})))
        webrtc_loader = ConfigLoader.from_settings_store(
            _dummy_settings_store(config=cast(TomlTable, {"stt": {"vad_engine": "webrtc"}}))
        )

        self.assertEqual("silero", default_loader.stt_config.vad_engine)
        self.assertEqual("webrtc", webrtc_loader.stt_config.vad_engine)

    def test_legacy_hallucination_blocklist_maps_to_suspicious_phrases(self) -> None:
        config: TomlTable = cast(TomlTable, {"stt": {"hallucination_phrase_blocklist": ["旧フレーズ"]}})
        store = _dummy_settings_store(config=config)
        loader = ConfigLoader.from_settings_store(store)

        self.assertEqual(("旧フレーズ",), loader.stt_config.suspicious_phrases)

    def test_legacy_whisper_thresholds_map_only_to_soft_judge_thresholds(self) -> None:
        config: TomlTable = cast(
            TomlTable,
            {
                "stt": {
                    "no_speech_threshold": 0.4,
                    "log_prob_threshold": -0.7,
                    "compression_ratio_threshold": 1.8,
                }
            },
        )
        store = _dummy_settings_store(config=config)
        loader = ConfigLoader.from_settings_store(store)

        self.assertEqual(1.0, loader.stt_config.decode_no_speech_threshold)
        self.assertEqual(-10.0, loader.stt_config.decode_log_prob_threshold)
        self.assertEqual(10.0, loader.stt_config.decode_compression_ratio_threshold)
        self.assertEqual(0.4, loader.stt_config.soft_no_speech_threshold)
        self.assertEqual(-0.7, loader.stt_config.soft_logprob_threshold)
        self.assertEqual(1.8, loader.stt_config.soft_compression_ratio_threshold)

    def test_new_decode_threshold_keys_override_decode_defaults(self) -> None:
        config: TomlTable = cast(
            TomlTable,
            {
                "stt": {
                    "decode_no_speech_threshold": 0.95,
                    "decode_log_prob_threshold": -8.5,
                    "decode_compression_ratio_threshold": 8.0,
                }
            },
        )
        store = _dummy_settings_store(config=config)
        loader = ConfigLoader.from_settings_store(store)

        self.assertEqual(0.95, loader.stt_config.decode_no_speech_threshold)
        self.assertEqual(-8.5, loader.stt_config.decode_log_prob_threshold)
        self.assertEqual(8.0, loader.stt_config.decode_compression_ratio_threshold)

    def test_rejects_persisted_numbers_outside_postable_ranges(self) -> None:
        cases: tuple[tuple[str, TomlTable, str], ...] = (
            (
                "audio sample rate",
                _toml_table(stt={"vad_engine": "webrtc"}, audio={"sample_rate": 192_001}),
                "audio.sample_rate",
            ),
            ("audio session length", _toml_table(audio={"max_session_seconds": 61}), "audio.max_session_seconds"),
            ("VAD sensitivity", _toml_table(stt={"vad_sensitivity": 0.049}), "stt.vad_sensitivity"),
            ("silence duration", _toml_table(stt={"silence_duration": 5.01}), "stt.silence_duration"),
            ("VAD aggressiveness", _toml_table(stt={"vad_aggressiveness": 4}), "stt.vad_aggressiveness"),
            ("minimum voiced time", _toml_table(stt={"min_voiced_ms": -1}), "stt.min_voiced_ms"),
            ("minimum voiced ratio", _toml_table(stt={"min_voiced_ratio": 1.01}), "stt.min_voiced_ratio"),
            (
                "decode no-speech threshold",
                _toml_table(stt={"decode_no_speech_threshold": 1.01}),
                "stt.decode_no_speech_threshold",
            ),
            (
                "decode compression threshold",
                _toml_table(stt={"decode_compression_ratio_threshold": 0.0}),
                "stt.decode_compression_ratio_threshold",
            ),
            ("hard minimum voiced time", _toml_table(stt={"hard_min_voiced_ms": -1}), "stt.hard_min_voiced_ms"),
            (
                "hard no-speech threshold",
                _toml_table(stt={"hard_no_speech_threshold": 1.01}),
                "stt.hard_no_speech_threshold",
            ),
            (
                "hard compression threshold",
                _toml_table(stt={"hard_compression_ratio_threshold": 0.0}),
                "stt.hard_compression_ratio_threshold",
            ),
            ("soft minimum voiced time", _toml_table(stt={"soft_min_voiced_ms": -1}), "stt.soft_min_voiced_ms"),
            (
                "soft minimum voiced ratio",
                _toml_table(stt={"soft_min_voiced_ratio": 1.01}),
                "stt.soft_min_voiced_ratio",
            ),
            (
                "legacy no-speech threshold",
                _toml_table(stt={"no_speech_threshold": 1.01}),
                "stt.soft_no_speech_threshold",
            ),
            (
                "legacy compression threshold",
                _toml_table(stt={"compression_ratio_threshold": 0.0}),
                "stt.soft_compression_ratio_threshold",
            ),
            ("drop score threshold", _toml_table(stt={"drop_score_threshold": 1.01}), "stt.drop_score_threshold"),
            ("temperature", _toml_table(stt={"temperature": -0.01}), "stt.temperature"),
        )

        for name, config, field_name in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, field_name):
                    _ = ConfigLoader.from_settings_store(_dummy_settings_store(config=config))


class AiRouteConfigurationTest(unittest.TestCase):
    """Schema-v2 AI configuration is strict so stale model strings cannot select a runtime."""

    def test_schema_v2_keeps_nullable_assignments_and_acp_runtime_config_separate(self) -> None:
        config = _toml_table(
            ai={
                "schema_version": 2,
                "assignments": {"reply": "acp", "info": None, "minutes": "openai"},
                "routes": {
                    "acp": {"command": ["agent-command", "--stdio"], "env": {"ACP_TOKEN": "secret-ref"}},
                    "openai": {"model": "gpt-test"},
                },
            }
        )

        loader = ConfigLoader.from_settings_store(_dummy_settings_store(config=config))

        self.assertEqual("acp", loader.ai_assignments.reply)
        self.assertIsNone(loader.ai_assignments.info)
        self.assertEqual("openai", loader.ai_assignments.minutes)
        acp = next(route for route in loader.routes if route.id == "acp")
        self.assertEqual(["agent-command", "--stdio"], acp.command)
        self.assertEqual({"ACP_TOKEN": "secret-ref"}, acp.env)
        openai = next(route for route in loader.routes if route.id == "openai")
        self.assertEqual("gpt-test", openai.model)

    def test_legacy_model_configuration_is_rejected_instead_of_becoming_a_hidden_route(self) -> None:
        cases: tuple[tuple[str, TomlTable], ...] = (
            ("legacy llm section", _toml_table(llm={"model": "openai/gpt-old"})),
            ("legacy assignments", _toml_table(llm_assignments={"reply_model": "openai/gpt-old"})),
        )
        for name, config in cases:
            with self.subTest(name=name):
                with self.assertRaises(UnsupportedAiConfigError):
                    _ = ConfigLoader.from_settings_store(_dummy_settings_store(config=config))

    def test_legacy_model_environment_variable_is_rejected(self) -> None:
        with patch.dict(os.environ, {"LLM_MODEL_REPLY": "openai/gpt-old"}, clear=False):
            with self.assertRaises(UnsupportedAiConfigError):
                _ = ConfigLoader.from_settings_store(
                    _dummy_settings_store(config=_toml_table(ai={"schema_version": 2}))
                )

    def test_codex_route_can_be_explicitly_configured_for_minutes(self) -> None:
        """Codex is a supported standalone minutes route, not a reply-only fallback."""
        config = _toml_table(ai={"schema_version": 2, "assignments": {"minutes": "codex"}})

        loader = ConfigLoader.from_settings_store(_dummy_settings_store(config=config))

        self.assertEqual("codex", loader.ai_assignments.minutes)

    def test_codex_route_can_be_explicitly_configured_for_info(self) -> None:
        config = _toml_table(ai={"schema_version": 2, "assignments": {"info": "codex"}})

        loader = ConfigLoader.from_settings_store(_dummy_settings_store(config=config))

        self.assertEqual("codex", loader.ai_assignments.info)

    def test_managed_and_acp_routes_remain_unsupported_for_info(self) -> None:
        for route_id in ("managed", "acp"):
            with self.subTest(route_id=route_id):
                config = _toml_table(ai={"schema_version": 2, "assignments": {"info": route_id}})
                with self.assertRaises(UnsupportedAiConfigError):
                    _ = ConfigLoader.from_settings_store(_dummy_settings_store(config=config))

    def test_runtime_command_under_a_provider_is_rejected(self) -> None:
        config = _toml_table(
            providers={
                "invalid": {
                    "kind": "openai-compatible",
                    "data_location": "external",
                    "command": ["agent-command"],
                }
            }
        )

        with self.assertRaises(UnsupportedAiConfigError):
            _ = ConfigLoader.from_settings_store(_dummy_settings_store(config=config))


def _dummy_settings_store(*, config: TomlTable | None = None) -> SettingsStore:
    """Create a minimal SettingsStore-like object for testing.

    Returns a ``SettingsStore`` subclass with a ``load_config`` method
    returning the given config (or an empty dict) and a dummy ``cfg_get``
    that performs safe traversal.
    """
    resolved_config: TomlTable = config if config is not None else {}

    class _DummyStore(SettingsStore):
        @override
        def load_config(self) -> TomlTable:
            return resolved_config

        @staticmethod
        @override
        def cfg_get(cfg: TomlTable, section: str, key: str, default: object) -> object:
            raw = cfg.get(section)
            if isinstance(raw, dict):
                return raw.get(key, default)
            return default

    return _DummyStore(
        config_path=Path("/nonexistent/config.toml"),
        default_config_path=Path("/nonexistent/default.toml"),
    )


if __name__ == "__main__":
    _ = unittest.main()
