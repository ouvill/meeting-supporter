"""Agent runtime models."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, Protocol, Self, override

from app.core.protocols import AgentLike, LifecycledAgentLike, StreamLike


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


@dataclass(frozen=True)
class InfoPrompt:
    """Input for an info agent runtime."""

    text: str


type InfoOutputMode = Literal["tool_update", "complete_note"]


class InfoAgentRuntime(Protocol):
    """情報更新用 runtime。MCP toolset を持つため lifecycle も持つ。"""

    @property
    def output_mode(self) -> InfoOutputMode: ...

    def run_stream(self, prompt: InfoPrompt) -> AbstractAsyncContextManager[StreamLike]: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, *exc_info: object) -> bool | None: ...


@dataclass(frozen=True)
class MinutesPrompt:
    """Input for a minutes agent runtime."""

    text: str


class MinutesAgentRuntime(Protocol):
    """議事録生成用 runtime。MCP 初期化は不要なため context-manager は持たない。"""

    def run_stream(self, prompt: MinutesPrompt) -> AbstractAsyncContextManager[StreamLike]: ...


@dataclass(frozen=True)
class PydanticAIInfoAgentRuntime(InfoAgentRuntime):
    """info 専用 adapter。agent は toolsets/tools/system_prompt 既設定の LifecycledAgentLike。"""

    agent: LifecycledAgentLike

    @property
    @override
    def output_mode(self) -> Literal["tool_update"]:
        return "tool_update"

    @override
    def run_stream(self, prompt: InfoPrompt) -> AbstractAsyncContextManager[StreamLike]:
        return self.agent.run_stream(prompt.text)

    @override
    async def __aenter__(self) -> Self:
        _ = await self.agent.__aenter__()
        return self

    @override
    async def __aexit__(self, *exc_info: object) -> bool | None:
        return await self.agent.__aexit__(*exc_info)


@dataclass(frozen=True)
class PydanticAIMinutesAgentRuntime(MinutesAgentRuntime):
    agent: AgentLike

    @override
    def run_stream(self, prompt: MinutesPrompt) -> AbstractAsyncContextManager[StreamLike]:
        return self.agent.run_stream(prompt.text)


__all__ = [
    "InfoOutputMode",
    "InfoAgentRuntime",
    "InfoPrompt",
    "MinutesAgentRuntime",
    "MinutesPrompt",
    "PydanticAIInfoAgentRuntime",
    "PydanticAIMinutesAgentRuntime",
    "PydanticAIReplyAgentRuntime",
    "ReplyAgentDefinition",
    "ReplyAgentRuntime",
    "ReplyAgentSpec",
    "ReplyPrompt",
]
