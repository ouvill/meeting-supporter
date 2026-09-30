# pyright: reportUnusedFunction=false
"""Runtime construction helpers for agent use cases."""

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from app.agents.models import (
    PydanticAIReplyAgentRuntime,
    ReplyAgentRuntime,
)
from app.agents.prompts import build_system
from app.core.state import AppState
from app.services.usage_logger import UsageLogger, make_logging_hooks

ModelValue = OpenAIChatModel | str


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


__all__ = [
    "ModelValue",
    "build_pydantic_reply_runtime",
]
