"""Shared status contract for locally cached speech models."""

from dataclasses import dataclass
from typing import Literal

ModelLanguage = Literal["ja", "en"]
ModelState = Literal["missing", "downloading", "ready", "failed", "cancelled"]
ModelPhase = Literal["idle", "downloading", "verifying", "extracting", "ready"]
ModelErrorCode = Literal["network", "disk_full", "permission", "checksum", "archive", "cancelled", "unknown"]


@dataclass(frozen=True)
class SpeechModelStatus:
    """The public, provider-neutral state of one managed speech model."""

    state: ModelState
    phase: ModelPhase
    language: ModelLanguage
    downloaded_bytes: int
    total_bytes: int | None
    progress_percent: int | None
    model_path: str | None
    storage_path: str
    error_code: ModelErrorCode | None
    message: str
    retryable: bool
    cancelable: bool
