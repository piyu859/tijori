"""Extraction tests. No network, no model load."""

from __future__ import annotations

import html
import io
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest

import ingest.extract as extract_module
from ingest.extract import ExtractionError, extract

PDF_TEXT = "Aadhaar number 1234-5678-9012 and PAN ABCDE1234F"


def make_docx(paragraphs: list[str], table_rows: list[list[str]] | None = None) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{html.escape(paragraph)}</w:t></w:r></w:p>"
        for paragraph in paragraphs
    )
    for row in table_rows or []:
        cells = "".join(
            f"<w:tc><w:p><w:r><w:t>{html.escape(cell)}</w:t></w:r></w:p></w:tc>"
            for cell in row
        )
        body += f"<w:tr>{cells}</w:tr>"

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def make_pdf(text: str) -> bytes:
    """Build a minimal one-page PDF with a standard Helvetica text object."""

    def escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = f"BT /F1 12 Tf 72 720 Td ({escape(text)}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode()
    output += b"0000000000 65535 f \n"
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(output)


class FakeCompleted:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_txt_strips_bom_and_keeps_text():
    result = extract("note.txt", b"\xef\xbb\xbfHello Tijori")
    assert result.text == "Hello Tijori"
    assert result.pages == 1
    assert result.ocr_used is False
    assert result.ocr_langs == ()


@pytest.mark.parametrize("name", ["note.md", "note.csv"])
def test_markdown_and_csv_are_plain_utf8(name):
    result = extract(name, "Name,Amount\nRahul,100\n".encode())
    assert "Rahul" in result.text
    assert result.pages == 1


def test_docx_paragraphs_and_table_rows_become_lines():
    data = make_docx(["First paragraph", "Second paragraph"], [["Cell A", "Cell B"]])
    result = extract("letter.docx", data)
    assert "First paragraph" in result.text
    assert "Second paragraph" in result.text
    assert "Cell A" in result.text and "Cell B" in result.text
    assert result.text.count("\n") >= 3
    assert result.ocr_used is False


def test_docx_without_document_xml_is_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/styles.xml", "<xml/>")
    with pytest.raises(ExtractionError, match="word/document.xml"):
        extract("broken.docx", buffer.getvalue())


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext is not installed")
def test_pdf_text_layer_is_extracted():
    result = extract("form.pdf", make_pdf(PDF_TEXT))
    assert "1234-5678-9012" in result.text
    assert "ABCDE1234F" in result.text
    assert result.pages == 1
    assert result.ocr_used is False


def test_pdf_without_pdftotext_fails_closed(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ExtractionError, match="pdftotext"):
        extract("form.pdf", make_pdf(PDF_TEXT))


def test_unsupported_suffix_is_rejected():
    with pytest.raises(ExtractionError, match="unsupported file type"):
        extract("archive.zip", b"PK\x03\x04")


def test_filename_with_path_separator_is_rejected():
    with pytest.raises(ExtractionError, match="path"):
        extract("../secret.txt", b"hello")
    with pytest.raises(ExtractionError, match="path"):
        extract("folder\\secret.txt", b"hello")


def test_oversize_file_is_rejected():
    with pytest.raises(ExtractionError, match="10 MB"):
        extract("big.txt", b"x" * (10 * 1024 * 1024 + 1))


def test_empty_file_is_rejected():
    with pytest.raises(ExtractionError, match="empty"):
        extract("empty.txt", b"")


def test_too_many_characters_is_rejected(monkeypatch):
    monkeypatch.setattr(extract_module, "MAX_CHARS", 10)
    with pytest.raises(ExtractionError, match="characters"):
        extract("long.txt", b"x" * 20)


def test_ocr_without_tesseract_fails_closed(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ExtractionError, match="tesseract"):
        extract("scan.png", b"not really a png")


def test_empty_scan_raises_extraction_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "tesseract")
    monkeypatch.setattr(extract_module, "_TESSERACT_LANG_CACHE", ("eng", "tam"))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: FakeCompleted(stdout=b"", returncode=0),
    )
    with pytest.raises(ExtractionError, match="OCR produced no text"):
        extract("scan.png", b"not really a png")


def test_ocr_without_indic_packs_fails_clearly(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "tesseract")
    monkeypatch.setattr(extract_module, "_TESSERACT_LANG_CACHE", ("eng",))

    with pytest.raises(ExtractionError, match="Indic OCR language packs are not installed"):
        extract("scan.png", b"not really a png")


def test_temp_directory_is_removed_after_pdf_extraction(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda name: name if name == "pdftotext" else None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: FakeCompleted(stdout=b"A text layer long enough to skip OCR."),
    )

    extract("form.pdf", make_pdf(PDF_TEXT))
    assert list(tmp_path.glob("tijori-ingest-*")) == []


def test_temp_directory_is_removed_after_failed_ocr(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda name: "tesseract")
    monkeypatch.setattr(extract_module, "_TESSERACT_LANG_CACHE", ("eng", "tam"))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: FakeCompleted(stdout=b"", returncode=0),
    )

    with pytest.raises(ExtractionError):
        extract("scan.png", b"not really a png")
    assert list(tmp_path.glob("tijori-ingest-*")) == []


def _indic_packs_installed() -> bool:
    installed = extract_module._installed_tesseract_langs()
    return any(lang in installed for lang in extract_module.INDIC_TESSERACT_LANGS)


@pytest.mark.slow
@pytest.mark.skipif(not _indic_packs_installed(), reason="Indic tesseract packs are not installed")
def test_indic_ocr_pack_is_used():
    # A real image fixture is intentionally not committed; this test documents
    # the expected path and skips on machines without the packs.
    langs, _warnings = extract_module._ocr_languages()
    assert "tam" in langs or "hin" in langs
