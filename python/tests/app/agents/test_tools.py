import asyncio
import inspect
import unittest
from collections.abc import Awaitable, Callable
from typing import cast

from app.agents.tools import make_str_replace_tool


class StrReplaceToolTest(unittest.IsolatedAsyncioTestCase):
    async def _run_tool(self, tool: Callable[[str, str], object], old_str: str, new_str: str) -> str:
        result = tool(old_str, new_str)
        if not inspect.isawaitable(result):
            self.fail("make_str_replace_tool must return an async tool that awaits the injected replace callback.")
        return await cast(Awaitable[str], result)

    async def test_str_replace_awaits_injected_callback_and_returns_its_result(self) -> None:
        calls: list[tuple[str, str]] = []
        callback_started = asyncio.Event()
        callback_can_finish = asyncio.Event()

        async def replace_ai_note(old_str: str, new_str: str) -> str:
            calls.append((old_str, new_str))
            callback_started.set()
            _ = await callback_can_finish.wait()
            return "OK: replaced"

        tool = cast(Callable[[str, str], object], make_str_replace_tool(replace_ai_note))
        result_task = asyncio.create_task(self._run_tool(tool, "旧", "新"))
        _ = await callback_started.wait()

        self.assertFalse(result_task.done())
        callback_can_finish.set()

        self.assertEqual("OK: replaced", await result_task)
        self.assertEqual([("旧", "新")], calls)


if __name__ == "__main__":
    _ = unittest.main()
