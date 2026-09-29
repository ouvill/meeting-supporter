"""Synthetic documents only; no user files, network services, or credentials."""

import io
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from meeting_worker.documents import MAX_XML_BYTES, convert_request, parse_request, validate_archive

ROOT = Path(__file__).resolve().parents[1]


def docx(path: Path, body: str) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" 
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" 
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
 Target="word/document.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "word/document.xml",
            f"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>{body}</w:body>
</w:document>""",
        )
        archive.writestr(
            "word/styles.xml",
            """<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Heading1">
<w:name w:val="heading 1"/>
</w:style>
</w:styles>""",
        )


def request(*paths: Path) -> dict:
    return {
        "protocol": 1,
        "id": "synthetic",
        "files": [{"id": index, "input_path": str(path)} for index, path in enumerate(paths)],
    }


def test_markitdown_preserves_heading_table_and_japanese_without_network(tmp_path, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("document conversion must stay offline")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    path = tmp_path / "synthetic.docx"
    docx(
        path,
        """<w:p>
<w:pPr>
<w:pStyle w:val="Heading1"/>
</w:pPr>
<w:r>
<w:t>合成の会議資料</w:t>
</w:r>
</w:p>
<w:tbl>
<w:tr>
<w:tc>
<w:p>
<w:r>
<w:t>項目</w:t>
</w:r>
</w:p>
</w:tc>
<w:tc>
<w:p>
<w:r>
<w:t>値</w:t>
</w:r>
</w:p>
</w:tc>
</w:tr>
<w:tr>
<w:tc>
<w:p>
<w:r>
<w:t>予定</w:t>
</w:r>
</w:p>
</w:tc>
<w:tc>
<w:p>
<w:r>
<w:t>合成データ</w:t>
</w:r>
</w:p>
</w:tc>
</w:tr>
</w:tbl>""",
    )
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"invalid zip")
    response = convert_request(request(path, broken))
    assert response["protocol"] == 1
    assert response["id"] == "synthetic"
    result, failed = response["results"]
    assert result["status"] == "converted"
    assert "# 合成の会議資料" in result["markdown"]
    assert "| 項目 | 値 |" in result["markdown"]
    assert "| 予定 | 合成データ |" in result["markdown"]
    assert failed == {"id": 1, "status": "failed", "error": "conversion_failed"}


def test_rejects_external_entities_deep_xml_and_zip_bombs():
    for xml in [
        (
            b'<!DOCTYPE test [<!ENTITY external SYSTEM "file:///synthetic-secret">]>'
            b"<test>&external;</test>"
        ),
        b"<x>" * 257 + b"</x>" * 257,
        b" " * (MAX_XML_BYTES + 1),
    ]:
        stream = io.BytesIO()
        with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", xml)
        stream.seek(0)
        with pytest.raises(Exception):
            validate_archive(stream)


def test_validates_request_before_loading_converter():
    for bad in [
        {},
        {"protocol": True, "id": "test", "files": []},
        request(Path("relative.docx")),
        request(Path("/synthetic.pdf")),
        {"protocol": 1, "id": "test", "files": [{"id": 0, "input_path": "/a.docx"}] * 2},
    ]:
        with pytest.raises(ValueError):
            parse_request(bad)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import meeting_worker.__main__; import sys; assert 'markitdown' not in sys.modules",
        ],
        cwd=ROOT,
        capture_output=True,
    )
    assert result.returncode == 0


def test_limits_unicode_output_and_rejects_symlinks(tmp_path):
    path = tmp_path / "long.docx"
    docx(path, f"<w:p><w:r><w:t>{'あ' * 40001}</w:t></w:r></w:p>")
    result = convert_request(request(path))["results"][0]
    assert result["markdown"] == "あ" * 40000
    if os.name != "nt":
        link = tmp_path / "linked.docx"
        link.symlink_to(path)
        assert convert_request(request(link))["results"][0]["status"] == "failed"


def test_frozen_worker_without_python_or_uv_on_path(tmp_path):
    executable = os.environ.get("MEETING_TEST_PYTHON_WORKER")
    if not executable:
        pytest.skip("set MEETING_TEST_PYTHON_WORKER to verify the frozen bundle")
    # Relocate the entire onedir bundle to exercise package data and native library paths.
    destination = tmp_path / "合成 配置" / "worker"
    source = Path(executable).resolve()
    shutil.copytree(source.parent, destination)
    path = tmp_path / "合成 資料.docx"
    docx(path, "<w:p><w:r><w:t>synthetic frozen conversion</w:t></w:r></w:p>")
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
    env.update({"PATH": "", "TMPDIR": str(tmp_path), "TMP": str(tmp_path), "TEMP": str(tmp_path)})
    result = subprocess.run(
        [str(destination / source.name), "convert-document"],
        input=json.dumps(request(path)),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0
    response = json.loads(result.stdout)
    assert response["results"][0] == {
        "id": 0,
        "status": "converted",
        "markdown": "synthetic frozen conversion",
    }
    assert "synthetic frozen conversion" not in result.stderr
    invalid = subprocess.run(
        [str(destination / source.name), "convert-document"],
        input='{"protocol":99}',
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        timeout=10,
    )
    assert invalid.returncode == 2
    assert invalid.stdout == ""
