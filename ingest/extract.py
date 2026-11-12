"""Local text extraction for uploaded documents.

Supported inputs: plain text, Markdown, CSV, DOCX, PDF (text layer or scanned)
and common image formats. No third-party Python parser is used: DOCX is read
with :mod:`zipfile`, PDFs go through the ``pdftotext`` binary, and images go
through the ``tesseract`` CLI.

Privacy rules enforced here:

* uploaded bytes are only written inside a per-call temporary directory;
* those files are created with mode ``0600`` and the directory is removed by a
  ``finally`` block (``TemporaryDirectory``);
* nothing is logged and nothing is persisted outside the call.
"""

from __future__ import annotations

import html
import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 20
MAX_CHARS = 200_000

#: Rough threshold below which a PDF page is assumed to be a scan.
MIN_TEXT_CHARS_PER_PAGE = 20

_TEXT_SUFFIXES = frozenset({".txt", ".md", ".csv"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff"})

#: Tesseract packs that cover the supported Indic scripts. ``eng`` is always
#: requested first so Latin fragments in a mixed document still work.
INDIC_TESSERACT_LANGS: tuple[str, ...] = (
    "tam",
    "tel",
    "kan",
    "mal",
    "hin",
    "mar",
    "ben",
    "guj",
    "pan",
    "ori",
)

_TESSERACT_LANG_CACHE: tuple[str, ...] | None = None


class ExtractionError(ValueError):
    """A document could not be read safely. The message is user-facing."""


@dataclass(frozen=True)
class Extraction:
    text: str
    filename: str
    pages: int
    ocr_used: bool
    ocr_langs: tuple[str, ...]
    warnings: tuple[str, ...]


def extract(filename: str, data: bytes) -> Extraction:
    """Extract text from one uploaded document.

    Raises:
        ExtractionError: the file is unsupported, too large, too long, or empty.
    """

    _validate_filename(filename)
    if not isinstance(data, (bytes, bytearray)):
        raise ExtractionError("file content must be bytes")
    if not data:
        raise ExtractionError("the uploaded file is empty")
    if len(data) > MAX_BYTES:
        raise ExtractionError(
            f"file is {len(data) / (1024 * 1024):.1f} MB; the limit is 10 MB"
        )

    suffix = Path(filename).suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        text, pages, ocr_used, ocr_langs, warnings = _extract_plain(data)
    elif suffix == ".docx":
        text, pages, ocr_used, ocr_langs, warnings = _extract_docx(data)
    elif suffix == ".pdf":
        text, pages, ocr_used, ocr_langs, warnings = _extract_pdf(data)
    elif suffix in _IMAGE_SUFFIXES:
        text, pages, ocr_used, ocr_langs, warnings = _extract_image(data, suffix)
    else:
        raise ExtractionError(
            f"unsupported file type '{suffix or '(none)'}'. "
            "Use txt, md, csv, docx, pdf, png, jpg, jpeg, tif or tiff."
        )

    text = _normalize(text)
    if len(text) > MAX_CHARS:
        raise ExtractionError(
            f"extracted text is {len(text):,} characters; the limit is "
            f"{MAX_CHARS:,}. Split the document and try again."
        )
    if not text.strip():
        raise ExtractionError("no text could be extracted from this document")
    if pages > MAX_PAGES:
        raise ExtractionError(f"document has {pages} pages; the limit is {MAX_PAGES}")

    return Extraction(
        text=text,
        filename=filename,
        pages=max(1, pages),
        ocr_used=ocr_used,
        ocr_langs=tuple(ocr_langs),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Validation and helpers
# ---------------------------------------------------------------------------


def _validate_filename(filename: str) -> None:
    if not isinstance(filename, str) or not filename.strip():
        raise ExtractionError("filename is required")
    if "\x00" in filename:
        raise ExtractionError("filename contains a null byte")
    if "/" in filename or "\\" in filename:
        raise ExtractionError("filename must not contain a path")
    if filename in {".", ".."}:
        raise ExtractionError("filename is not a file")
    if not Path(filename).suffix:
        raise ExtractionError("file has no extension; cannot choose an extractor")


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # pdftotext separates pages with a form feed; it is layout, not content.
    text = text.replace("\f", "\n")
    return text.strip()


def _extract_plain(data: bytes) -> tuple[str, int, bool, tuple[str, ...], tuple[str, ...]]:
    text = data.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text, 1, False, (), ()


def _extract_docx(data: bytes) -> tuple[str, int, bool, tuple[str, ...], tuple[str, ...]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            try:
                xml_bytes = archive.read("word/document.xml")
            except KeyError as exc:
                raise ExtractionError(
                    "DOCX is missing word/document.xml; is it a valid Word file?"
                ) from exc
    except zipfile.BadZipFile as exc:
        raise ExtractionError("file is not a valid DOCX archive") from exc

    xml = xml_bytes.decode("utf-8", errors="replace")
    pages = 1 + len(re.findall(r"<w:br\b[^>]*w:type=\"page\"", xml))
    if pages > MAX_PAGES:
        raise ExtractionError(f"document has {pages} pages; the limit is {MAX_PAGES}")

    # WordprocessingML: paragraph and row ends are line breaks; tabs and manual
    # breaks are whitespace. Strip the remaining tags, then unescape XML.
    xml = re.sub(r"<w:tab\b[^>]*/>", "\t", xml)
    xml = re.sub(r"<w:br\b[^>]*/>", "\n", xml)
    xml = re.sub(r"</w:p\s*>", "\n", xml)
    xml = re.sub(r"</w:tr\s*>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    text = html.unescape(xml)
    return text, pages, False, (), ()


def _extract_pdf(data: bytes) -> tuple[str, int, bool, tuple[str, ...], tuple[str, ...]]:
    with tempfile.TemporaryDirectory(prefix="tijori-ingest-") as tmp:
        pdf_path = _write_private(Path(tmp) / "input.pdf", data)
        pdftotext = shutil.which("pdftotext")
        if not pdftotext:
            raise ExtractionError(
                "PDF extraction requires the 'pdftotext' binary on PATH"
            )

        try:
            completed = subprocess.run(
                [pdftotext, "-layout", "-enc", "UTF-8", str(pdf_path), "-"],
                capture_output=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExtractionError("pdftotext timed out after 60 seconds") from exc
        except OSError as exc:
            raise ExtractionError(f"could not run pdftotext: {exc}") from exc

        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()[:200]
            raise ExtractionError(f"pdftotext could not read this PDF: {detail}")

        text = completed.stdout.decode("utf-8", errors="replace")
        pages = _pdf_page_count(text)
        if pages > MAX_PAGES:
            raise ExtractionError(f"document has {pages} pages; the limit is {MAX_PAGES}")

        if len(text.strip()) < MIN_TEXT_CHARS_PER_PAGE * pages:
            return _ocr_pdf(pdf_path, tmp)

        return text, pages, False, (), ()


def _pdf_page_count(text: str) -> int:
    # pdftotext terminates every page with a form feed.
    return max(1, text.count("\f"))


def _ocr_pdf(
    pdf_path: Path,
    tmp: str,
) -> tuple[str, int, bool, tuple[str, ...], tuple[str, ...]]:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise ExtractionError(
            "this PDF appears to be scanned, but 'pdftoppm' is not installed. "
            "Install poppler-utils (or the tesseract language packs) to OCR it."
        )

    prefix = Path(tmp) / "page"
    try:
        completed = subprocess.run(
            [
                pdftoppm,
                "-r",
                "200",
                "-png",
                "-f",
                "1",
                "-l",
                str(MAX_PAGES),
                str(pdf_path),
                str(prefix),
            ],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError("pdftoppm timed out after 120 seconds") from exc
    except OSError as exc:
        raise ExtractionError(f"could not run pdftoppm: {exc}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:200]
        raise ExtractionError(f"pdftoppm could not render this PDF: {detail}")

    images = sorted(
        Path(tmp).glob("page-*.png"),
        key=lambda path: int(re.search(r"(\d+)$", path.stem).group(1))
        if re.search(r"(\d+)$", path.stem)
        else 0,
    )
    if not images:
        raise ExtractionError("pdftoppm produced no page images")

    langs, warnings = _ocr_languages()
    pages_text = [_ocr_image(image, langs) for image in images]
    text = "\n\n".join(pages_text)
    return text, len(images), True, tuple(langs.split("+")), warnings


def _extract_image(
    data: bytes,
    suffix: str,
) -> tuple[str, int, bool, tuple[str, ...], tuple[str, ...]]:
    with tempfile.TemporaryDirectory(prefix="tijori-ingest-") as tmp:
        image_path = _write_private(Path(tmp) / f"upload{suffix}", data)
        langs, warnings = _ocr_languages()
        text = _ocr_image(image_path, langs)
        return text, 1, True, tuple(langs.split("+")), warnings


def _write_private(path: Path, data: bytes) -> Path:
    """Write ``data`` with owner-only permissions and return the path."""

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    finally:
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Windows may ignore POSIX mode bits; the temp directory is still
            # per-call and removed on exit.
            pass
    return path


# ---------------------------------------------------------------------------
# Tesseract
# ---------------------------------------------------------------------------


def _installed_tesseract_langs() -> tuple[str, ...]:
    """Return installed Tesseract language codes, cached once per process."""

    global _TESSERACT_LANG_CACHE
    if _TESSERACT_LANG_CACHE is not None:
        return _TESSERACT_LANG_CACHE

    executable = shutil.which("tesseract")
    if not executable:
        _TESSERACT_LANG_CACHE = ()
        return _TESSERACT_LANG_CACHE

    try:
        completed = subprocess.run(
            [executable, "--list-langs"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _TESSERACT_LANG_CACHE = ()
        return _TESSERACT_LANG_CACHE

    output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    # The first line is a header ("List of available languages ...:").
    if lines and ":" in lines[0]:
        lines = lines[1:]
    _TESSERACT_LANG_CACHE = tuple(lines)
    return _TESSERACT_LANG_CACHE


def _ocr_languages() -> tuple[str, tuple[str, ...]]:
    """Build the ``-l`` argument and any user-visible warning."""

    installed = _installed_tesseract_langs()
    if not installed:
        raise ExtractionError(
            "OCR requires the 'tesseract' binary with language data. "
            "Install tesseract and the Indic language packs, then retry."
        )

    indic = [lang for lang in INDIC_TESSERACT_LANGS if lang in installed]
    if not indic:
        raise ExtractionError(
            "Indic OCR language packs are not installed. Install the tesseract-ocr "
            "packs for the document's script, then retry."
        )

    langs: list[str] = []
    if "eng" in installed:
        langs.append("eng")
    langs.extend(indic)
    if not langs:
        raise ExtractionError(
            "tesseract is installed but has none of the required language packs "
            "(eng or Indic)"
        )

    warnings: list[str] = []
    return "+".join(langs), tuple(warnings)


def _ocr_image(path: Path, langs: str) -> str:
    executable = shutil.which("tesseract")
    if not executable:
        raise ExtractionError(
            "OCR requires the 'tesseract' binary. Install tesseract and retry."
        )

    try:
        completed = subprocess.run(
            [executable, str(path), "stdout", "-l", langs],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError("tesseract timed out after 120 seconds") from exc
    except OSError as exc:
        raise ExtractionError(f"could not run tesseract: {exc}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:200]
        raise ExtractionError(f"tesseract could not OCR this page: {detail}")

    text = completed.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        raise ExtractionError("OCR produced no text; the scan may be blank or unreadable")
    return text
