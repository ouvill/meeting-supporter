"""HTTP contracts for provider-aware speech-model preparation endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI

from app.api.stt_models import create_router
from app.services.vosk_model_manager import SpeechModelStatus
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


class _VoskModelManagerForApi:
    """Deterministic fake for Vosk's single cancellable download."""

    def __init__(self) -> None:
        self._statuses: dict[Literal["ja", "en"], SpeechModelStatus] = {
            "ja": _status("ja", state="missing"),
            "en": _status("en", state="missing"),
        }
        self._active_language: Literal["ja", "en"] | None = None

    def status(self, language: Literal["ja", "en"]) -> SpeechModelStatus:
        return self._statuses[language]

    async def start(self, language: Literal["ja", "en"]) -> SpeechModelStatus:
        if self._active_language is not None:
            return self._statuses[self._active_language]
        downloading = _status(language, state="downloading")
        self._statuses[language] = downloading
        self._active_language = language
        return downloading

    async def cancel(self) -> SpeechModelStatus:
        if self._active_language is None:
            return self._statuses["ja"]
        language = self._active_language
        cancelled = _status(language, state="cancelled", error_code="cancelled")
        self._statuses[language] = cancelled
        self._active_language = None
        return cancelled

    def fail_active_download(self, code: Literal["network", "checksum"]) -> None:
        if self._active_language is None:
            raise AssertionError("An active download is required to expose a download failure")
        language = self._active_language
        self._statuses[language] = _status(language, state="failed", error_code=code)
        self._active_language = None


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


def _make_client() -> tuple[TypedTestClient, _VoskModelManagerForApi, _WhisperModelManagerForApi]:
    vosk_manager = _VoskModelManagerForApi()
    whisper_manager = _WhisperModelManagerForApi()
    reazonspeech_manager = _ReazonSpeechModelManagerForApi()
    app = FastAPI()
    app.include_router(
        create_router(
            vosk_model_manager=vosk_manager,  # pyright: ignore[reportArgumentType]
            whisper_model_manager=whisper_manager,  # pyright: ignore[reportArgumentType]
            reazonspeech_model_manager=reazonspeech_manager,  # pyright: ignore[reportArgumentType]
        )
    )
    return TypedTestClient(app), vosk_manager, whisper_manager


class TestSpeechModelStatusApi:
    def test_vosk_status_returns_provider_and_catalog_model_identity(self) -> None:
        client, _, _ = _make_client()

        response = client.get("/api/stt/model?backend=vosk&language=ja")

        assert response.status_code == 200
        status = response.json_object()
        assert status["backend"] == "vosk"
        assert status["model_id"] == "vosk-model-small-ja-0.22"
        assert status["state"] == "missing"
        assert status["language"] == "ja"
        assert status["storage_path"] == "/app-data"
        assert status["model_path"] is None
        assert status["error_code"] is None
        assert status["retryable"] is True
        assert "url" not in status

    def test_rejects_invalid_provider_requests_and_whisper_aliases(self) -> None:
        client, _, _ = _make_client()

        missing_backend = client.get("/api/stt/model?language=ja")
        unsupported_backend = client.get("/api/stt/model?backend=remote&language=ja")
        unsupported_language = client.get("/api/stt/model?backend=vosk&language=fr")
        arbitrary_url = client.post(
            "/api/stt/model/download",
            json={"backend": "vosk", "language": "ja", "url": "https://attacker.invalid/model.zip"},
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

    def test_vosk_start_deduplicates_active_download_and_cancel_is_actionable_and_idempotent(self) -> None:
        client, _, _ = _make_client()

        first_start = client.post("/api/stt/model/download", json={"backend": "vosk", "language": "ja"})
        duplicate_start = client.post("/api/stt/model/download", json={"backend": "vosk", "language": "en"})
        first_cancel = client.post("/api/stt/model/cancel?backend=vosk&language=ja")
        repeated_cancel = client.post("/api/stt/model/cancel?backend=vosk&language=ja")

        assert first_start.status_code == 200
        assert duplicate_start.status_code == 200
        assert first_start.json_object()["state"] == "downloading"
        assert first_start.json_object()["cancelable"] is True
        assert duplicate_start.json_object()["language"] == "ja"
        assert duplicate_start.json_object()["state"] == "downloading"
        assert first_cancel.status_code == 200
        assert first_cancel.json_object()["state"] == "cancelled"
        assert first_cancel.json_object()["error_code"] == "cancelled"
        assert first_cancel.json_object()["retryable"] is True
        assert first_cancel.json_object()["cancelable"] is False
        assert repeated_cancel.json_object() == first_cancel.json_object()

    def test_vosk_failure_exposes_retryable_error_and_retry_returns_downloading_state(self) -> None:
        client, manager, _ = _make_client()

        started = client.post("/api/stt/model/download", json={"backend": "vosk", "language": "en"})
        assert started.json_object()["state"] == "downloading"
        manager.fail_active_download("network")

        failed = client.get("/api/stt/model?backend=vosk&language=en")
        retry = client.post("/api/stt/model/download", json={"backend": "vosk", "language": "en"})

        assert failed.status_code == 200
        assert failed.json_object()["state"] == "failed"
        assert failed.json_object()["error_code"] == "network"
        assert failed.json_object()["retryable"] is True
        assert failed.json_object()["cancelable"] is False
        assert retry.status_code == 200
        assert retry.json_object()["state"] == "downloading"
        assert retry.json_object()["language"] == "en"
        assert retry.json_object()["error_code"] is None

    def test_whisper_status_start_and_cancel_preserve_selected_model_and_download(self) -> None:
        client, _, _ = _make_client()

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
        client, _, _ = _make_client()

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
        client, _, _ = _make_client()

        status = client.get("/api/stt/model?backend=reazonspeech&language=en")
        start = client.post(
            "/api/stt/model/download",
            json={"backend": "reazonspeech", "language": "en"},
        )
        cancel = client.post("/api/stt/model/cancel?backend=reazonspeech&language=en")

        assert status.status_code == 422
        assert start.status_code == 422
        assert cancel.status_code == 422
