"""Meeting history API: list, detail, update, delete, recording serving."""

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from app.meetings.history_models import RecordingRole
from app.meetings.recording_retention import RecordingCleanupError, RecordingCleanupPlan, RecordingRetentionService
from app.meetings.service import MeetingHistoryService

logger = logging.getLogger(__name__)

# ── Pydantic API models ────────────────────────────────────────────────────────

_AUDIO_MEDIA_TYPES: dict[str, str] = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
    "webm": "audio/webm",
}


class MeetingListItem(BaseModel):
    """Lightweight meeting list item with asset indicator flags."""

    id: str
    title: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    status: str
    has_ai_note: bool
    has_recording: bool


class MeetingListPage(BaseModel):
    """Paginated meeting list response."""

    items: list[MeetingListItem]
    total: int
    limit: int
    offset: int


class TurnItem(BaseModel):
    id: str
    sequence: int
    speaker: str
    text: str
    speaker_id: str | None = None
    created_at: datetime | None = None


class ReplySuggestionItem(BaseModel):
    id: str
    target_turn_id: str
    sequence: int
    agent_id: str
    agent_label: str
    text: str
    created_at: datetime | None = None


class RecordingAssetItem(BaseModel):
    id: str
    role: RecordingRole
    format: str = "wav"
    sample_rate: int = 16000
    channels: int = 1
    started_at: datetime
    ended_at: datetime | None = None
    size_bytes: int | None = None


class MeetingDetail(BaseModel):
    id: str
    title: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    status: str
    ai_note: str = ""
    minutes: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    turns: list[TurnItem] = Field(default_factory=list)
    reply_suggestions: list[ReplySuggestionItem] = Field(default_factory=list)
    recording_assets: list[RecordingAssetItem] = Field(default_factory=list)


class UpdateTitleRequest(BaseModel):
    title: str


class UpdateMeetingTitleResponse(BaseModel):
    ok: bool


class DeleteMeetingResponse(BaseModel):
    ok: bool
    warning: str | None = None


class RecordingCleanupRequest(BaseModel):
    """A user-requested cleanup policy; omitted values disable that condition."""

    cutoff_date: date | None = None
    max_total_bytes: int | None = Field(default=None, gt=0)

    @field_validator("max_total_bytes", mode="before")
    @classmethod
    def zero_disables_capacity(cls, value: object) -> object:
        return None if value == 0 else value


class RecordingCleanupPreviewResponse(BaseModel):
    candidate_meeting_ids: list[str]
    delete_count: int
    delete_recording_bytes: int
    total_recording_bytes_before: int
    total_recording_bytes_after: int


class RecordingCleanupExecuteResponse(RecordingCleanupPreviewResponse):
    deleted_meeting_ids: list[str]
    failed_meeting_ids: list[str]
    skipped_meeting_ids: list[str]


# ── Utility ────────────────────────────────────────────────────────────────────


def _resolve_recording_path(base_dir: Path, relative_path: str) -> Path:
    """Resolve and validate a recording path under base_dir, preventing traversal.

    ``relative_path`` is relative to ``base_dir`` (the user_data_dir).
    Uses ``Path.relative_to()`` for a robust containment check that rejects
    sibling-prefix directories (e.g. ``/tmp/base_evil`` when base is ``/tmp/base``).
    """
    resolved = (base_dir / relative_path).resolve()
    base_resolved = base_dir.resolve()
    try:
        _ = resolved.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid recording path")
    return resolved


def _infer_media_type(format_str: str) -> str:
    return _AUDIO_MEDIA_TYPES.get(format_str, "application/octet-stream")


# ── Router factory ─────────────────────────────────────────────────────────────


