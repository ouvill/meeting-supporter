"""Public AI route catalog and readiness evaluation.

A route chooses a runtime.  A provider only describes a model API.  Keeping
those concepts separate prevents process-backed agents (Codex and ACP) from
leaking into model provider resolution.
"""

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol, cast
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from app.core.config import (
    AiRouteAssignments,
    BillingOwner,
    DataLocation,
    ProviderDefinition,
    RouteAction,
    RouteAvailability,
    RouteCapability,
    RouteDefinition,
    RouteKind,
    RouteReadiness,
    RouteServiceTier,
)
from app.core.protocols import SecretStore

BUILT_IN_ROUTE_IDS = ("managed", "codex", "acp", "ollama", "gemini", "openai", "anthropic")
type AssignableUseCase = Literal["reply", "info", "minutes"]


@dataclass(frozen=True)
class RouteProbeStatus:
    """Safe status supplied by a runtime-specific controller."""

    readiness: RouteReadiness
    reason_code: str
    message: str
    action: RouteAction = "none"
    service_tier: RouteServiceTier | None = None


class ManagedStatusProvider(Protocol):
    """Callable injection boundary for managed entitlement readiness."""

    async def __call__(self) -> RouteProbeStatus: ...


class CodexStatusProvider(Protocol):
    """Callable injection boundary for a configured Codex model readiness probe."""

    async def __call__(self, requested_model: str) -> RouteProbeStatus: ...


class OllamaStatusProvider(Protocol):
    """Callable injection boundary for a non-blocking Ollama status probe."""

    async def __call__(self) -> RouteProbeStatus: ...


