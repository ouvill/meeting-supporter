"""Behavior contracts for public AI route catalog selection."""

from __future__ import annotations

import unittest
from collections.abc import Iterable
from typing import override

from app.agents.route_catalog import RouteCatalog, RouteProbeStatus
from app.core.config import AiRouteAssignments, RouteDefinition
from app.core.protocols import SecretStore


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
    async def test_read_assigned_route_probes_only_the_requested_assignment(self) -> None:
        managed_calls = 0
        ollama_calls = 0

        async def probe_managed() -> RouteProbeStatus:
            nonlocal managed_calls
            managed_calls += 1
            return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")

        async def probe_ollama() -> RouteProbeStatus:
            nonlocal ollama_calls
            ollama_calls += 1
            return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")

        catalog = RouteCatalog(
            providers=[],
            routes=[RouteDefinition(id="managed", runtime="managed")],
            assignments=AiRouteAssignments(reply="managed"),
            secret_store=_SecretStore(),
            managed_status=probe_managed,
            ollama_status=probe_ollama,
        )

        route = await catalog.read_assigned_route("reply")
        unassigned = await catalog.read_assigned_route("minutes")

        self.assertIsNotNone(route)
        self.assertEqual("managed", route.id if route is not None else None)
        self.assertIn("reply", route.capabilities if route is not None else ())
        self.assertIsNone(unassigned)
        self.assertEqual(1, managed_calls)
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
