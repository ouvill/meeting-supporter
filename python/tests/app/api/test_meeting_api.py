"""Behavior tests for persisted POST /meetings/{meeting_id}/minutes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import cast, override

from fastapi import FastAPI

from app.agents.models import MinutesAgentRuntime, MinutesPrompt
from app.api.meeting import create_router
from app.api.meeting_history import create_router as create_history_router
from app.core.protocols import StreamLike
from app.meetings.history_models import MeetingRecord, MeetingStatus, MeetingTurnRecord
from app.meetings.service import MeetingHistoryService
from app.meetings.sqlite_repository import SqliteMeetingHistoryRepository
from tests.helpers.api_client import TypedTestClient


class _FakeMinutesStream(StreamLike):
    def __init__(self, chunks: tuple[str, ...], *, cancel_after: int | None = None) -> None:
        self._chunks: tuple[str, ...] = chunks
        self._cancel_after: int | None = cancel_after
        self.delta_calls: list[bool] = []
        self.closed: bool = False

    async def __aenter__(self) -> _FakeMinutesStream:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        self.closed = True

    @override
    async def stream_text(self, *, delta: bool) -> AsyncIterator[str]:
        self.delta_calls.append(delta)
        for index, chunk in enumerate(self._chunks):
            if self._cancel_after == index:
                raise asyncio.CancelledError()
            yield chunk


class _FakeMinutesRuntime:
    def __init__(
        self,
        chunks: tuple[str, ...] = ("# 議事録\n", "- 決定: 見積もりを送る\n"),
        *,
        cancel_after: int | None = None,
    ) -> None:
        self._chunks: tuple[str, ...] = chunks
        self._cancel_after: int | None = cancel_after
        self.prompts: list[MinutesPrompt] = []
        self.streams: list[_FakeMinutesStream] = []

    def run_stream(self, prompt: MinutesPrompt) -> _FakeMinutesStream:
        self.prompts.append(prompt)
        stream = _FakeMinutesStream(self._chunks, cancel_after=self._cancel_after)
        self.streams.append(stream)
        return stream


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _make_client(
    tmp_path: Path,
    *,
    minutes_available: bool = True,
    chunks: tuple[str, ...] = ("# 議事録\n", "- 決定: 見積もりを送る\n"),
    cancel_after: int | None = None,
) -> tuple[TypedTestClient, _FakeMinutesRuntime, SqliteMeetingHistoryRepository]:
    runtime = _FakeMinutesRuntime(chunks, cancel_after=cancel_after)
    repository = SqliteMeetingHistoryRepository(":memory:")
    _run(repository.initialize())
    history_service = MeetingHistoryService(repository=repository)
    app = FastAPI()

    def get_minutes_runtime() -> MinutesAgentRuntime | None:
        return cast(MinutesAgentRuntime, runtime) if minutes_available else None

    app.include_router(
        create_router(
            get_minutes_runtime=get_minutes_runtime,
            history_service=history_service,
        )
    )
    app.include_router(create_history_router(history_service=history_service, user_data_dir=tmp_path))
    return TypedTestClient(app), runtime, repository


def _persist_meeting(
    repository: SqliteMeetingHistoryRepository,
    *,
    meeting_id: str,
    status: MeetingStatus = "completed",
    transcript: tuple[str, ...] = ("次回までに見積もりを送ります", "承知しました"),
    minutes: str = "",
) -> None:
    _run(
        repository.create_meeting(
            MeetingRecord(
                id=meeting_id,
                started_at=datetime(2026, 7, 8, tzinfo=UTC),
                minutes=minutes,
                status=status,
            )
        )
    )
    for sequence, text in enumerate(transcript, start=1):
        _run(
            repository.insert_turn(
                MeetingTurnRecord(
                    id=f"{meeting_id}-turn-{sequence}",
                    meeting_id=meeting_id,
                    sequence=sequence,
                    speaker="other" if sequence % 2 else "self",
                    text=text,
                )
            )
        )


def test_completed_persisted_meeting_streams_and_saves_only_its_full_minutes_output(tmp_path: Path) -> None:
    client, runtime, repository = _make_client(tmp_path, chunks=("# 議事録\n", "- 決定事項\n", "- 見積もりを送る\n"))
    _persist_meeting(repository, meeting_id="meeting-completed")

    response = client.post("/meetings/meeting-completed/minutes")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == "# 議事録\n- 決定事項\n- 見積もりを送る\n"
    assert len(runtime.prompts) == 1
    assert "次回までに見積もりを送ります" in runtime.prompts[0].text
    assert "承知しました" in runtime.prompts[0].text
    assert [stream.delta_calls for stream in runtime.streams] == [[True]]
    persisted = _run(repository.get_meeting("meeting-completed"))
    assert persisted is not None
    assert persisted.minutes == response.text
    detail = client.get("/meetings/meeting-completed")
    assert detail.status_code == 200
    assert detail.json_object()["minutes"] == response.text


def test_minutes_for_missing_or_unready_runtime_returns_safe_error_without_reply_fallback(tmp_path: Path) -> None:
    client, runtime, repository = _make_client(tmp_path, minutes_available=False)
    _persist_meeting(repository, meeting_id="meeting-no-runtime")

    missing = client.post("/meetings/no-such-meeting/minutes")
    unavailable = client.post("/meetings/meeting-no-runtime/minutes")

    assert missing.status_code == 404
    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "error": "議事録AIは未設定です。設定で対応するAI経路を選択してください。",
        "code": "MINUTES_RUNTIME_UNAVAILABLE",
    }
    assert runtime.prompts == []


def test_minutes_rejects_active_aborted_and_transcriptless_meetings_before_starting_ai(tmp_path: Path) -> None:
    client, runtime, repository = _make_client(tmp_path)
    _persist_meeting(repository, meeting_id="meeting-active", status="active")
    _persist_meeting(repository, meeting_id="meeting-aborted", status="aborted")
    _persist_meeting(repository, meeting_id="meeting-empty", transcript=())

    for meeting_id in ("meeting-active", "meeting-aborted", "meeting-empty"):
        response = client.post(f"/meetings/{meeting_id}/minutes")
        assert response.status_code == 400, meeting_id

    assert runtime.prompts == []


def test_cancelled_minutes_stream_closes_context_and_does_not_save_partial_content(tmp_path: Path) -> None:
    client, runtime, repository = _make_client(
        tmp_path,
        chunks=("# 部分的な議事録\n", "- 保存してはいけない\n"),
        cancel_after=1,
    )
    _persist_meeting(repository, meeting_id="meeting-cancelled", minutes="既存の議事録")

    try:
        _ = client.post("/meetings/meeting-cancelled/minutes")
    except BaseException:
        pass

    assert len(runtime.streams) == 1
    assert runtime.streams[0].closed is True
    persisted = _run(repository.get_meeting("meeting-cancelled"))
    assert persisted is not None
    assert persisted.minutes == "既存の議事録"