@dataclass(frozen=True)
class OllamaHttpStatusProvider:
    """Probe the configured OpenAI-compatible Ollama models endpoint."""

    base_url: str
    model: str | None = None
    timeout_seconds: float = 1.5

    async def __call__(self) -> RouteProbeStatus:
        return await asyncio.to_thread(self._probe)

    def _probe(self) -> RouteProbeStatus:
        request = urllib.request.Request(f"{self.base_url.rstrip('/')}/models", method="GET")
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # pyright: ignore[reportAny]
                if response.status != 200:  # pyright: ignore[reportAny]
                    return RouteProbeStatus(
                        readiness="error",
                        reason_code="OLLAMA_STATUS_CHECK_FAILED",
                        message="Ollamaの利用状態を確認できませんでした。",
                        action="none",
                    )
                payload: object = json.loads(response.read().decode("utf-8"))  # pyright: ignore[reportAny]
        except (urllib.error.URLError, OSError, TimeoutError):
            return RouteProbeStatus(
                readiness="unavailable",
                reason_code="OLLAMA_SERVICE_NOT_RUNNING",
                message="Ollamaが起動していません。",
                action="start",
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return RouteProbeStatus(
                readiness="error",
                reason_code="OLLAMA_INVALID_STATUS_RESPONSE",
                message="Ollamaの利用状態を確認できませんでした。",
                action="none",
            )
        if not isinstance(payload, dict):
            return RouteProbeStatus(
                readiness="error",
                reason_code="OLLAMA_INVALID_STATUS_RESPONSE",
                message="Ollamaの利用状態を確認できませんでした。",
                action="none",
            )
        payload_dict = cast(dict[str, object], payload)
        raw_models = payload_dict.get("data")
        if not isinstance(raw_models, list):
            return RouteProbeStatus(
                readiness="error",
                reason_code="OLLAMA_INVALID_STATUS_RESPONSE",
                message="Ollamaの利用状態を確認できませんでした。",
                action="none",
            )
        models = cast(list[object], raw_models)
        model_ids: set[str] = set()
        for entry in models:
            if isinstance(entry, dict):
                entry_dict = cast(dict[str, object], entry)
                model_id = entry_dict.get("id")
                if isinstance(model_id, str):
                    model_ids.add(model_id)
        model_available = self.model is None or any(
            model_id == self.model or model_id.startswith(f"{self.model}:") for model_id in model_ids
        )
        if not model_ids or not model_available:
            return RouteProbeStatus(
                readiness="setup_required",
                reason_code="OLLAMA_MODEL_NOT_INSTALLED",
                message="選択したOllamaモデルがインストールされていません。",
                action="configure",
            )
        return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")


class RouteReadModel(BaseModel):
    """Secret-free route state returned by the public API."""

    model_config = ConfigDict(frozen=True)  # pyright: ignore[reportUnannotatedClassAttribute]

    id: str
    kind: RouteKind
    label: str
    description: str
    availability: RouteAvailability
    readiness: RouteReadiness
    selectable: bool
    selected: bool
    data_location: DataLocation
    billing_owner: BillingOwner
    capabilities: list[RouteCapability]
    reason_code: str | None
    message: str
    action: RouteAction
    service_tier: RouteServiceTier | None = None


class RouteAssignmentsReadModel(BaseModel):
    model_config = ConfigDict(frozen=True)  # pyright: ignore[reportUnannotatedClassAttribute]

    reply: str | None = None
    info: str | None = None
    minutes: str | None = None

    @classmethod
    def from_config(cls, assignments: AiRouteAssignments) -> "RouteAssignmentsReadModel":
        return cls(reply=assignments.reply, info=assignments.info, minutes=assignments.minutes)


class RouteCatalogResponse(BaseModel):
    model_config = ConfigDict(frozen=True)  # pyright: ignore[reportUnannotatedClassAttribute]

    routes: list[RouteReadModel]
    assignments: RouteAssignmentsReadModel


@dataclass(frozen=True)
class _RouteMetadata:
    kind: RouteKind
    label: str
    description: str
    availability: RouteAvailability
    selectable: bool
    data_location: DataLocation
    billing_owner: BillingOwner
    capabilities: tuple[RouteCapability, ...]


_METADATA: dict[str, _RouteMetadata] = {
    "managed": _RouteMetadata(
        kind="managed",
        label="Meeting Supporter AI",
        description="月額3,000円（税込）。返答案とクラウド音声認識を月額内で利用できます",
        availability="experimental",
        selectable=True,
        data_location="cloud",
        billing_owner="app",
        capabilities=("reply", "stream", "cancel"),
    ),
    "codex": _RouteMetadata(
        kind="subscription_app",
        label="Codex",
        description="ChatGPTでログインした公式Codexを利用します",
        availability="experimental",
        selectable=True,
        data_location="external",
        billing_owner="external_subscription",
        capabilities=("reply", "info", "minutes", "stream", "cancel"),
    ),
    "acp": _RouteMetadata(
        kind="subscription_app",
        label="ACP agent",
        description="設定したACP互換エージェントを利用します",
        availability="experimental",
        selectable=True,
        data_location="unknown",
        billing_owner="external_subscription",
        capabilities=("reply", "stream", "cancel"),
    ),
    "ollama": _RouteMetadata(
        kind="local",
        label="Ollama",
        description="端末上のOllamaモデルを利用します",
        availability="available",
        selectable=True,
        data_location="local",
        billing_owner="none",
        capabilities=("reply", "info", "minutes", "stream"),
    ),
    "gemini": _RouteMetadata(
        kind="byok",
        label="Google Gemini",
        description="ユーザーが設定したGemini API認証情報を利用します",
        availability="available",
        selectable=True,
        data_location="cloud",
        billing_owner="user",
        capabilities=("reply", "info", "minutes", "stream"),
    ),
    "openai": _RouteMetadata(
        kind="byok",
        label="OpenAI API",
        description="ユーザーが設定したOpenAI API認証情報を利用します",
        availability="available",
        selectable=True,
        data_location="cloud",
        billing_owner="user",
        capabilities=("reply", "info", "minutes", "stream"),
    ),
    "anthropic": _RouteMetadata(
        kind="byok",
        label="Anthropic API",
        description="ユーザーが設定したAnthropic API認証情報を利用します",
        availability="available",
        selectable=True,
        data_location="cloud",
        billing_owner="user",
        capabilities=("reply", "info", "minutes", "stream"),
    ),
}


def ollama_data_location(base_url: str) -> DataLocation:
    """Classify the configured Ollama endpoint without assuming it is local."""

    try:
        parsed = urlparse(base_url)
        hostname = parsed.hostname
    except ValueError:
        return "unknown"
    if parsed.scheme not in {"http", "https"} or hostname is None or any(character.isspace() for character in hostname):
        return "unknown"
    if hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
        return "local"
    return "external"


class RouteCatalog:
    """Build the fixed built-in catalog from live configuration state."""

    def __init__(
        self,
        *,
        providers: list[ProviderDefinition],
        routes: list[RouteDefinition],
        assignments: AiRouteAssignments,
        secret_store: SecretStore,
        managed_status: ManagedStatusProvider | None = None,
        codex_status: CodexStatusProvider | None = None,
        ollama_status: OllamaStatusProvider | None = None,
    ) -> None:
        self._providers: dict[str, ProviderDefinition] = {provider.id: provider for provider in providers}
        self._routes: dict[str, RouteDefinition] = {route.id: route for route in routes}
        self._assignments: AiRouteAssignments = assignments
        self._secret_store: SecretStore = secret_store
        self._managed_status: ManagedStatusProvider | None = managed_status
        self._codex_status: CodexStatusProvider | None = codex_status
        ollama_provider = self._providers.get("ollama")
        ollama_route = self._routes.get("ollama")
        self._ollama_status: OllamaStatusProvider | None = (
            ollama_status
            if ollama_status is not None
            else OllamaHttpStatusProvider(
                ollama_provider.base_url,
                model=ollama_route.model if ollama_route is not None else None,
            )
            if ollama_provider is not None and ollama_provider.base_url
            else None
        )

    @property
    def assignments(self) -> AiRouteAssignments:
        return self._assignments

    async def read(self) -> RouteCatalogResponse:
        selected_ids = {
            route_id
            for route_id in (
                self._assignments.reply,
                self._assignments.info,
                self._assignments.minutes,
            )
            if route_id is not None
        }
        routes = [await self._read_route(route_id, route_id in selected_ids) for route_id in BUILT_IN_ROUTE_IDS]
        return RouteCatalogResponse(
            routes=routes,
            assignments=RouteAssignmentsReadModel.from_config(self._assignments),
        )

    async def read_assigned_route(self, use_case: AssignableUseCase) -> RouteReadModel | None:
        """Read and probe only the route assigned to one use case."""
        route_id = (
            self._assignments.reply
            if use_case == "reply"
            else self._assignments.info
            if use_case == "info"
            else self._assignments.minutes
        )
        if route_id is None or route_id not in _METADATA:
            return None
        return await self._read_route(route_id, selected=True)

    async def _read_route(self, route_id: str, selected: bool) -> RouteReadModel:
        metadata = _METADATA[route_id]
        status = await self._status(route_id)
        data_location = metadata.data_location
        if route_id == "ollama":
            provider = self._providers.get("ollama")
            data_location = (
                ollama_data_location(provider.base_url) if provider is not None and provider.base_url else "unknown"
            )
        selectable = metadata.selectable
        if route_id in ("managed", "codex", "acp"):
            selectable = status.readiness == "ready"
        selected = selected and selectable
        return RouteReadModel(
            id=route_id,
            kind=metadata.kind,
            label=metadata.label,
            description=metadata.description,
            availability=metadata.availability,
            readiness=status.readiness,
            selectable=selectable,
            selected=selected,
            data_location=data_location,
            billing_owner=metadata.billing_owner,
            capabilities=list(metadata.capabilities),
            reason_code=status.reason_code or None,
            message=status.message,
            action=status.action,
            service_tier=status.service_tier,
        )

    async def _status(self, route_id: str) -> RouteProbeStatus:
        if route_id == "managed":
            if self._managed_status is None:
                return RouteProbeStatus(
                    readiness="not_offered",
                    reason_code="MANAGED_SERVICE_NOT_CONFIGURED",
                    message="このビルドではMeeting Supporter AIを提供していません。",
                    action="none",
                )
            return await self._managed_status()
        if route_id == "codex":
            route = self._routes.get("codex")
            requested_model = route.model if route is not None else None
            if not requested_model:
                return RouteProbeStatus(
                    readiness="setup_required",
                    reason_code="CODEX_MODEL_NOT_CONFIGURED",
                    message="Codexで利用するモデルを設定してください。",
                    action="configure",
                )
            status_provider = self._codex_status
            if status_provider is None:
                return RouteProbeStatus(
                    readiness="unknown",
                    reason_code="CODEX_STATUS_CONTROLLER_NOT_CONNECTED",
                    message="Codexの利用状態をまだ確認できません。",
                    action="login",
                )
            return await self._safe_probe(
                lambda: status_provider(requested_model),
                "CODEX_STATUS_CHECK_FAILED",
            )
        if route_id == "acp":
            route = self._routes.get("acp")
            if route is None or not route.command:
                return RouteProbeStatus(
                    readiness="setup_required",
                    reason_code="ACP_COMMAND_NOT_CONFIGURED",
                    message="ACPエージェントのコマンドを設定してください。",
                    action="configure",
                )
            return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")
        if route_id == "ollama":
            if "ollama" not in self._providers:
                return RouteProbeStatus(
                    readiness="unavailable",
                    reason_code="OLLAMA_PROVIDER_NOT_CONFIGURED",
                    message="Ollamaの設定がありません。",
                    action="configure",
                )
            if self._ollama_status is None:
                return RouteProbeStatus(
                    readiness="unknown",
                    reason_code="OLLAMA_STATUS_CONTROLLER_NOT_CONNECTED",
                    message="Ollamaの稼働状態をまだ確認できません。",
                    action="start",
                )
            return await self._safe_probe(self._ollama_status, "OLLAMA_STATUS_CHECK_FAILED")
        return self._provider_status(route_id)

    def _provider_status(self, route_id: str) -> RouteProbeStatus:
        provider = self._providers.get(route_id)
        if provider is None:
            return RouteProbeStatus(
                readiness="unavailable",
                reason_code="MODEL_PROVIDER_NOT_CONFIGURED",
                message="モデルプロバイダーの設定がありません。",
                action="configure",
            )
        route = self._routes.get(route_id)
        if route is None or not route.model:
            return RouteProbeStatus(
                readiness="setup_required",
                reason_code="MODEL_NOT_CONFIGURED",
                message="利用するモデルを設定してください。",
                action="configure",
            )
        if provider.key_ref and not self._secret_store.status(provider.key_ref):
            return RouteProbeStatus(
                readiness="setup_required",
                reason_code="API_CREDENTIAL_NOT_CONFIGURED",
                message="このAIを利用するには認証情報の設定が必要です。",
                action="configure",
            )
        return RouteProbeStatus(readiness="ready", reason_code="", message="利用できます。")

    @staticmethod
    async def _safe_probe(
        probe: Callable[[], Awaitable[RouteProbeStatus]],
        failure_code: str,
    ) -> RouteProbeStatus:
        try:
            result = await probe()
        except Exception:
            return RouteProbeStatus(
                readiness="error",
                reason_code=failure_code,
                message="利用状態を確認できませんでした。もう一度お試しください。",
                action="none",
            )
        return result


def route_supports(route: RouteReadModel, use_case: RouteCapability) -> bool:
    """Return whether a selectable route supports an assignable use case."""

    return route.selectable and use_case in route.capabilities


__all__ = [
    "BUILT_IN_ROUTE_IDS",
    "CodexStatusProvider",
    "ManagedStatusProvider",
    "OllamaStatusProvider",
    "OllamaHttpStatusProvider",
    "RouteAssignmentsReadModel",
    "AssignableUseCase",
    "RouteCatalog",
    "RouteCatalogResponse",
    "RouteProbeStatus",
    "RouteReadModel",
    "ollama_data_location",
    "route_supports",
]