def create_router(
    *,
    history_service: MeetingHistoryService,
    user_data_dir: Path,
) -> APIRouter:
    router = APIRouter(tags=["meetings"])

    retention = RecordingRetentionService(history=history_service, user_data_dir=user_data_dir)

    # All route handlers below are registered via decorator and called by FastAPI.
    # Inline pyright: ignore[reportUnusedFunction] is added to each.

    # ── GET /meetings ──────────────────────────────────────────────────────────

    @router.get("/meetings", response_model=MeetingListPage)
    async def list_meetings(  # pyright: ignore[reportUnusedFunction]
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> MeetingListPage:
        records, total = await history_service.list_meetings(limit=limit, offset=offset)
        items = [
            MeetingListItem(
                id=r.id,
                title=r.title,
                started_at=r.started_at,
                ended_at=r.ended_at,
                duration_seconds=r.duration_seconds,
                status=r.status,
                has_ai_note=bool(r.ai_note.strip()) if r.ai_note else False,
                has_recording=r.has_recording,
            )
            for r in records
        ]
        return MeetingListPage(items=items, total=total, limit=limit, offset=offset)

    # ── Explicit recording cleanup ────────────────────────────────────────────

    def cleanup_preview_response(plan: RecordingCleanupPlan) -> RecordingCleanupPreviewResponse:
        return RecordingCleanupPreviewResponse(
            candidate_meeting_ids=[candidate.meeting.id for candidate in plan.candidates],
            delete_count=plan.delete_count,
            delete_recording_bytes=plan.delete_recording_bytes,
            total_recording_bytes_before=plan.total_recording_bytes_before,
            total_recording_bytes_after=plan.total_recording_bytes_after,
        )

    @router.post("/meetings/recordings/cleanup/preview", response_model=RecordingCleanupPreviewResponse)
    async def preview_recording_cleanup(  # pyright: ignore[reportUnusedFunction]
        body: RecordingCleanupRequest,
    ) -> RecordingCleanupPreviewResponse:
        try:
            plan = await retention.preview(
                cutoff_date=body.cutoff_date,
                max_total_bytes=body.max_total_bytes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return cleanup_preview_response(plan)

    @router.post("/meetings/recordings/cleanup", response_model=RecordingCleanupExecuteResponse)
    async def execute_recording_cleanup(  # pyright: ignore[reportUnusedFunction]
        body: RecordingCleanupRequest,
    ) -> RecordingCleanupExecuteResponse:
        try:
            result = await retention.execute(
                cutoff_date=body.cutoff_date,
                max_total_bytes=body.max_total_bytes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        preview = cleanup_preview_response(result.plan)
        return RecordingCleanupExecuteResponse(
            candidate_meeting_ids=preview.candidate_meeting_ids,
            delete_count=preview.delete_count,
            delete_recording_bytes=preview.delete_recording_bytes,
            total_recording_bytes_before=preview.total_recording_bytes_before,
            total_recording_bytes_after=preview.total_recording_bytes_after,
            deleted_meeting_ids=list(result.deleted_meeting_ids),
            failed_meeting_ids=list(result.failed_meeting_ids),
            skipped_meeting_ids=list(result.skipped_meeting_ids),
        )

    # ── GET /meetings/{meeting_id} ─────────────────────────────────────────────
    @router.get("/meetings/{meeting_id}", response_model=MeetingDetail)
    async def get_meeting(meeting_id: str) -> MeetingDetail:  # pyright: ignore[reportUnusedFunction]
        meeting, turns, suggestions, assets = await history_service.get_meeting_detail(meeting_id)
        if meeting is None:
            raise HTTPException(status_code=404, detail="Meeting not found")
        return MeetingDetail(
            id=meeting.id,
            title=meeting.title,
            started_at=meeting.started_at,
            ended_at=meeting.ended_at,
            duration_seconds=meeting.duration_seconds,
            status=meeting.status,
            ai_note=meeting.ai_note,
            minutes=meeting.minutes,
            created_at=meeting.created_at,
            updated_at=meeting.updated_at,
            turns=[
                TurnItem(
                    id=t.id,
                    sequence=t.sequence,
                    speaker=t.speaker,
                    text=t.text,
                    speaker_id=t.speaker_id,
                    created_at=t.created_at,
                )
                for t in turns
            ],
            reply_suggestions=[
                ReplySuggestionItem(
                    id=s.id,
                    target_turn_id=s.target_turn_id,
                    sequence=s.sequence,
                    agent_id=s.agent_id,
                    agent_label=s.agent_label,
                    text=s.text,
                    created_at=s.created_at,
                )
                for s in suggestions
            ],
            recording_assets=[
                RecordingAssetItem(
                    id=a.id,
                    role=a.role,
                    format=a.format,
                    sample_rate=a.sample_rate,
                    channels=a.channels,
                    started_at=a.started_at,
                    ended_at=a.ended_at,
                    size_bytes=a.size_bytes,
                )
                for a in assets
            ],
        )

    # ── PATCH /meetings/{meeting_id} ───────────────────────────────────────────

    @router.patch("/meetings/{meeting_id}", response_model=UpdateMeetingTitleResponse)
    async def update_meeting_title(meeting_id: str, body: UpdateTitleRequest) -> UpdateMeetingTitleResponse:  # pyright: ignore[reportUnusedFunction]
        updated = await history_service.update_meeting_title(meeting_id, body.title)
        if not updated:
            raise HTTPException(status_code=404, detail="Meeting not found")
        return UpdateMeetingTitleResponse(ok=True)

    # ── DELETE /meetings/{meeting_id} ──────────────────────────────────────────

    @router.delete("/meetings/{meeting_id}", response_model=DeleteMeetingResponse)
    async def delete_meeting(meeting_id: str) -> DeleteMeetingResponse:  # pyright: ignore[reportUnusedFunction]
        try:
            deleted = await retention.delete_meeting_with_recordings(meeting_id)
        except RecordingCleanupError:
            logger.exception("Failed to delete recording directory for meeting %s", meeting_id)
            raise HTTPException(
                status_code=409,
                detail="録音ファイルを削除できなかったため、会議データは保持されています。再試行してください。",
            ) from None
        if not deleted:
            raise HTTPException(status_code=404, detail="Meeting not found")
        return DeleteMeetingResponse(ok=True)

    # ── GET /meetings/{meeting_id}/recordings ──────────────────────────────────

    @router.get(
        "/meetings/{meeting_id}/recordings",
        response_model=list[RecordingAssetItem],
    )
    async def list_recordings(meeting_id: str) -> list[RecordingAssetItem]:  # pyright: ignore[reportUnusedFunction]
        meeting = await history_service.repository.get_meeting(meeting_id)
        if meeting is None:
            raise HTTPException(status_code=404, detail="Meeting not found")

        assets = await history_service.repository.list_recording_assets(meeting_id)
        return [
            RecordingAssetItem(
                id=a.id,
                role=a.role,
                format=a.format,
                sample_rate=a.sample_rate,
                channels=a.channels,
                started_at=a.started_at,
                ended_at=a.ended_at,
                size_bytes=a.size_bytes,
            )
            for a in assets
        ]

    # ── GET /meetings/{meeting_id}/recordings/{role} ──────────────────────────

    @router.get("/meetings/{meeting_id}/recordings/{role}")
    async def serve_recording(meeting_id: str, role: RecordingRole) -> FileResponse:  # pyright: ignore[reportUnusedFunction]
        # FastAPI auto-validates role as Literal["other", "self"]; invalid values
        # produce a 422 validation error via the request validation layer.
        meeting = await history_service.repository.get_meeting(meeting_id)
        if meeting is None:
            raise HTTPException(status_code=404, detail="Meeting not found")

        asset = await history_service.repository.get_recording_asset_by_role(meeting_id, role)
        if asset is None:
            raise HTTPException(
                status_code=404,
                detail=f"No recording found for role '{role}' in meeting {meeting_id}",
            )

        file_path = _resolve_recording_path(user_data_dir, asset.relative_path)
        if not file_path.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"Recording file not found on disk: {file_path.name}",
            )

        media_type = _infer_media_type(asset.format)
        return FileResponse(path=str(file_path), media_type=media_type)

    return router


__all__ = ["create_router"]
