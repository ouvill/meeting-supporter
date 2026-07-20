# pyright: reportUnusedFunction=false
"""Construct AI runtimes from schema-v2 route assignments."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai.toolsets import AbstractToolset

from app.agents.model_resolver import resolve_route_model
from app.agents.models import (
    InfoAgentRuntime,
    MinutesAgentRuntime,
    ReplyAgentDefinition,
    ReplyAgentRuntime,
    ReplyAgentSpec,
)
from app.agents.runtime_factory import (
    build_acp_reply_runtime,
    build_info_runtime,
    build_minutes_runtime,
    build_pydantic_reply_runtime,
)
from app.core.config import AiRouteAssignments, ProviderDefinition, RouteDefinition
from app.core.protocols import SecretStore
from app.core.state import AppState
from app.services.usage_logger import UsageLogger

ExternalReplyRuntimeFactory = Callable[[RouteDefinition, ReplyAgentDefinition], ReplyAgentRuntime]
ExternalInfoRuntimeFactory = Callable[[RouteDefinition], InfoAgentRuntime]
ExternalMinutesRuntimeFactory = Callable[[RouteDefinition], MinutesAgentRuntime]


class AgentRouteError(RuntimeError):
    """Safe composition-boundary error for an assigned route that cannot run."""

    code: str
    message: str
    retryable: bool

    def __init__(self, *, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class AgentBundle:
    """Available use-case runtimes; an unassigned use case is explicitly ``None``."""

    info_runtime: InfoAgentRuntime | None
    minutes_runtime: MinutesAgentRuntime | None
    reply_agent_specs: list[ReplyAgentSpec]


def _assigned_route(
    route_id: str,
    routes: Mapping[str, RouteDefinition],
    *,
    use_case: str,
) -> RouteDefinition:
    route = routes.get(route_id)
    if route is None:
        raise AgentRouteError(
            code="AI_ROUTE_NOT_FOUND",
            message=f"{use_case}に設定されたAI経路を利用できません。設定を確認してください。",
        )
    return route


def _build_reply_runtime(
    *,
    route: RouteDefinition,
    definition: ReplyAgentDefinition,
    external_reply_factories: Mapping[str, ExternalReplyRuntimeFactory],
    state: AppState,
    providers: list[ProviderDefinition],
    secret_store: SecretStore,
    context_dir: Path,
    usage_logger: UsageLogger,
) -> ReplyAgentRuntime:
    if route.runtime == "pydantic-ai":
        try:
            model = resolve_route_model(route, providers, secret_store)
        except ValueError as error:
            raise AgentRouteError(
                code="AI_ROUTE_SETUP_REQUIRED",
                message="選択したAI経路の設定が完了していません。",
            ) from error
        return build_pydantic_reply_runtime(
            agent_id=definition.id,
            model=model,
            state=state,
            instruction=definition.instruction,
            usage_logger=usage_logger,
        )
    if route.runtime == "acp":
        try:
            return build_acp_reply_runtime(route, context_dir)
        except ValueError as error:
            raise AgentRouteError(
                code="ACP_ROUTE_SETUP_REQUIRED",
                message="ACP経路の設定が完了していません。",
            ) from error
    if route.runtime == "managed":
        factory = external_reply_factories.get(route.id)
        if factory is None:
            raise AgentRouteError(
                code="MANAGED_RUNTIME_NOT_CONNECTED",
                message="Meeting Supporter AIへまだ接続できません。",
                retryable=True,
            )
        return factory(route, definition)
    if route.runtime == "codex-app-server":
        if not route.model:
            raise AgentRouteError(
                code="CODEX_MODEL_NOT_CONFIGURED",
                message="Codex経路のモデル設定が空です。設定を確認してください。",
            )
        factory = external_reply_factories.get(route.id)
        if factory is None:
            raise AgentRouteError(
                code="CODEX_RUNTIME_NOT_CONNECTED",
                message="Codex実行環境へまだ接続できません。",
                retryable=True,
            )
        return factory(route, definition)
    raise AgentRouteError(
        code="AI_ROUTE_NOT_AVAILABLE",
        message="選択したAI経路は現在利用できません。",
    )


def _build_minutes_runtime(
    *,
    route_id: str | None,
    routes: Mapping[str, RouteDefinition],
    providers: list[ProviderDefinition],
    secret_store: SecretStore,
    state: AppState,
    usage_logger: UsageLogger,
    external_minutes_factories: Mapping[str, ExternalMinutesRuntimeFactory],
) -> MinutesAgentRuntime | None:
    if route_id is None:
        return None
    route = _assigned_route(route_id, routes, use_case="議事録")
    if route.runtime == "codex-app-server":
        if not route.model:
            raise AgentRouteError(
                code="CODEX_MODEL_NOT_CONFIGURED",
                message="Codex経路のモデル設定が空です。設定を確認してください。",
            )
        factory = external_minutes_factories.get(route.id)
        if factory is None:
            raise AgentRouteError(
                code="CODEX_RUNTIME_NOT_CONNECTED",
                message="Codex実行環境へまだ接続できません。",
                retryable=True,
            )
        return factory(route)
    if route.runtime != "pydantic-ai":
        raise AgentRouteError(
            code="AI_ROUTE_CAPABILITY_MISMATCH",
            message="選択したAI経路は議事録に対応していません。",
        )
    try:
        model = resolve_route_model(route, providers, secret_store)
    except ValueError as error:
        raise AgentRouteError(
            code="AI_ROUTE_SETUP_REQUIRED",
            message="議事録のAI経路設定が完了していません。",
        ) from error
    return build_minutes_runtime(model=model, state=state, usage_logger=usage_logger)


def _build_info_runtime(
    *,
    route_id: str | None,
    routes: Mapping[str, RouteDefinition],
    providers: list[ProviderDefinition],
    secret_store: SecretStore,
    state: AppState,
    context_dir: Path,
    usage_logger: UsageLogger,
    mcp_servers: list[AbstractToolset[None]],
    replace_ai_note: Callable[[str, str], Awaitable[str]],
    external_info_factories: Mapping[str, ExternalInfoRuntimeFactory],
) -> InfoAgentRuntime | None:
    if route_id is None:
        return None
    route = _assigned_route(route_id, routes, use_case="情報更新")
    if route.runtime == "codex-app-server":
        if not route.model:
            raise AgentRouteError(
                code="CODEX_MODEL_NOT_CONFIGURED",
                message="Codex経路のモデル設定が空です。設定を確認してください。",
            )
        factory = external_info_factories.get(route.id)
        if factory is None:
            raise AgentRouteError(
                code="CODEX_RUNTIME_NOT_CONNECTED",
                message="Codex実行環境へまだ接続できません。",
                retryable=True,
            )
        return factory(route)
    if route.runtime != "pydantic-ai":
        raise AgentRouteError(
            code="AI_ROUTE_CAPABILITY_MISMATCH",
            message="選択したAI経路は情報更新に対応していません。",
        )
    try:
        model = resolve_route_model(route, providers, secret_store)
    except ValueError as error:
        raise AgentRouteError(
            code="AI_ROUTE_SETUP_REQUIRED",
            message="情報更新のAI経路設定が完了していません。",
        ) from error
    return build_info_runtime(
        model=model,
        state=state,
        context_dir=context_dir,
        usage_logger=usage_logger,
        mcp_servers=mcp_servers,
        replace_ai_note=replace_ai_note,
    )


def build_agents(
    *,
    state: AppState,
    providers: list[ProviderDefinition],
    routes: list[RouteDefinition],
    assignments: AiRouteAssignments,
    secret_store: SecretStore,
    context_dir: Path,
    usage_logger: UsageLogger,
    mcp_servers: list[AbstractToolset[None]],
    reply_agent_definitions: list[ReplyAgentDefinition],
    replace_ai_note: Callable[[str, str], Awaitable[str]],
    external_reply_factories: Mapping[str, ExternalReplyRuntimeFactory] | None = None,
    external_minutes_factories: Mapping[str, ExternalMinutesRuntimeFactory] | None = None,
    external_info_factories: Mapping[str, ExternalInfoRuntimeFactory] | None = None,
) -> AgentBundle:
    """Build assigned runtimes without inventing a fallback route."""

    route_by_id = {route.id: route for route in routes}
    external_factories = external_reply_factories or {}
    info_factories = external_info_factories or {}
    minutes_factories = external_minutes_factories or {}
    reply_agent_specs: list[ReplyAgentSpec] = []
    if assignments.reply is not None:
        reply_route = _assigned_route(assignments.reply, route_by_id, use_case="返答")
        for definition in sorted(reply_agent_definitions, key=lambda item: item.priority):
            if not definition.enabled:
                continue
            runtime = _build_reply_runtime(
                route=reply_route,
                definition=definition,
                external_reply_factories=external_factories,
                state=state,
                providers=providers,
                secret_store=secret_store,
                context_dir=context_dir,
                usage_logger=usage_logger,
            )
            reply_agent_specs.append(
                ReplyAgentSpec(
                    id=definition.id,
                    label=definition.label,
                    runtime=runtime,
                    priority=definition.priority,
                )
            )

    info_runtime = _build_info_runtime(
        route_id=assignments.info,
        routes=route_by_id,
        providers=providers,
        secret_store=secret_store,
        state=state,
        context_dir=context_dir,
        usage_logger=usage_logger,
        mcp_servers=mcp_servers,
        replace_ai_note=replace_ai_note,
        external_info_factories=info_factories,
    )
    minutes_runtime = _build_minutes_runtime(
        route_id=assignments.minutes,
        routes=route_by_id,
        providers=providers,
        secret_store=secret_store,
        state=state,
        usage_logger=usage_logger,
        external_minutes_factories=minutes_factories,
    )

    return AgentBundle(
        info_runtime=info_runtime,
        minutes_runtime=minutes_runtime,
        reply_agent_specs=reply_agent_specs,
    )


__all__ = [
    "AgentBundle",
    "AgentRouteError",
    "ExternalInfoRuntimeFactory",
    "ExternalReplyRuntimeFactory",
    "ExternalMinutesRuntimeFactory",
    "build_agents",
]
