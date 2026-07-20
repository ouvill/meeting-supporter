"""Meeting-scoped context/reference persistence and parsing."""

from __future__ import annotations

import base64
import importlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Protocol, cast

from app.core.messages import MeetingContextPayload, ReferenceDocumentPayload
from app.meetings.models import MeetingContext, ReferenceDocument

_MAX_INLINE_TEXT_CHARS = 40_000
_SUPPORTED_TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}
_SUPPORTED_EXTENSIONS = _SUPPORTED_TEXT_EXTENSIONS | {".docx"}


class _MarkItDownResult(Protocol):
    text_content: str


class _MarkItDownConverter(Protocol):
    def convert(self, path: str) -> _MarkItDownResult: ...


class _MarkItDownFactory(Protocol):
    def __call__(self, *, enable_plugins: bool) -> _MarkItDownConverter: ...


def context_from_payload(payload: MeetingContextPayload | None) -> MeetingContext:
    if payload is None:
        return MeetingContext()
    return MeetingContext(
        scenario=payload.scenario.strip() or "会議",
        user_role=payload.userRole.strip() or "会議メンバー",
        counterpart_role=(payload.counterpartRole or "").strip(),
        objective=payload.objective.strip() or "目的未設定",
        background=(payload.background or "").strip(),
        tone=(payload.tone or "").strip() or "簡潔で自然",
        constraints=(payload.constraints or "").strip(),
        custom_instructions=(payload.customInstructions or "").strip(),
    )


def _safe_document_id(document_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in document_id)[:80] or "document"


def _extension(name: str) -> str:
    return Path(name).suffix.lower()


def _parse_text_document(payload: ReferenceDocumentPayload) -> ReferenceDocument:
    text = payload.text or ""
    return ReferenceDocument(
        id=_safe_document_id(payload.id),
        name=payload.name,
        mime_type=payload.mimeType,
        size_bytes=payload.sizeBytes,
        status="parsed",
        text=text[:_MAX_INLINE_TEXT_CHARS],
    )


def _parse_docx_document(payload: ReferenceDocumentPayload) -> ReferenceDocument:
    if not payload.contentBase64:
        return ReferenceDocument(
            id=_safe_document_id(payload.id),
            name=payload.name,
            mime_type=payload.mimeType,
            size_bytes=payload.sizeBytes,
            status="failed",
            error="docx content is missing",
        )
    try:
        markitdown_module = importlib.import_module("markitdown")
        markitdown_symbol = cast(object, markitdown_module.__dict__["MarkItDown"])
        markitdown_factory = cast(_MarkItDownFactory, markitdown_symbol)
    except ImportError:
        return ReferenceDocument(
            id=_safe_document_id(payload.id),
            name=payload.name,
            mime_type=payload.mimeType,
            size_bytes=payload.sizeBytes,
            status="failed",
            error="markitdown[docx] is not installed",
        )

    raw = base64.b64decode(payload.contentBase64, validate=True)
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=True) as tmp:
        _ = tmp.write(raw)
        tmp.flush()
        result_text = markitdown_factory(enable_plugins=False).convert(tmp.name).text_content
    return ReferenceDocument(
        id=_safe_document_id(payload.id),
        name=payload.name,
        mime_type=payload.mimeType,
        size_bytes=payload.sizeBytes,
        status="parsed",
        text=result_text[:_MAX_INLINE_TEXT_CHARS],
    )


def parse_reference_payloads(payloads: list[ReferenceDocumentPayload]) -> tuple[ReferenceDocument, ...]:
    documents: list[ReferenceDocument] = []
    for payload in payloads:
        ext = _extension(payload.name)
        if ext not in _SUPPORTED_EXTENSIONS:
            documents.append(
                ReferenceDocument(
                    id=_safe_document_id(payload.id),
                    name=payload.name,
                    mime_type=payload.mimeType,
                    size_bytes=payload.sizeBytes,
                    status="failed",
                    error="unsupported file type",
                )
            )
            continue
        try:
            if ext in _SUPPORTED_TEXT_EXTENSIONS:
                documents.append(_parse_text_document(payload))
            else:
                documents.append(_parse_docx_document(payload))
        except Exception:
            documents.append(
                ReferenceDocument(
                    id=_safe_document_id(payload.id),
                    name=payload.name,
                    mime_type=payload.mimeType,
                    size_bytes=payload.sizeBytes,
                    status="failed",
                    error="parse failed",
                )
            )
    return tuple(documents)


def persist_meeting_context(user_data_dir: Path, meeting_id: str, context: MeetingContext) -> None:
    meeting_dir = user_data_dir / "meetings" / meeting_id
    meeting_dir.mkdir(parents=True, exist_ok=True)
    _ = (meeting_dir / "context.json").write_text(
        json.dumps(asdict(context), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def persist_reference_documents(user_data_dir: Path, meeting_id: str, documents: tuple[ReferenceDocument, ...]) -> None:
    references_dir = user_data_dir / "meetings" / meeting_id / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    for document in documents:
        doc_dir = references_dir / _safe_document_id(document.id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        metadata: dict[str, object] = {
            "id": document.id,
            "name": document.name,
            "mime_type": document.mime_type,
            "size_bytes": document.size_bytes,
            "status": document.status,
            "error": document.error,
        }
        _ = (doc_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        if document.status == "parsed":
            suffix = ".md" if _extension(document.name) in {".md", ".markdown", ".docx"} else ".txt"
            _ = (doc_dir / f"parsed{suffix}").write_text(document.text, encoding="utf-8")


__all__ = [
    "context_from_payload",
    "parse_reference_payloads",
    "persist_meeting_context",
    "persist_reference_documents",
]
