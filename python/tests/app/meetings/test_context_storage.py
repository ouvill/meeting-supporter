"""Tests for meeting context normalization and reference persistence."""

import json
import tempfile
import unittest
from pathlib import Path
from typing import cast

from app.core.messages import MeetingContextPayload, ReferenceDocumentPayload
from app.meetings.context_storage import (
    context_from_payload,
    parse_reference_payloads,
    persist_reference_documents,
)
from app.meetings.models import MeetingContext, ReferenceDocument


class ContextStorageTest(unittest.TestCase):
    def test_context_payload_is_trimmed_and_blank_prompt_fields_fall_back_to_domain_defaults(self) -> None:
        payload = MeetingContextPayload(
            scenario="  ",
            userRole="  Facilitator  ",
            counterpartRole="  Customer  ",
            objective="\n\t",
            background="  Migration status  ",
            tone="   ",
            constraints="  Stay under ten minutes  ",
            customInstructions="  Mention risks first  ",
        )

        context = context_from_payload(payload)
        defaults = MeetingContext()

        self.assertEqual(defaults.scenario, context.scenario)
        self.assertEqual("Facilitator", context.user_role)
        self.assertEqual("Customer", context.counterpart_role)
        self.assertEqual(defaults.objective, context.objective)
        self.assertEqual("Migration status", context.background)
        self.assertEqual(defaults.tone, context.tone)
        self.assertEqual("Stay under ten minutes", context.constraints)
        self.assertEqual("Mention risks first", context.custom_instructions)

    def test_text_and_markdown_references_parse_as_inline_text_capped_at_forty_thousand_chars(self) -> None:
        prefix = "A" * 40_000
        over_limit_text = prefix + "SHOULD_NOT_SURVIVE"
        payloads = [
            ReferenceDocumentPayload(
                id="txt-doc",
                name="notes.txt",
                mimeType="text/plain",
                sizeBytes=len(over_limit_text),
                text=over_limit_text,
            ),
            ReferenceDocumentPayload(
                id="md-doc",
                name="agenda.md",
                mimeType="text/markdown",
                sizeBytes=len(over_limit_text),
                text=over_limit_text,
            ),
        ]

        documents = parse_reference_payloads(payloads)

        self.assertEqual(2, len(documents))
        for document, expected_id, expected_name, expected_mime in zip(
            documents,
            ("txt-doc", "md-doc"),
            ("notes.txt", "agenda.md"),
            ("text/plain", "text/markdown"),
            strict=True,
        ):
            with self.subTest(name=expected_name):
                self.assertEqual(expected_id, document.id)
                self.assertEqual(expected_name, document.name)
                self.assertEqual(expected_mime, document.mime_type)
                self.assertEqual(len(over_limit_text), document.size_bytes)
                self.assertEqual("parsed", document.status)
                self.assertEqual(prefix, document.text)
                self.assertNotIn("SHOULD_NOT_SURVIVE", document.text)

    def test_unsupported_reference_extension_returns_failed_document(self) -> None:
        payload = ReferenceDocumentPayload(
            id="slide-deck",
            name="slides.pdf",
            mimeType="application/pdf",
            sizeBytes=1024,
            text="not parsed",
        )

        (document,) = parse_reference_payloads([payload])

        self.assertEqual("slide-deck", document.id)
        self.assertEqual("slides.pdf", document.name)
        self.assertEqual("application/pdf", document.mime_type)
        self.assertEqual(1024, document.size_bytes)
        self.assertEqual("failed", document.status)
        self.assertEqual("unsupported file type", document.error)
        self.assertEqual("", document.text)

    def test_docx_without_uploaded_content_fails_before_converter_is_required(self) -> None:
        payload = ReferenceDocumentPayload(
            id="docx-without-body",
            name="briefing.docx",
            mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            sizeBytes=2048,
        )

        (document,) = parse_reference_payloads([payload])

        self.assertEqual("docx-without-body", document.id)
        self.assertEqual("briefing.docx", document.name)
        self.assertEqual("failed", document.status)
        self.assertEqual("docx content is missing", document.error)
        self.assertEqual("", document.text)

    def test_persist_reference_documents_uses_meeting_document_layout_without_raw_upload_files(self) -> None:
        document = ReferenceDocument(
            id="quarterly-plan----draft-v1",
            name="plan.md",
            mime_type="text/markdown",
            size_bytes=27,
            status="parsed",
            text="# Plan\nParsed reference body",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            user_data_dir = Path(temp_dir)

            persist_reference_documents(user_data_dir, "meeting-123", (document,))

            document_dir = user_data_dir / "meetings" / "meeting-123" / "references" / document.id
            self.assertTrue(document_dir.is_dir())
            self.assertEqual({"metadata.json", "parsed.md"}, {path.name for path in document_dir.iterdir()})
            self.assertEqual("# Plan\nParsed reference body", (document_dir / "parsed.md").read_text(encoding="utf-8"))

            metadata = cast(dict[str, object], json.loads((document_dir / "metadata.json").read_text(encoding="utf-8")))
            self.assertEqual(
                {
                    "id": "quarterly-plan----draft-v1",
                    "name": "plan.md",
                    "mime_type": "text/markdown",
                    "size_bytes": 27,
                    "status": "parsed",
                    "error": "",
                },
                metadata,
            )
            self.assertNotIn("text", metadata)

    def test_unsafe_document_ids_are_sanitized_before_parsing_and_persistence(self) -> None:
        payload = ReferenceDocumentPayload(
            id="../../顧客 deck:v1?*",
            name="notes.txt",
            mimeType="text/plain",
            sizeBytes=4,
            text="safe",
        )
        (document,) = parse_reference_payloads([payload])

        self.assertEqual("------顧客-deck-v1--", document.id)
        self.assertNotIn("/", document.id)
        self.assertNotIn("..", document.id)

        with tempfile.TemporaryDirectory() as temp_dir:
            user_data_dir = Path(temp_dir)

            persist_reference_documents(user_data_dir, "meeting-safe", (document,))

            references_dir = user_data_dir / "meetings" / "meeting-safe" / "references"
            self.assertEqual([document.id], [path.name for path in references_dir.iterdir()])
            self.assertTrue((references_dir / document.id / "metadata.json").is_file())
            self.assertFalse((user_data_dir / "顧客 deck:v1?*").exists())


if __name__ == "__main__":
    _ = unittest.main()
