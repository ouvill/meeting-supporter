"""Behavior tests for schema-v2 AI runtime composition."""

from __future__ import annotations

import unittest
from collections.abc import Iterable
from pathlib import Path
from typing import cast, override

from app.agents.factory import AgentBundle, AgentRouteError, build_agents
from app.agents.models import ReplyAgentDefinition
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
    ) -> AgentBundle:

        return build_agents(
            state=cast(AppState, object()),
            providers=[],
            routes=routes,
            assignments=assignments,
            secret_store=_SecretStore(),
            usage_logger=UsageLogger(Path("/tmp/route-contract-usage.jsonl")),
            reply_agent_definitions=[
                ReplyAgentDefinition(
                    id="standard",
                    label="標準",
                    enabled=True,
                    priority=10,
                    instruction="短く答えてください。",
                )
            ],
        )

    def test_unassigned_routes_leave_all_ai_runtimes_optional(self) -> None:
        """An empty route assignment must keep meeting/STT composition usable without AI runtimes."""
        bundle = self._build(assignments=AiRouteAssignments(), routes=[])

        self.assertEqual([], bundle.reply_agent_specs)

    def test_managed_reply_assignment_is_not_silently_emulated(self) -> None:
        """A managed route without the native session bridge must fail rather than fall back."""
        with self.assertRaises(AgentRouteError) as raised:
            _ = self._build(
                assignments=AiRouteAssignments(reply="managed"),
                routes=[RouteDefinition(id="managed", runtime="managed")],
            )

        self.assertEqual("MANAGED_RUNTIME_NOT_CONNECTED", raised.exception.code)


if __name__ == "__main__":
    _ = unittest.main()
