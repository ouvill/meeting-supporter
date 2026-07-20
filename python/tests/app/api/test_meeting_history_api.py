"""Tests for app.api.meeting_history — GET/PATCH/DELETE /meetings endpoints."""

import asyncio
import tempfile
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI

from app.api.meeting_history import create_router
from app.meetings.history_models import (
    MeetingRecord,
    MeetingTurnRecord,
    RecordingAsset,
    ReplySuggestionRecord,
)
from app.meetings.service import MeetingHistoryService
from app.meetings.sqlite_repository import SqliteMeetingHistoryRepository
from tests.helpers.api_client import JsonObject, TypedResponse, TypedTestClient, as_json_array, as_object_array

# Reusable event loop for synchronous test helpers.
_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)


def _make_client(tmp_path: Path) -> tuple[TypedTestClient, MeetingHistoryService, SqliteMeetingHistoryRepository]:
    """Build a TypedTestClient with a fresh meeting history router."""
    repo = SqliteMeetingHistoryRepository(":memory:")
    _LOOP.run_until_complete(repo.initialize())

    service = MeetingHistoryService(repository=repo)
    app = FastAPI()
    router = create_router(
        history_service=service,
        user_data_dir=tmp_path,
    )
    app.include_router(router)
    return TypedTestClient(app), service, repo


def _run[T](coro: Awaitable[T]) -> T:
    """Run a coroutine synchronously."""
    return _LOOP.run_until_complete(coro)


def _meeting_items(resp: TypedResponse) -> list[JsonObject]:
    page = resp.json_object()
    return as_object_array(page["items"])


class TestListMeetings:
    def test_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _ = _make_client(Path(td))
            resp = client.get("/meetings")
            assert resp.status_code == 200
            assert resp.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}

    def test_list_returns_meetings_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))

            m1 = MeetingRecord(
                id="m1",
                started_at=datetime.now(UTC) - timedelta(hours=2),
                title="Older",
                status="completed",
            )
            m2 = MeetingRecord(
                id="m2",
                started_at=datetime.now(UTC) - timedelta(hours=1),
                title="Newer",
                status="active",
            )
            _run(service.repository.create_meeting(m1))
            _run(service.repository.create_meeting(m2))

            resp = client.get("/meetings")
            assert resp.status_code == 200
            data = _meeting_items(resp)
            assert len(data) == 2
            assert isinstance(data[0], dict)
            assert isinstance(data[1], dict)
            # Newer first.
            assert data[0]["id"] == "m2"
            assert data[1]["id"] == "m1"
            page = resp.json()
            assert isinstance(page, dict)
            assert page["total"] == 2
            assert page["limit"] == 50
            assert page["offset"] == 0

    def test_list_pagination_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))
            same_started = datetime.now(UTC)
            for idx in range(5):
                _run(
                    service.repository.create_meeting(
                        MeetingRecord(
                            id=f"m{idx}",
                            started_at=same_started,
                            title=f"Meeting {idx}",
                            status="completed",
                        )
                    )
                )

            resp = client.get("/meetings?limit=2&offset=2")
            assert resp.status_code == 200
            page = resp.json()
            assert isinstance(page, dict)
            data = as_json_array(page["items"])
            assert [item["id"] for item in data if isinstance(item, dict)] == ["m2", "m1"]
            assert page["total"] == 5
            assert page["limit"] == 2
            assert page["offset"] == 2

    def test_list_pagination_rejects_invalid_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _ = _make_client(Path(td))

            assert client.get("/meetings?limit=0").status_code == 422
            assert client.get("/meetings?limit=201").status_code == 422
            assert client.get("/meetings?offset=-1").status_code == 422

    def test_list_item_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))

            started = datetime.now(UTC) - timedelta(minutes=30)
            m = MeetingRecord(
                id="m-fields",
                started_at=started,
                ended_at=started + timedelta(minutes=15),
                duration_seconds=900,
                title="Test Meeting",
                status="completed",
            )
            _run(service.repository.create_meeting(m))

            resp = client.get("/meetings")
            data = _meeting_items(resp)
            item = data[0]
            assert item["id"] == "m-fields"
            assert item["title"] == "Test Meeting"
            assert item["status"] == "completed"
            assert item["duration_seconds"] == 900
            assert item["started_at"] is not None
            assert item["ended_at"] is not None
            assert item["has_ai_note"] is False  # ai_note was empty
            assert item["has_recording"] is False  # no recording assets

    def test_list_item_has_ai_note(self) -> None:
        """Meeting with ai_note set returns has_ai_note=True."""
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))

            m = MeetingRecord(
                id="m-ai",
                started_at=datetime.now(UTC),
                title="With AI Note",
                status="completed",
                ai_note="AI summary content",
            )
            _run(service.repository.create_meeting(m))

            resp = client.get("/meetings")
            data = _meeting_items(resp)
            item = data[0]
            assert item["has_ai_note"] is True

    def test_list_item_has_recording(self) -> None:
        """Meeting with recording assets returns has_recording=True."""
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))

            m = MeetingRecord(
                id="m-rec",
                started_at=datetime.now(UTC),
                title="With Recording",
                status="completed",
            )
            _run(service.repository.create_meeting(m))

            # Add a recording asset.
            asset = RecordingAsset(
                id="r-test",
                meeting_id="m-rec",
                role="other",
                relative_path="recordings/other.wav",
                started_at=datetime.now(UTC),
            )
            _run(service.repository.insert_recording_assets([asset]))

            resp = client.get("/meetings")
            data = _meeting_items(resp)
            item = data[0]
            assert item["has_recording"] is True


