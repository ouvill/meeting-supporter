# pyright: reportUnusedFunction=false
"""Construct AI runtimes from schema-v2 route assignments."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.agents.model_resolver import resolve_route_model
from app.agents.models import MinutesAgentRuntime, ReplyAgentDefinition, ReplyAgentRuntime, ReplyAgentSpec
from app.agents.runtime_factory import build_minutes_runtime, build_pydantic_reply_runtime
from app.core.config import AiRouteAssignments, ProviderDefinition, RouteDefinition
from app.core.protocols import SecretStore
from app.core.state import AppState
from app.services.usage_logger import UsageLogger

ExternalReplyRuntimeFactory = Callable[[RouteDefinition, ReplyAgentDefinition], ReplyAgentRuntime]


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
    if route.runtime == "managed":
        factory = external_reply_factories.get(route.id)
        if factory is None:
            raise AgentRouteError(
                code="MANAGED_RUNTIME_NOT_CONNECTED",
                message="Meeting Supporter AIへまだ接続できません。",
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
) -> MinutesAgentRuntime | None:
    if route_id is None:
        return None
    route = _assigned_route(route_id, routes, use_case="議事録")
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


def build_agents(
    *,
    state: AppState,
    providers: list[ProviderDefinition],
    routes: list[RouteDefinition],
    assignments: AiRouteAssignments,
    secret_store: SecretStore,
    usage_logger: UsageLogger,
    reply_agent_definitions: list[ReplyAgentDefinition],
    external_reply_factories: Mapping[str, ExternalReplyRuntimeFactory] | None = None,
) -> AgentBundle:
    """Build assigned runtimes without inventing a fallback route."""

    route_by_id = {route.id: route for route in routes}
    external_factories = external_reply_factories or {}
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

    minutes_runtime = _build_minutes_runtime(
        route_id=assignments.minutes,
        routes=route_by_id,
        providers=providers,
        secret_store=secret_store,
        state=state,
        usage_logger=usage_logger,
    )

    return AgentBundle(
        minutes_runtime=minutes_runtime,
        reply_agent_specs=reply_agent_specs,
    )


__all__ = [
    "AgentBundle",
    "AgentRouteError",
    "ExternalReplyRuntimeFactory",
    "build_agents",
]
