"""Persisted post-meeting minutes endpoint."""

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.agents.models import MinutesAgentRuntime
from app.meetings.service import MeetingHistoryService
from app.services.minutes_generator import MinutesGenerator


def create_router(
    *,
    history_service: MeetingHistoryService,
    get_minutes_runtime: Callable[[], MinutesAgentRuntime | None],
) -> APIRouter:
    router = APIRouter(tags=["meetings"])

    @router.post(
        "/meetings/{meeting_id}/minutes",
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "UTF-8 plaintext minutes stream; saved after the stream completes.",
                "content": {"text/plain": {"schema": {"type": "string"}}},
            }
        },
    )
    async def generate_minutes(meeting_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        meeting = await history_service.repository.get_meeting(meeting_id)
        if meeting is None:
            return JSONResponse({"error": "会議が見つかりません"}, status_code=404)
        if meeting.status != "completed":
            return JSONResponse({"error": "会議終了後に要約・議事録を作成できます"}, status_code=400)

        snapshot = await history_service.get_minutes_snapshot(meeting_id)
        if snapshot is None or not snapshot.turns:
            return JSONResponse({"error": "会話履歴がありません"}, status_code=400)

        minutes_runtime = get_minutes_runtime()
        if minutes_runtime is None:
            return JSONResponse(
                {
                    "error": "議事録AIは未設定です。設定で対応するAI経路を選択してください。",
                    "code": "MINUTES_RUNTIME_UNAVAILABLE",
                },
                status_code=503,
            )
        minutes_generator = MinutesGenerator(minutes_runtime)

        async def stream():
            chunks: list[str] = []
            async for delta in minutes_generator.stream(snapshot):
                chunks.append(delta)
                yield delta
            _ = await history_service.save_minutes(meeting_id, "".join(chunks))

        return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")

    return router


__all__ = ["create_router"]