class TestGetMeetingDetail:
    def test_get_nonexistent_returns_404(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _ = _make_client(Path(td))
            resp = client.get("/meetings/nonexistent")
            assert resp.status_code == 404

    def test_get_meeting_with_related_data(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))

            started = datetime.now(UTC) - timedelta(minutes=10)
            m = MeetingRecord(
                id="m-detail",
                started_at=started,
                ended_at=started + timedelta(minutes=5),
                duration_seconds=300,
                title="Detail Test",
                status="completed",
                ai_note="AI summary",
                minutes="## 決定事項\n見積もりを送る",
            )
            _run(service.repository.create_meeting(m))

            turn = MeetingTurnRecord(id="t1", meeting_id="m-detail", sequence=1, speaker="other", text="Hello")
            _run(service.repository.insert_turn(turn))

            sug = ReplySuggestionRecord(
                id="s1",
                meeting_id="m-detail",
                target_turn_id="t1",
                sequence=1,
                agent_id="agent_a",
                agent_label="Agent A",
                text="Reply",
            )
            _run(service.repository.insert_reply_suggestion(sug))

            asset = RecordingAsset(
                id="r1",
                meeting_id="m-detail",
                role="other",
                relative_path="recordings/other.wav",
                started_at=datetime.now(UTC),
            )
            _run(service.repository.insert_recording_assets([asset]))

            resp = client.get("/meetings/m-detail")
            assert resp.status_code == 200
            data = resp.json_object()
            assert data["id"] == "m-detail"
            assert data["title"] == "Detail Test"
            assert data["status"] == "completed"
            assert data["ai_note"] == "AI summary"
            assert data["minutes"] == "## 決定事項\n見積もりを送る"
            assert data["duration_seconds"] == 300

            turns = as_json_array(data["turns"])
            assert len(turns) == 1
            assert isinstance(turns[0], dict)
            assert turns[0]["id"] == "t1"
            assert turns[0]["text"] == "Hello"

            reply_suggestions = as_json_array(data["reply_suggestions"])
            assert len(reply_suggestions) == 1
            assert isinstance(reply_suggestions[0], dict)
            assert reply_suggestions[0]["id"] == "s1"
            assert reply_suggestions[0]["text"] == "Reply"

            recording_assets = as_json_array(data["recording_assets"])
            assert len(recording_assets) == 1
            assert isinstance(recording_assets[0], dict)
            assert recording_assets[0]["id"] == "r1"
            assert recording_assets[0]["role"] == "other"

    def test_get_meeting_empty_lists_for_no_related_data(self) -> None:
        """A meeting with no turns/suggestions/assets still returns empty lists."""
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))

            m = MeetingRecord(
                id="m-empty",
                started_at=datetime.now(UTC),
                status="active",
            )
            _run(service.repository.create_meeting(m))

            resp = client.get("/meetings/m-empty")
            assert resp.status_code == 200
            data = resp.json_object()
            assert data["id"] == "m-empty"
            assert data["turns"] == []
            assert data["reply_suggestions"] == []
            assert data["recording_assets"] == []


