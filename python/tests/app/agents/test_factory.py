"""Behavior tests for schema-v2 AI runtime composition."""

from __future__ import annotations

import unittest
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import cast, override

from app.agents.factory import AgentBundle, AgentRouteError, build_agents
from app.agents.models import InfoAgentRuntime, MinutesAgentRuntime, ReplyAgentDefinition
from app.core.config import AiRouteAssignments, RouteDefinition
from app.core.protocols import SecretStore
from app.core.state import AppState
from app.services.usage_logger import UsageLogger


class _SecretStore(SecretStore):
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


class BuildAgentsRouteContractTest(unittest.TestCase):
    def _build(
        self,
        *,
        assignments: AiRouteAssignments,
        routes: list[RouteDefinition],
        external_minutes_factories: Mapping[str, Callable[[RouteDefinition], MinutesAgentRuntime]] | None = None,
        external_info_factories: Mapping[str, Callable[[RouteDefinition], InfoAgentRuntime]] | None = None,
    ) -> AgentBundle:
        async def replace_ai_note(old: str, new: str) -> str:
            _ = (old, new)
            return ""

        return build_agents(
            state=cast(AppState, object()),
            providers=[],
            routes=routes,
            assignments=assignments,
            secret_store=_SecretStore(),
            context_dir=Path("/tmp"),
            usage_logger=UsageLogger(Path("/tmp/route-contract-usage.jsonl")),
            mcp_servers=[],
            reply_agent_definitions=[
                ReplyAgentDefinition(
                    id="standard",
                    label="標準",
                    enabled=True,
                    priority=10,
                    instruction="短く答えてください。",
                )
            ],
            replace_ai_note=replace_ai_note,
            external_minutes_factories=external_minutes_factories,
            external_info_factories=external_info_factories,
        )

    def test_unassigned_routes_leave_all_ai_runtimes_optional(self) -> None:
        """An empty route assignment must keep meeting/STT composition usable without AI runtimes."""
        bundle = self._build(assignments=AiRouteAssignments(), routes=[])

        self.assertEqual([], bundle.reply_agent_specs)
        self.assertIsNone(bundle.info_runtime)
        self.assertIsNone(bundle.minutes_runtime)

    def test_codex_info_assignment_requires_a_configured_model(self) -> None:
        with self.assertRaises(AgentRouteError) as raised:
            _ = self._build(
                assignments=AiRouteAssignments(info="codex"),
                routes=[RouteDefinition(id="codex", runtime="codex-app-server")],
            )

        self.assertEqual("CODEX_MODEL_NOT_CONFIGURED", raised.exception.code)

    def test_managed_reply_assignment_is_not_silently_emulated(self) -> None:
        """A managed route without the native session bridge must fail rather than fall back."""
        with self.assertRaises(AgentRouteError) as raised:
            _ = self._build(
                assignments=AiRouteAssignments(reply="managed"),
                routes=[RouteDefinition(id="managed", runtime="managed")],
            )

        self.assertEqual("MANAGED_RUNTIME_NOT_CONNECTED", raised.exception.code)

    def test_codex_reply_assignment_rejects_a_route_without_a_model_instead_of_falling_back(self) -> None:
        """A selected Codex runtime cannot silently choose a model when the route configuration is incomplete."""
        with self.assertRaises(AgentRouteError) as raised:
            _ = self._build(
                assignments=AiRouteAssignments(reply="codex"),
                routes=[RouteDefinition(id="codex", runtime="codex-app-server")],
            )

        self.assertEqual("CODEX_MODEL_NOT_CONFIGURED", raised.exception.code)

    def test_codex_info_assignment_builds_its_complete_note_runtime(self) -> None:
        info_runtime = cast(InfoAgentRuntime, object())

        bundle = self._build(
            assignments=AiRouteAssignments(info="codex"),
            routes=[RouteDefinition(id="codex", runtime="codex-app-server", model="gpt-5.6-luna")],
            external_info_factories={"codex": lambda _route: info_runtime},
        )

        self.assertIs(info_runtime, bundle.info_runtime)
        self.assertIsNone(bundle.minutes_runtime)

    def test_codex_info_assignment_without_its_runtime_fails_closed(self) -> None:
        with self.assertRaises(AgentRouteError) as raised:
            _ = self._build(
                assignments=AiRouteAssignments(info="codex"),
                routes=[RouteDefinition(id="codex", runtime="codex-app-server", model="gpt-5.6-luna")],
            )

        self.assertEqual("CODEX_RUNTIME_NOT_CONNECTED", raised.exception.code)

    def test_codex_minutes_assignment_builds_its_explicit_minutes_runtime(self) -> None:
        """Codex minutes uses its dedicated runtime injection rather than reply or Pydantic AI fallback paths."""
        minutes_runtime = cast(MinutesAgentRuntime, object())

        bundle = self._build(
            assignments=AiRouteAssignments(minutes="codex"),
            routes=[RouteDefinition(id="codex", runtime="codex-app-server", model="gpt-5.6-luna")],
            external_minutes_factories={"codex": lambda _route: minutes_runtime},
        )

        self.assertIs(minutes_runtime, bundle.minutes_runtime)
        self.assertEqual([], bundle.reply_agent_specs)

    def test_codex_minutes_assignment_without_its_runtime_fails_closed(self) -> None:
        """A selected Codex minutes route cannot borrow a reply runtime when its own peer is unavailable."""
        with self.assertRaises(AgentRouteError) as raised:
            _ = self._build(
                assignments=AiRouteAssignments(minutes="codex"),
                routes=[RouteDefinition(id="codex", runtime="codex-app-server", model="gpt-5.6-luna")],
            )

        self.assertEqual("CODEX_RUNTIME_NOT_CONNECTED", raised.exception.code)


if __name__ == "__main__":
    _ = unittest.main()
