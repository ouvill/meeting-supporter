from collections.abc import AsyncIterator

from app.agents.models import MinutesAgentRuntime, MinutesPrompt
from app.agents.prompts import build_minutes_prompt
from app.meetings.models import MeetingSession, history_texts


class MinutesGenerator:
    """Post-meeting minutes generation use case."""

    def __init__(self, minutes_runtime: MinutesAgentRuntime) -> None:
        self._minutes_runtime: MinutesAgentRuntime = minutes_runtime

    async def stream(self, session: MeetingSession) -> AsyncIterator[str]:
        prompt = build_minutes_prompt(history_texts(session.turns), session.ai_note)
        async with self._minutes_runtime.run_stream(MinutesPrompt(text=prompt)) as s:
            async for delta in s.stream_text(delta=True):
                yield delta


__all__ = ["MinutesGenerator"]
