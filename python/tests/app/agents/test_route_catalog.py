"""Behavior contracts for public AI route catalog selection."""

from __future__ import annotations

import unittest
from collections.abc import Iterable
from typing import override

from app.agents.route_catalog import RouteCatalog, RouteProbeStatus
from app.core.config import AiRouteAssignments, RouteDefinition
from app.core.protocols import SecretStore

type RouteCase = tuple[str, str, list[RouteDefinition], RouteProbeStatus, str, bool]


class _SecretStore(SecretStore):
    """No-credential fake matching the route-catalog dependency boundary."""

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


class RouteCatalogSelectionContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_process_backed_assignments_are_selected_only_when_ready(self) -> None:
        """The catalog must never publish an unselectable persisted Codex or ACP route as selected."""
        cases: tuple[RouteCase, ...] = (
            (
                "unready Codex assignment",
                "codex",
                [RouteDefinition(id="codex", runtime="codex-app-server", model="gpt-5.6-luna")],
                RouteProbeStatus(
                    readiness="error",
                    reason_code="CODEX_STATUS_CHECK_FAILED",
                    message="Codexを確認できません。",
                ),
                "error",
                False,
            ),
            (
                "unready ACP assignment",
                "acp",
                [],
                RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。"),
                "setup_required",
                False,
            ),
            (
                "ready Codex assignment",
                "codex",
                [RouteDefinition(id="codex", runtime="codex-app-server", model="gpt-5.6-luna")],
                RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。"),
                "ready",
                True,
            ),
            (
                "ready ACP assignment",
                "acp",
                [RouteDefinition(id="acp", runtime="acp", command=["acp-agent", "--stdio"])],
                RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。"),
                "ready",
                True,
            ),
        )
        for name, route_id, routes, codex_status, expected_readiness, expected_selectable in cases:
            with self.subTest(name=name):

                async def probe_codex(requested_model: str) -> RouteProbeStatus:
                    _ = requested_model
                    return codex_status

                catalog = RouteCatalog(
                    providers=[],
                    routes=routes,
                    assignments=AiRouteAssignments(reply=route_id),
                    secret_store=_SecretStore(),
                    codex_status=probe_codex,
                )

                response = await catalog.read()
                route = next(candidate for candidate in response.routes if candidate.id == route_id)

                self.assertEqual(route_id, response.assignments.reply)
                self.assertEqual(expected_readiness, route.readiness)
                self.assertEqual(expected_selectable, route.selectable)
                self.assertEqual(expected_selectable, route.selected)

    async def test_read_assigned_route_probes_only_the_requested_assignment(self) -> None:
        codex_calls = 0
        ollama_calls = 0

        async def probe_codex(requested_model: str) -> RouteProbeStatus:
            nonlocal codex_calls
            codex_calls += 1
            self.assertEqual("gpt-5.6-luna", requested_model)
            return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")

        async def probe_ollama() -> RouteProbeStatus:
            nonlocal ollama_calls
            ollama_calls += 1
            return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")

        catalog = RouteCatalog(
            providers=[],
            routes=[RouteDefinition(id="codex", runtime="codex-app-server", model="gpt-5.6-luna")],
            assignments=AiRouteAssignments(info="codex"),
            secret_store=_SecretStore(),
            codex_status=probe_codex,
            ollama_status=probe_ollama,
        )

        route = await catalog.read_assigned_route("info")
        unassigned = await catalog.read_assigned_route("minutes")

        self.assertIsNotNone(route)
        self.assertEqual("codex", route.id if route is not None else None)
        self.assertIn("info", route.capabilities if route is not None else ())
        self.assertIsNone(unassigned)
        self.assertEqual(1, codex_calls)
        self.assertEqual(0, ollama_calls)

    async def test_managed_route_presents_monthly_inclusions_without_yen_comparison(self) -> None:
        """The public managed route copy must describe included features rather than API resale value."""
        catalog = RouteCatalog(
            providers=[],
            routes=[],
            assignments=AiRouteAssignments(),
            secret_store=_SecretStore(),
        )

        response = await catalog.read()
        route = next(candidate for candidate in response.routes if candidate.id == "managed")

        self.assertEqual(
            "月額3,000円（税込）。返答案とクラウド音声認識を月額内で利用できます",
            route.description,
        )
        self.assertNotIn("円相当", route.description)


if __name__ == "__main__":
    _ = unittest.main()
