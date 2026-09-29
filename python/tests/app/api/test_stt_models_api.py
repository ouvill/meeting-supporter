"""HTTP contracts for provider-aware speech-model preparation endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI

from app.api.stt_models import create_router
from app.services.speech_model_status import SpeechModelStatus
from app.services.whisper_model_manager import WhisperModelAlias
from tests.helpers.api_client import TypedTestClient


def _status(
    language: Literal["ja", "en"],
    *,
    state: Literal["missing", "downloading", "ready", "failed", "cancelled"],
    error_code: Literal["network", "disk_full", "permission", "checksum", "archive", "cancelled", "unknown"]
    | None = None,
    cancelable: bool | None = None,
) -> SpeechModelStatus:
    active = state == "downloading"
    ready = state == "ready"
    failed_or_cancelled = state in {"failed", "cancelled"}
    return SpeechModelStatus(
        state=state,
        phase="ready" if ready else ("downloading" if active else "idle"),
        language=language,
        downloaded_bytes=64 if active else 0,
        total_bytes=128 if active else None,
        progress_percent=50 if active else (100 if ready else None),
        model_path="/app-data/models/speech/managed" if ready else None,
        storage_path="/app-data",
        error_code=error_code,
        message=f"{state}:{language}",
        retryable=state == "missing" or failed_or_cancelled,
        cancelable=active if cancelable is None else cancelable,
    )


class _WhisperModelManagerForApi:
    """Deterministic fake for Whisper's independently prepared, non-cancellable models."""

    def __init__(self) -> None:
        self._statuses: dict[tuple[Literal["ja", "en"], WhisperModelAlias], SpeechModelStatus] = {}

    def status(self, language: Literal["ja", "en"], model: WhisperModelAlias) -> SpeechModelStatus:
        key = (language, model)
        status = self._statuses.get(key)
        if status is None:
            status = _status(language, state="missing", cancelable=False)
            self._statuses[key] = status
        return status

    async def start(self, language: Literal["ja", "en"], model: WhisperModelAlias) -> SpeechModelStatus:
        current = self.status(language, model)
        if current.state == "downloading":
            return current
        downloading = _status(language, state="downloading", cancelable=False)
        self._statuses[(language, model)] = downloading
        return downloading

    def cancel(self, language: Literal["ja", "en"], model: WhisperModelAlias) -> SpeechModelStatus:
        return self.status(language, model)


class _ReazonSpeechModelManagerForApi:
    """Deterministic fake for the fixed Japanese non-cancellable model."""

    def __init__(self) -> None:
        self._status: SpeechModelStatus = _status("ja", state="missing", cancelable=False)

    def status(self) -> SpeechModelStatus:
        return self._status

    async def start(self) -> SpeechModelStatus:
        self._status = _status("ja", state="downloading", cancelable=False)
        return self._status

    def cancel(self) -> SpeechModelStatus:
        return self._status


def _make_client() -> TypedTestClient:
    whisper_manager = _WhisperModelManagerForApi()
    reazonspeech_manager = _ReazonSpeechModelManagerForApi()
    app = FastAPI()
    app.include_router(
        create_router(
            whisper_model_manager=whisper_manager,  # pyright: ignore[reportArgumentType]
            reazonspeech_model_manager=reazonspeech_manager,  # pyright: ignore[reportArgumentType]
        )
    )
    return TypedTestClient(app)


class TestSpeechModelStatusApi:
    def test_rejects_invalid_provider_requests_and_whisper_aliases(self) -> None:
        client = _make_client()

        missing_backend = client.get("/api/stt/model?language=ja")
        unsupported_backend = client.get("/api/stt/model?backend=remote&language=ja")
        unsupported_language = client.get("/api/stt/model?backend=whisper&language=fr")
        arbitrary_url = client.post(
            "/api/stt/model/download",
            json={"backend": "whisper", "language": "ja", "url": "https://attacker.invalid/model.zip"},
        )
        invalid_alias_status = client.get("/api/stt/model?backend=whisper&language=ja&model=untrusted")
        invalid_alias_start = client.post(
            "/api/stt/model/download",
            json={"backend": "whisper", "language": "ja", "model": "untrusted"},
        )

        assert missing_backend.status_code == 422
        assert unsupported_backend.status_code == 422
        assert unsupported_language.status_code == 422
        assert arbitrary_url.status_code == 422
        assert invalid_alias_status.status_code == 422
        assert invalid_alias_start.status_code == 422

    def test_whisper_status_start_and_cancel_preserve_selected_model_and_download(self) -> None:
        client = _make_client()

        missing = client.get("/api/stt/model?backend=whisper&language=ja&model=small")
        started = client.post(
            "/api/stt/model/download",
            json={"backend": "whisper", "language": "ja", "model": "small"},
        )
        cancelled = client.post("/api/stt/model/cancel?backend=whisper&language=ja&model=small")

        assert missing.status_code == 200
        assert missing.json_object()["backend"] == "whisper"
        assert missing.json_object()["model_id"] == "small"
        assert missing.json_object()["state"] == "missing"
        assert started.status_code == 200
        assert started.json_object()["backend"] == "whisper"
        assert started.json_object()["model_id"] == "small"
        assert started.json_object()["state"] == "downloading"
        assert started.json_object()["cancelable"] is False
        assert cancelled.status_code == 200
        assert cancelled.json_object()["state"] == "downloading"
        assert cancelled.json_object()["cancelable"] is False

    def test_reazonspeech_is_fixed_to_the_japanese_int8_model(self) -> None:
        client = _make_client()

        missing = client.get("/api/stt/model?backend=reazonspeech&language=ja")
        started = client.post(
            "/api/stt/model/download",
            json={"backend": "reazonspeech", "language": "ja"},
        )
        cancelled = client.post("/api/stt/model/cancel?backend=reazonspeech&language=ja")

        assert missing.status_code == 200
        assert missing.json_object()["backend"] == "reazonspeech"
        assert missing.json_object()["model_id"] == "reazonspeech-k2-v2-int8"
        assert missing.json_object()["state"] == "missing"
        assert started.status_code == 200
        assert started.json_object()["state"] == "downloading"
        assert started.json_object()["cancelable"] is False
        assert cancelled.status_code == 200
        assert cancelled.json_object()["state"] == "downloading"

    def test_reazonspeech_rejects_non_japanese_requests(self) -> None:
        client = _make_client()

        status = client.get("/api/stt/model?backend=reazonspeech&language=en")
        start = client.post(
            "/api/stt/model/download",
            json={"backend": "reazonspeech", "language": "en"},
        )
        cancel = client.post("/api/stt/model/cancel?backend=reazonspeech&language=en")

        assert status.status_code == 422
        assert start.status_code == 422
        assert cancel.status_code == 422


def test_removed_vosk_backend_is_rejected() -> None:
    client = _make_client()
    assert client.get("/api/stt/model?backend=vosk&language=ja").status_code == 422
    assert client.post("/api/stt/model/download", json={"backend": "vosk", "language": "ja"}).status_code == 422
    assert client.post("/api/stt/model/cancel?backend=vosk&language=ja").status_code == 422
