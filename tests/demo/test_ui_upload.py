"""Upload flow with ingest monkeypatched: no model load, no network."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from streamlit.testing.v1 import AppTest

import ingest.extract as extract_module
import ingest.language as language_module
import ingest.translate as translate_module
from ingest.extract import Extraction, ExtractionError
from ingest.language import DetectedLanguage
from ingest.translate import Translation, TranslationUnavailable

APP = Path(__file__).resolve().parents[2] / "ui" / "app.py"

TAMIL_TEXT = "வணக்கம். என் ஆதார் எண் 1234 5678 9012."
ENGLISH_TEXT = "Hello. My Aadhaar number is 1234 5678."
TAMIL = DetectedLanguage("ta", "Tamil", "tam_Taml", "tam", False)
ENGLISH = DetectedLanguage("en", "Latin", "eng_Latn", "eng", True)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        return None


def fake_get(url, *args, **kwargs):
    if url.endswith("/health"):
        return FakeResponse({"status": "ok", "provider": "stub", "model": "stub-1"})
    return FakeResponse([])


def run_upload_app(monkeypatch) -> AppTest:
    monkeypatch.setenv("TIJORI_API_URL", "http://stub")
    monkeypatch.setattr(httpx, "get", fake_get)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception, app.exception

    app.file_uploader[0].set_value(
        ("tamil.txt", TAMIL_TEXT.encode("utf-8"), "text/plain")
    )
    app.run()
    assert not app.exception, app.exception
    return app


def patch_success(monkeypatch) -> None:
    monkeypatch.setattr(
        extract_module,
        "extract",
        lambda filename, data: Extraction(
            text=TAMIL_TEXT,
            filename=filename,
            pages=2,
            ocr_used=False,
            ocr_langs=(),
            warnings=(),
        ),
    )
    monkeypatch.setattr(language_module, "detect_language", lambda text: TAMIL)
    monkeypatch.setattr(
        translate_module,
        "translate_to_english",
        lambda text, detected: Translation(
            text=ENGLISH_TEXT,
            source_lang="ta",
            model="fake-nllb",
            duration_ms=5,
            warnings=("digit sequence 9012 is missing from the translation",),
        ),
    )


def test_upload_populates_english_box_and_badge(monkeypatch):
    patch_success(monkeypatch)
    app = run_upload_app(monkeypatch)

    editor = next(item for item in app.text_area if item.key == "original_text")
    assert editor.value == ENGLISH_TEXT

    assert any("Tamil → English, on-device" in caption.value for caption in app.caption)
    assert any("pages: 2" in caption.value for caption in app.caption)
    assert any(TAMIL_TEXT in code.value for code in app.code)
    assert any("9012" in warning.value for warning in app.warning)
    assert app.session_state["source_lang"] == "ta"

    run_button = next(button for button in app.button if button.label == "Run gateway")
    assert not run_button.disabled


def test_failed_translation_blocks_the_run(monkeypatch):
    monkeypatch.setattr(
        extract_module,
        "extract",
        lambda filename, data: Extraction(
            text=TAMIL_TEXT,
            filename=filename,
            pages=1,
            ocr_used=False,
            ocr_langs=(),
            warnings=(),
        ),
    )
    monkeypatch.setattr(language_module, "detect_language", lambda text: TAMIL)
    monkeypatch.setattr(
        translate_module,
        "translate_to_english",
        lambda text, detected: (_ for _ in ()).throw(
            TranslationUnavailable("weights are not cached")
        ),
    )

    app = run_upload_app(monkeypatch)

    assert any("refusing to send un-translated" in error.value for error in app.error)
    run_button = next(button for button in app.button if button.label == "Run gateway")
    assert run_button.disabled


def test_extraction_failure_blocks_the_run(monkeypatch):
    monkeypatch.setattr(
        extract_module,
        "extract",
        lambda filename, data: (_ for _ in ()).throw(ExtractionError("bad document")),
    )

    app = run_upload_app(monkeypatch)

    assert any("bad document" in error.value for error in app.error)
    run_button = next(button for button in app.button if button.label == "Run gateway")
    assert run_button.disabled


def test_english_upload_needs_no_translation(monkeypatch):
    monkeypatch.setattr(
        extract_module,
        "extract",
        lambda filename, data: Extraction(
            text="Hello world",
            filename=filename,
            pages=1,
            ocr_used=False,
            ocr_langs=(),
            warnings=(),
        ),
    )
    monkeypatch.setattr(language_module, "detect_language", lambda text: ENGLISH)
    monkeypatch.setattr(
        translate_module,
        "translate_to_english",
        lambda text, detected: Translation(
            text=text,
            source_lang="en",
            model="none",
            duration_ms=0,
            warnings=(),
        ),
    )

    app = run_upload_app(monkeypatch)

    editor = next(item for item in app.text_area if item.key == "original_text")
    assert editor.value == "Hello world"
    assert any(
        "English — no translation needed" in caption.value for caption in app.caption
    )
    run_button = next(button for button in app.button if button.label == "Run gateway")
    assert not run_button.disabled
