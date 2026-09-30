import asyncio
import unittest
from collections.abc import AsyncIterator, Callable
from dataclasses import FrozenInstanceError
from typing import cast

from app.agents.models import (
    PydanticAIReplyAgentRuntime,
    ReplyAgentDefinition,
    ReplyPrompt,
)


class FakeStream:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks: list[str] = chunks

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    async def stream_text(self, *, delta: bool) -> AsyncIterator[str]:
        _ = delta
        for chunk in self._chunks:
            await asyncio.sleep(0)
            yield chunk


class RecordingAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run_stream(self, user_prompt: str) -> FakeStream:
        self.prompts.append(user_prompt)
        return FakeStream(["ok"])


class RecordingLifecycledAgent(RecordingAgent):
    entered: bool = False
    exited: bool = False

    async def __aenter__(self) -> "RecordingLifecycledAgent":
        self.entered = True
        return self

    async def __aexit__(self, *exc_info: object) -> bool | None:
        self.exited = True
        return None


class ReplyAgentRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_pydantic_ai_runtime_delegates_prompt_text(self) -> None:
        agent = RecordingAgent()
        runtime = PydanticAIReplyAgentRuntime(agent)

        async with runtime.run_stream(ReplyPrompt(text="hello")) as stream:
            chunks = [chunk async for chunk in stream.stream_text(delta=True)]

        self.assertEqual(["hello"], agent.prompts)
        self.assertEqual(["ok"], chunks)

    def test_reply_prompt_is_immutable(self) -> None:
        prompt = ReplyPrompt(text="hello")

        with self.assertRaises(FrozenInstanceError):
            setattr(prompt, "text", "changed")


class ReplyAgentDefinitionTest(unittest.TestCase):
    def test_style_definition_rejects_runtime_model_selection(self) -> None:
        """Style metadata cannot select a provider model; runtime construction owns that choice."""
        legacy_definition_factory = cast(Callable[..., ReplyAgentDefinition], ReplyAgentDefinition)

        with self.assertRaisesRegex(TypeError, "model"):
            _ = legacy_definition_factory(
                id="custom",
                label="Custom",
                enabled=True,
                priority=50,
                model="openai:gpt-4o-mini",
                instruction="Be helpful",
            )

    def test_is_immutable(self) -> None:
        d = ReplyAgentDefinition(id="test", label="Test", enabled=True, priority=10, instruction="Hi")
        with self.assertRaises(FrozenInstanceError):
            setattr(d, "id", "changed")


if __name__ == "__main__":
    _ = unittest.main()
