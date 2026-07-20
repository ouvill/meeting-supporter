"""Provider-aware HTTP endpoints for managed speech-model preparation."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from app.services.vosk_model_manager import CATALOG, ModelLanguage, SpeechModelStatus, VoskModelManager
from app.services.whisper_model_manager import DEFAULT_WHISPER_MODEL, WhisperModelAlias, WhisperModelManager

SpeechModelBackend = Literal["vosk", "whisper"]


class SpeechModelDownloadRequest(BaseModel):
    """Request a provider-controlled model without accepting arbitrary URLs or paths."""

    model_config = ConfigDict(extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    backend: SpeechModelBackend
    language: ModelLanguage
    model: WhisperModelAlias | None = None


class SpeechModelStatusResponse(BaseModel):
    """Stable public state for one provider-managed speech model."""

    backend: SpeechModelBackend
    model_id: str
    state: Literal["missing", "downloading", "ready", "failed", "cancelled"]
    phase: Literal["idle", "downloading", "verifying", "extracting", "ready"]
    language: Literal["ja", "en"]
    downloaded_bytes: int
    total_bytes: int | None
    progress_percent: int | None
    model_path: str | None
    storage_path: str
    error_code: Literal["network", "disk_full", "permission", "checksum", "archive", "cancelled", "unknown"] | None
    message: str
    retryable: bool
    cancelable: bool


def _response(
    *,
    backend: SpeechModelBackend,
    model_id: str,
    status: SpeechModelStatus,
) -> SpeechModelStatusResponse:
    return SpeechModelStatusResponse(
        backend=backend,
        model_id=model_id,
        state=status.state,
        phase=status.phase,
        language=status.language,
        downloaded_bytes=status.downloaded_bytes,
        total_bytes=status.total_bytes,
        progress_percent=status.progress_percent,
        model_path=status.model_path,
        storage_path=status.storage_path,
        error_code=status.error_code,
        message=status.message,
        retryable=status.retryable,
        cancelable=status.cancelable,
    )


def _whisper_model_id(model: WhisperModelAlias | None) -> WhisperModelAlias:
    return model or DEFAULT_WHISPER_MODEL


def _vosk_response(status: SpeechModelStatus) -> SpeechModelStatusResponse:
    return _response(backend="vosk", model_id=CATALOG[status.language].model_id, status=status)


def _whisper_response(model: WhisperModelAlias, status: SpeechModelStatus) -> SpeechModelStatusResponse:
    return _response(backend="whisper", model_id=model, status=status)


def create_router(
    *,
    vosk_model_manager: VoskModelManager,
    whisper_model_manager: WhisperModelManager,
) -> APIRouter:
    """Create provider-aware managed-model routes."""
    router = APIRouter(prefix="/api/stt", tags=["speech-model"])

    @router.get("/model")
    async def get_speech_model_status(  # pyright: ignore[reportUnusedFunction]
        backend: Annotated[SpeechModelBackend, Query()],
        language: Annotated[ModelLanguage, Query()],
        model: Annotated[WhisperModelAlias | None, Query()] = None,
    ) -> SpeechModelStatusResponse:
        if backend == "vosk":
            return _vosk_response(vosk_model_manager.status(language))
        whisper_model = _whisper_model_id(model)
        return _whisper_response(whisper_model, whisper_model_manager.status(language, whisper_model))

    @router.post("/model/download")
    async def start_speech_model_download(  # pyright: ignore[reportUnusedFunction]
        body: SpeechModelDownloadRequest,
    ) -> SpeechModelStatusResponse:
        if body.backend == "vosk":
            return _vosk_response(await vosk_model_manager.start(body.language))
        whisper_model = _whisper_model_id(body.model)
        return _whisper_response(whisper_model, await whisper_model_manager.start(body.language, whisper_model))

    @router.post("/model/cancel")
    async def cancel_speech_model_download(  # pyright: ignore[reportUnusedFunction]
        backend: Annotated[SpeechModelBackend, Query()],
        language: Annotated[ModelLanguage, Query()],
        model: Annotated[WhisperModelAlias | None, Query()] = None,
    ) -> SpeechModelStatusResponse:
        if backend == "vosk":
            return _vosk_response(await vosk_model_manager.cancel())
        whisper_model = _whisper_model_id(model)
        return _whisper_response(whisper_model, whisper_model_manager.cancel(language, whisper_model))

    return router


__all__ = [
    "SpeechModelBackend",
    "SpeechModelDownloadRequest",
    "SpeechModelStatusResponse",
    "create_router",
]
