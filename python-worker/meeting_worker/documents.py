"""Local DOCX conversion through MarkItDown, without plugins or remote services."""

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from zipfile import ZipFile

from defusedxml.ElementTree import iterparse

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_TEXT_CHARS = 40_000


@dataclass(frozen=True)
class Input:
    id: int
    path: Path


@dataclass(frozen=True)
class Request:
    id: str
    files: tuple[Input, ...]


def parse_request(value: object) -> Request:
    if not isinstance(value, dict) or set(value) != {"protocol", "id", "files"}:
        raise ValueError("request")
    if type(value["protocol"]) is not int or value["protocol"] != 1:
        raise ValueError("protocol")
    request_id = value["id"]
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80:
        raise ValueError("id")
    files = value["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= 10:
        raise ValueError("files")
    parsed: list[Input] = []
    ids: set[int] = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {"id", "input_path"}:
            raise ValueError("file")
        index = item["id"]
        name = item["input_path"]
        if type(index) is not int or not 0 <= index < 10 or index in ids:
            raise ValueError("file id")
        if not isinstance(name, str) or len(name) > 4096:
            raise ValueError("path")
        path = Path(name)
        if not path.is_absolute() or path.suffix != ".docx":
            raise ValueError("path")
        ids.add(index)
        parsed.append(Input(index, path))
    return Request(request_id, tuple(parsed))


def validate_archive(stream: BinaryIO) -> None:
    """Bound all uncompressed parts before MarkItDown reads the OOXML package."""
    with ZipFile(stream) as archive:
        entries = archive.infolist()
        if len(entries) > 4096 or sum(e.file_size for e in entries) > MAX_ARCHIVE_BYTES:
            raise ValueError("archive size")
        if len({e.filename for e in entries}) != len(entries):
            raise ValueError("duplicate archive entry")
        if "word/document.xml" not in archive.namelist():
            raise ValueError("document missing")
        for entry in entries:
            if not entry.filename.endswith((".xml", ".rels")):
                continue
            if entry.file_size > MAX_XML_BYTES:
                raise ValueError("XML size")
            with archive.open(entry) as part:
                depth = 0
                for event, element in iterparse(
                    part,
                    events=("start", "end"),
                    forbid_dtd=True,
                    forbid_entities=True,
                    forbid_external=True,
                ):
                    if event == "start":
                        depth += 1
                        if depth > 256:
                            raise ValueError("XML depth")
                    else:
                        depth -= 1
                        element.clear()
    stream.seek(0)


def convert_request(value: object) -> dict[str, object]:
    request = parse_request(value)
    # No converter/model construction until the command has passed validation.
    from markitdown import MarkItDown, StreamInfo
    from markitdown.converters import DocxConverter

    converter = MarkItDown(enable_builtins=False, enable_plugins=False)
    converter.register_converter(DocxConverter())
    results: list[dict[str, object]] = []
    total = 0
    for item in request.files:
        try:
            if item.path.is_symlink() or not item.path.is_file():
                raise ValueError("file")
            size = item.path.stat().st_size
            total += size
            if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("file size")
            with item.path.open("rb") as stream:
                validate_archive(stream)
                converted = converter.convert_stream(
                    stream,
                    stream_info=StreamInfo(extension=".docx"),
                )
            results.append(
                {
                    "id": item.id,
                    "status": "converted",
                    "markdown": converted.text_content.strip()[:MAX_TEXT_CHARS],
                }
            )
        except Exception:
            results.append({"id": item.id, "status": "failed", "error": "conversion_failed"})
    return {"protocol": 1, "id": request.id, "results": results}