class TestUpdateMeetingTitle:
    def test_update_title(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))

            m = MeetingRecord(id="m-upd", started_at=datetime.now(UTC), title="Old Title")
            _run(service.repository.create_meeting(m))

            resp = client.patch("/meetings/m-upd", json={"title": "New Title"})
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

            fetched = _run(service.repository.get_meeting("m-upd"))
            assert fetched is not None
            assert fetched.title == "New Title"

    def test_update_nonexistent_returns_404(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _ = _make_client(Path(td))
            resp = client.patch("/meetings/nonexistent", json={"title": "Noop"})
            assert resp.status_code == 404


class TestDeleteMeeting:
    def test_delete_meeting_db_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))

            m = MeetingRecord(id="m-del", started_at=datetime.now(UTC))
            _run(service.repository.create_meeting(m))

            resp = client.delete("/meetings/m-del")
            assert resp.status_code == 200
            data = resp.json_object()
            assert data["ok"] is True
            assert data["warning"] is None

            # Verify DB deletion.
            fetched = _run(service.repository.get_meeting("m-del"))
            assert fetched is None

    def test_delete_meeting_with_recording_dir(self) -> None:
        """When a recordings directory exists, it is removed."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, service, _ = _make_client(tmp)

            m = MeetingRecord(id="m-rec-del", started_at=datetime.now(UTC))
            _run(service.repository.create_meeting(m))

            # Create a fake recordings directory.
            rec_dir = tmp / "recordings" / "m-rec-del"
            rec_dir.mkdir(parents=True)
            _ = (rec_dir / "other.wav").write_text("fake audio data")
            assert rec_dir.exists()

            resp = client.delete("/meetings/m-rec-del")
            assert resp.status_code == 200
            data = resp.json_object()
            assert data["ok"] is True
            assert data["warning"] is None  # File deletion succeeded
            assert not rec_dir.exists()  # Directory was removed

    def test_delete_nonexistent_returns_404(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _ = _make_client(Path(td))
            resp = client.delete("/meetings/nonexistent")
            assert resp.status_code == 404

    def test_delete_with_cascade(self) -> None:
        """Deleting a meeting cascades to turns, suggestions, assets."""
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))

            m = MeetingRecord(id="m-cascade-del", started_at=datetime.now(UTC))
            _run(service.repository.create_meeting(m))

            turn = MeetingTurnRecord(
                id="t-cascade",
                meeting_id="m-cascade-del",
                sequence=1,
                speaker="other",
                text="x",
            )
            _run(service.repository.insert_turn(turn))

            sug = ReplySuggestionRecord(
                id="s-cascade",
                meeting_id="m-cascade-del",
                target_turn_id="t-cascade",
                sequence=1,
                agent_id="a",
                agent_label="A",
                text="x",
            )
            _run(service.repository.insert_reply_suggestion(sug))

            asset = RecordingAsset(
                id="r-cascade",
                meeting_id="m-cascade-del",
                role="other",
                relative_path="x.wav",
                started_at=datetime.now(UTC),
            )
            _run(service.repository.insert_recording_assets([asset]))

            resp = client.delete("/meetings/m-cascade-del")
            assert resp.status_code == 200

            # Verify cascade.
            turns = _run(service.repository.list_turns("m-cascade-del"))
            assert turns == []
            suggestions = _run(service.repository.list_reply_suggestions("m-cascade-del"))
            assert suggestions == []
            assets = _run(service.repository.list_recording_assets("m-cascade-del"))
            assert assets == []


class TestRecordingCleanup:
    @staticmethod
    def _create_recording_meeting(
        service: MeetingHistoryService,
        user_data_dir: Path,
        *,
        meeting_id: str,
        status: str,
        ended_at: datetime | None,
        recording_size: int,
    ) -> None:
        started_at = datetime(2025, 1, 1, tzinfo=UTC)
        _run(
            service.repository.create_meeting(
                MeetingRecord(
                    id=meeting_id,
                    started_at=started_at,
                    ended_at=ended_at,
                    status=status,  # pyright: ignore[reportArgumentType]  # fixture status is one of the persisted literals
                )
            )
        )
        _run(
            service.repository.insert_recording_assets(
                [
                    RecordingAsset(
                        id=f"asset-{meeting_id}",
                        meeting_id=meeting_id,
                        role="other",
                        relative_path=f"recordings/{meeting_id}/other.wav",
                        started_at=started_at,
                        size_bytes=recording_size,
                    )
                ]
            )
        )
        recording_dir = user_data_dir / "recordings" / meeting_id
        recording_dir.mkdir(parents=True)
        _ = (recording_dir / "other.wav").write_bytes(b"x" * recording_size)

    def test_preview_cutoff_excludes_active_and_does_not_mutate_storage(self) -> None:
        """Cutoff preview selects only completed meetings before UTC midnight and leaves them intact."""
        with tempfile.TemporaryDirectory() as td:
            user_data_dir = Path(td)
            client, service, _ = _make_client(user_data_dir)
            self._create_recording_meeting(
                service,
                user_data_dir,
                meeting_id="old",
                status="completed",
                ended_at=datetime(2025, 1, 31, 23, 59, tzinfo=UTC),
                recording_size=30,
            )
            self._create_recording_meeting(
                service,
                user_data_dir,
                meeting_id="cutoff-boundary",
                status="completed",
                ended_at=datetime(2025, 2, 1, tzinfo=UTC),
                recording_size=20,
            )
            self._create_recording_meeting(
                service,
                user_data_dir,
                meeting_id="active",
                status="active",
                ended_at=None,
                recording_size=90,
            )

            response = client.post(
                "/meetings/recordings/cleanup/preview",
                json={"cutoff_date": "2025-02-01"},
            )

            assert response.status_code == 200
            preview = response.json_object()
            assert preview["candidate_meeting_ids"] == ["old"]
            assert preview["delete_count"] == 1
            assert preview["delete_recording_bytes"] == 30
            assert preview["total_recording_bytes_before"] == 50
            assert preview["total_recording_bytes_after"] == 20
            for meeting_id in ("old", "cutoff-boundary", "active"):
                assert _run(service.repository.get_meeting(meeting_id)) is not None
                assert (user_data_dir / "recordings" / meeting_id).is_dir()

    def test_preview_capacity_removes_oldest_completed_recordings_until_within_budget(self) -> None:
        """Capacity preview sums persisted bytes and chooses completed recordings oldest first."""
        with tempfile.TemporaryDirectory() as td:
            user_data_dir = Path(td)
            client, service, _ = _make_client(user_data_dir)
            for meeting_id, ended_at, recording_size in (
                ("oldest", datetime(2025, 1, 1, tzinfo=UTC), 40),
                ("middle", datetime(2025, 1, 2, tzinfo=UTC), 35),
                ("newest", datetime(2025, 1, 3, tzinfo=UTC), 30),
            ):
                self._create_recording_meeting(
                    service,
                    user_data_dir,
                    meeting_id=meeting_id,
                    status="completed",
                    ended_at=ended_at,
                    recording_size=recording_size,
                )
            self._create_recording_meeting(
                service,
                user_data_dir,
                meeting_id="active-large",
                status="active",
                ended_at=None,
                recording_size=500,
            )

            response = client.post(
                "/meetings/recordings/cleanup/preview",
                json={"max_total_bytes": 34},
            )

            assert response.status_code == 200
            preview = response.json_object()
            assert preview["candidate_meeting_ids"] == ["oldest", "middle"]
            assert preview["delete_count"] == 2
            assert preview["delete_recording_bytes"] == 75
            assert preview["total_recording_bytes_before"] == 105
            assert preview["total_recording_bytes_after"] == 30

    def test_preview_combines_cutoff_and_capacity_without_double_counting_candidates(self) -> None:
        """Cutoff removals are retained while capacity removes additional oldest recordings."""
        with tempfile.TemporaryDirectory() as td:
            user_data_dir = Path(td)
            client, service, _ = _make_client(user_data_dir)
            for meeting_id, ended_at, recording_size in (
                ("cutoff", datetime(2025, 1, 1, tzinfo=UTC), 40),
                ("remaining-old", datetime(2025, 2, 2, tzinfo=UTC), 40),
                ("remaining-new", datetime(2025, 2, 3, tzinfo=UTC), 20),
            ):
                self._create_recording_meeting(
                    service,
                    user_data_dir,
                    meeting_id=meeting_id,
                    status="completed",
                    ended_at=ended_at,
                    recording_size=recording_size,
                )

            response = client.post(
                "/meetings/recordings/cleanup/preview",
                json={"cutoff_date": "2025-02-01", "max_total_bytes": 50},
            )

            assert response.status_code == 200
            preview = response.json_object()
            assert preview["candidate_meeting_ids"] == ["cutoff", "remaining-old"]
            assert preview["delete_count"] == 2
            assert preview["delete_recording_bytes"] == 80
            assert preview["total_recording_bytes_before"] == 100
            assert preview["total_recording_bytes_after"] == 20

    def test_execute_removes_selected_files_before_their_database_rows(self) -> None:
        """Explicit execution deletes selected recording directories and their matching completed rows only."""
        with tempfile.TemporaryDirectory() as td:
            user_data_dir = Path(td)
            client, service, _ = _make_client(user_data_dir)
            self._create_recording_meeting(
                service,
                user_data_dir,
                meeting_id="old",
                status="completed",
                ended_at=datetime(2025, 1, 1, tzinfo=UTC),
                recording_size=60,
            )
            self._create_recording_meeting(
                service,
                user_data_dir,
                meeting_id="new",
                status="completed",
                ended_at=datetime(2025, 1, 2, tzinfo=UTC),
                recording_size=10,
            )
            self._create_recording_meeting(
                service,
                user_data_dir,
                meeting_id="active",
                status="active",
                ended_at=None,
                recording_size=500,
            )

            response = client.post("/meetings/recordings/cleanup", json={"max_total_bytes": 20})

            assert response.status_code == 200
            result = response.json_object()
            assert result["candidate_meeting_ids"] == ["old"]
            assert result["delete_count"] == 1
            assert result["delete_recording_bytes"] == 60
            assert result["total_recording_bytes_before"] == 70
            assert result["total_recording_bytes_after"] == 10
            assert result["deleted_meeting_ids"] == ["old"]
            assert result["failed_meeting_ids"] == []
            assert result["skipped_meeting_ids"] == []
            assert _run(service.repository.get_meeting("old")) is None
            assert not (user_data_dir / "recordings" / "old").exists()
            assert _run(service.repository.get_meeting("new")) is not None
            assert (user_data_dir / "recordings" / "new").is_dir()
            assert _run(service.repository.get_meeting("active")) is not None
            assert (user_data_dir / "recordings" / "active").is_dir()

    def test_manual_delete_keeps_database_row_when_recording_removal_fails(self) -> None:
        """A failed rmtree surfaces a conflict and preserves the meeting for a safe retry."""
        with tempfile.TemporaryDirectory() as td:
            user_data_dir = Path(td)
            client, service, _ = _make_client(user_data_dir)
            self._create_recording_meeting(
                service,
                user_data_dir,
                meeting_id="locked",
                status="completed",
                ended_at=datetime(2025, 1, 1, tzinfo=UTC),
                recording_size=10,
            )

            with patch("app.meetings.recording_retention.shutil.rmtree", side_effect=OSError("locked")):
                response = client.delete("/meetings/locked")

            assert response.status_code == 409
            assert _run(service.repository.get_meeting("locked")) is not None
            assert (user_data_dir / "recordings" / "locked" / "other.wav").is_file()


class TestListRecordings:
    def test_list_recordings_empty(self) -> None:
        """A meeting with no recording assets returns an empty list."""
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))

            m = MeetingRecord(id="m-no-rec", started_at=datetime.now(UTC))
            _run(service.repository.create_meeting(m))

            resp = client.get("/meetings/m-no-rec/recordings")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_list_recordings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))

            m = MeetingRecord(id="m-rec", started_at=datetime.now(UTC))
            _run(service.repository.create_meeting(m))

            asset1 = RecordingAsset(
                id="r1",
                meeting_id="m-rec",
                role="other",
                relative_path="recordings/other.wav",
                started_at=datetime.now(UTC),
                size_bytes=1000,
            )
            asset2 = RecordingAsset(
                id="r2",
                meeting_id="m-rec",
                role="self",
                relative_path="recordings/self.wav",
                started_at=datetime.now(UTC),
                size_bytes=2000,
            )
            _run(service.repository.insert_recording_assets([asset1, asset2]))

            resp = client.get("/meetings/m-rec/recordings")
            assert resp.status_code == 200
            data = resp.json_array()
            assert len(data) == 2
            roles: set[str] = set()
            for d in data:
                assert isinstance(d, dict)
                role = d["role"]
                assert isinstance(role, str)
                roles.add(role)
            assert roles == {"other", "self"}

    def test_list_recordings_nonexistent_meeting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _ = _make_client(Path(td))
            resp = client.get("/meetings/nonexistent/recordings")
            assert resp.status_code == 404


class TestServeRecording:
    def test_serve_nonexistent_meeting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client, _, _ = _make_client(Path(td))
            resp = client.get("/meetings/nonexistent/recordings/other")
            assert resp.status_code == 404

    def test_serve_invalid_role(self) -> None:
        """Invalid role returns 422 (FastAPI validation rejects the Literal type)."""
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))

            m = MeetingRecord(id="m-bad-role", started_at=datetime.now(UTC))
            _run(service.repository.create_meeting(m))

            resp = client.get("/meetings/m-bad-role/recordings/invalid")
            assert resp.status_code == 422

    def test_serve_missing_asset(self) -> None:
        """Meeting exists but no recording asset for role."""
        with tempfile.TemporaryDirectory() as td:
            client, service, _ = _make_client(Path(td))

            m = MeetingRecord(id="m-no-asset", started_at=datetime.now(UTC))
            _run(service.repository.create_meeting(m))

            resp = client.get("/meetings/m-no-asset/recordings/other")
            assert resp.status_code == 404

    def test_serve_missing_file(self) -> None:
        """Asset record exists but file not on disk."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, service, _ = _make_client(tmp)

            m = MeetingRecord(id="m-missing-file", started_at=datetime.now(UTC))
            _run(service.repository.create_meeting(m))

            asset = RecordingAsset(
                id="r-missing",
                meeting_id="m-missing-file",
                role="other",
                relative_path="recordings/missing_file.wav",
                started_at=datetime.now(UTC),
            )
            _run(service.repository.insert_recording_assets([asset]))

            resp = client.get("/meetings/m-missing-file/recordings/other")
            assert resp.status_code == 404

    def test_serve_recording_success(self) -> None:
        """Successful audio file serving returns FileResponse with correct media type."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, service, _ = _make_client(tmp)

            m = MeetingRecord(id="m-serve", started_at=datetime.now(UTC))
            _run(service.repository.create_meeting(m))

            # Create actual file.
            rec_dir = tmp / "recordings" / "m-serve"
            rec_dir.mkdir(parents=True)
            rec_file = rec_dir / "other.wav"
            _ = rec_file.write_bytes(b"RIFFfakewavdata")

            asset = RecordingAsset(
                id="r-serve",
                meeting_id="m-serve",
                role="other",
                relative_path="recordings/m-serve/other.wav",
                started_at=datetime.now(UTC),
                format="wav",
            )
            _run(service.repository.insert_recording_assets([asset]))

            resp = client.get("/meetings/m-serve/recordings/other")
            assert resp.status_code == 200
            assert resp.headers.get("content-type") == "audio/wav"
            assert resp.content == b"RIFFfakewavdata"

    def test_serve_rejects_sibling_prefix_traversal(self) -> None:
        """A relative path escaping to a sibling-prefix directory is rejected.

        e.g. when base_dir is /tmp/tmpXXXXXX, a relative_path like
        ``../tmpXXXXXX_evil/file.wav`` resolves to ``/tmp/tmpXXXXXX_evil/file.wav``,
        which is *not* under base_dir.  The old ``startswith`` string check
        would incorrectly accept it (sibling names share the same prefix).
        The ``Path.relative_to()`` containment check correctly rejects it.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # Create a sibling directory whose name is a prefix-extension of tmp.
            sibling_dir = tmp.parent / f"{tmp.name}_evil"
            sibling_dir.mkdir(parents=True, exist_ok=True)
            _ = (sibling_dir / "file.wav").write_text("data")
            try:
                client, service, _ = _make_client(tmp)

                m = MeetingRecord(id="m-sibling", started_at=datetime.now(UTC))
                _run(service.repository.create_meeting(m))

                # relative_path that would traverse into the sibling directory.
                asset = RecordingAsset(
                    id="r-sibling",
                    meeting_id="m-sibling",
                    role="other",
                    relative_path=f"../{tmp.name}_evil/file.wav",
                    started_at=datetime.now(UTC),
                )
                _run(service.repository.insert_recording_assets([asset]))

                resp = client.get("/meetings/m-sibling/recordings/other")
                # Must be rejected by path containment check, never served.
                assert resp.status_code == 400
                assert "Invalid recording path" in resp.text
            finally:
                # Clean up the sibling directory we created outside the tempdir.
                import shutil

                shutil.rmtree(sibling_dir, ignore_errors=True)
