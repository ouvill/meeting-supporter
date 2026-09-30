"""Agent runtime models."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol, override

from app.core.protocols import AgentLike, StreamLike


@dataclass(frozen=True)
class ReplyPrompt:
    """Input for a reply agent runtime."""

    text: str


class ReplyAgentRuntime(Protocol):
    """Protocol for reply-generating agent runtimes."""

    def run_stream(self, prompt: ReplyPrompt) -> AbstractAsyncContextManager[StreamLike]: ...


@dataclass(frozen=True)
class PydanticAIReplyAgentRuntime(ReplyAgentRuntime):
    """Adapter wrapping an AgentLike (Pydantic AI agent) into ReplyAgentRuntime."""

    agent: AgentLike

    @override
    def run_stream(self, prompt: ReplyPrompt) -> AbstractAsyncContextManager[StreamLike]:
        return self.agent.run_stream(prompt.text)


@dataclass(frozen=True)
class ReplyAgentDefinition:
    """Config-level definition of a reply agent."""

    id: str
    label: str
    enabled: bool
    priority: int
    instruction: str


@dataclass(frozen=True)
class ReplyAgentSpec:
    """Runtime specification for a reply-generating agent."""

    id: str
    label: str
    runtime: ReplyAgentRuntime
    priority: int = 100


__all__ = [
    "PydanticAIReplyAgentRuntime",
    "ReplyAgentDefinition",
    "ReplyAgentRuntime",
    "ReplyAgentSpec",
    "ReplyPrompt",
]
