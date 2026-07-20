# pyright: reportUnusedFunction=false
"""Runtime construction helpers for agent use cases."""

from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.toolsets import AbstractToolset

from app.agents.acp_runtime import ACPReplyAgentRuntime
from app.agents.models import (
    InfoAgentRuntime,
    MinutesAgentRuntime,
    PydanticAIInfoAgentRuntime,
    PydanticAIMinutesAgentRuntime,
    PydanticAIReplyAgentRuntime,
    ReplyAgentRuntime,
)
from app.agents.prompts import INFO_INSTRUCTION, MINUTES_INSTRUCTION, build_system
from app.agents.tools import make_search_context_files_tool, make_str_replace_tool
from app.core.config import RouteDefinition
from app.core.state import AppState
from app.services.usage_logger import UsageLogger, make_logging_hooks

ModelValue = OpenAIChatModel | str


def build_acp_reply_runtime(route: RouteDefinition, context_dir: Path) -> ReplyAgentRuntime:
    """Build a reply runtime backed by an external ACP process route."""

    if route.runtime != "acp" or not route.command:
        raise ValueError(f"ACP route '{route.id}' にはcommandが必要です")
    return ACPReplyAgentRuntime(command=route.command, cwd=context_dir, env=route.env)


def build_pydantic_reply_runtime(
    *,
    agent_id: str,
    model: ModelValue,
    state: AppState,
    instruction: str,
    usage_logger: UsageLogger,
) -> ReplyAgentRuntime:
    """Build a Pydantic AI reply runtime for a user-visible reply candidate."""
    agent: Agent[None] = Agent(
        model,
        capabilities=[make_logging_hooks(agent_id, usage_logger)],
    )

    @agent.system_prompt
    def _reply_system() -> str:
        return build_system(instruction, state.context_text)

    return PydanticAIReplyAgentRuntime(agent)


def build_info_runtime(
    *,
    model: ModelValue,
    state: AppState,
    context_dir: Path,
    usage_logger: UsageLogger,
    mcp_servers: list[AbstractToolset[None]],
    replace_ai_note: Callable[[str, str], Awaitable[str]],
) -> InfoAgentRuntime:
    """Build the hidden info-note updater runtime."""
    agent: Agent[None] = Agent(
        model,
        toolsets=mcp_servers,
        capabilities=[make_logging_hooks("info", usage_logger)],
    )

    @agent.system_prompt
    def _info_system() -> str:
        return build_system(INFO_INSTRUCTION, state.context_text)

    _ = agent.tool_plain()(make_str_replace_tool(replace_ai_note))
    _ = agent.tool_plain()(make_search_context_files_tool(context_dir))
    return PydanticAIInfoAgentRuntime(agent)


def build_minutes_runtime(
    *,
    model: ModelValue,
    state: AppState,
    usage_logger: UsageLogger,
) -> MinutesAgentRuntime:
    """Build the post-meeting minutes generator runtime."""
    agent: Agent[None] = Agent(
        model,
        capabilities=[make_logging_hooks("minutes", usage_logger)],
    )

    @agent.system_prompt
    def _minutes_system() -> str:
        return build_system(MINUTES_INSTRUCTION, state.context_text)

    return PydanticAIMinutesAgentRuntime(agent)


__all__ = [
    "ModelValue",
    "build_acp_reply_runtime",
    "build_info_runtime",
    "build_minutes_runtime",
    "build_pydantic_reply_runtime",
]
