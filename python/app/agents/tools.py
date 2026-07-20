"""Tool factory functions for info_agent."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path


def make_str_replace_tool(
    replace_ai_note: Callable[[str, str], Awaitable[str]],
) -> Callable[[str, str], Awaitable[str]]:
    async def str_replace(old_str: str, new_str: str) -> str:
        """会議補助資料の old_str を new_str に置き換える。変化のあった箇所だけを更新すること。"""
        return await replace_ai_note(old_str, new_str)

    return str_replace


def make_search_context_files_tool(context_dir: Path) -> Callable[[str], Awaitable[str]]:
    async def search_context_files(query: str) -> str:
        """ユーザーが context/ ディレクトリに用意した資料からキーワード検索する。"""
        if not context_dir.exists():
            return "資料ディレクトリなし"
        hits: list[str] = []
        md_files = await asyncio.to_thread(lambda: sorted(context_dir.glob("*.md")))
        for md_file in md_files:
            text = await asyncio.to_thread(md_file.read_text, encoding="utf-8")
            if query.lower() in text.lower():
                hits.append(f"## {md_file.stem}\n{text[:800]}")
        return "\n\n".join(hits) if hits else "該当する資料なし"

    return search_context_files


__all__ = [
    "make_search_context_files_tool",
    "make_str_replace_tool",
]
