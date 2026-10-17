"""Mobile share providers: metadata is unreliable; received content is authoritative."""
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

import backend_adapter as b
from tests.test_detection_layers import _docx, HUMAN_PARA


@pytest.mark.parametrize("name,mime", [
    ("تقرير", "application/octet-stream"),
    ("report.docx.bin", "application/octet-stream"),
    ("report.docx (1)", ""),
    ("report.txt", "text/plain"),
    ("REPORT.DOCX", ""),
])
def test_shared_word_content_is_accepted_despite_metadata(name, mime):
    content = _docx([HUMAN_PARA] * 4)
    normalized = b.prepare_ui_upload(name, mime, content)
    assert normalized.endswith(".docx")
    assert b.analyze_uploaded_bytes(normalized, content).is_docx


@pytest.mark.parametrize("encoding,text", [
    ("utf-8", "a" * 2047 + "عربية"),
    ("utf-16", "ملاحظات عربية " * 200),
])
def test_extensionless_text_at_sample_boundary(encoding, text):
    assert b.prepare_ui_upload("notes", "application/octet-stream", text.encode(encoding)) == "notes.txt"


def test_long_arabic_filename_does_not_become_a_filesystem_path():
    name = "تقرير" * 70 + ".docx"
    report = b.analyze_uploaded_bytes(name, _docx([HUMAN_PARA] * 4))
    assert report.source == name


@pytest.mark.parametrize("name,data,message", [
    ("empty.docx", b"", "فارغ"),
    ("large.txt", b"a" * (b.MAX_FILE_BYTES + 1), "25 MB"),
    ("broken.docx", b"PK\x03\x04broken", "تالف"),
    ("image.txt", b"\xff\xd8\x00" * 100, "نصًا"),
    ("old.doc", bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 64, "DOCX"),
    ("scan", b"%PDF-1.7\n", "PDF"),
])
def test_failed_uploads_have_actionable_errors(name, data, message):
    with pytest.raises(ValueError, match=message):
        b.prepare_ui_upload(name, "", data)


def test_zip_requires_batch_mode():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("notes.txt", "Readable notes")
    data = out.getvalue()
    assert b.prepare_ui_upload("shared.bin", "", data, allow_zip=True).endswith(".zip")
    with pytest.raises(ValueError):
        b.prepare_ui_upload("shared.bin", "", data)


def test_picker_does_not_reject_mobile_files_with_desktop_user_agent():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py")).run()
    for mode in ("ملف واحد", "دفعة ملفات أو ZIP"):
        app.radio[0].set_value(mode).run()
        assert not app.exception
        upload = app.get("file_uploader")[0]
        assert not list(upload.proto.type)
        assert upload.proto.max_upload_size_mb == 25


def test_invalid_mobile_attachment_does_not_block_valid_batch_file():
    content = _docx([HUMAN_PARA] * 4)
    files = [
        SimpleNamespace(name="broken.docx", type="", getvalue=lambda: b"broken"),
        SimpleNamespace(name="تقرير.bin", type="application/octet-stream", getvalue=lambda: content),
    ]
    with patch("streamlit.file_uploader", return_value=files), patch("turnstile_gate.require_human", return_value=True):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py")).run()
        app.radio[0].set_value("دفعة ملفات أو ZIP").run()
        app.button[0].click().run()
        assert not app.exception
        reports = app.session_state["batch_reports"]
        assert len(reports) == 1
        assert reports[0][0] == "تقرير.bin.docx"
        assert not isinstance(reports[0][1], Exception)
        assert any("broken.docx" in warning.value for warning in app.warning)
