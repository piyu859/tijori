"""Script detection tests: one case per supported block."""

from __future__ import annotations

import pytest

from ingest.language import detect_language


@pytest.mark.parametrize(
    ("text", "code", "script", "nllb", "tesseract"),
    [
        ("வணக்கம் உலகம்", "ta", "Tamil", "tam_Taml", "tam"),
        ("నమస్కారం ప్రపంచం", "te", "Telugu", "tel_Telu", "tel"),
        ("ನಮಸ್ಕಾರ ವಿಶ್ವ", "kn", "Kannada", "kan_Knda", "kan"),
        ("നമസ്കാരം ലോകം", "ml", "Malayalam", "mal_Mlym", "mal"),
        ("नमस्ते दुनिया", "hi", "Devanagari", "hin_Deva", "hin"),
        ("मराठी भाषा", "hi", "Devanagari", "hin_Deva", "hin"),
        ("বাংলা ভাষা", "bn", "Bengali", "ben_Beng", "ben"),
        ("ગુજરાતી ભાષા", "gu", "Gujarati", "guj_Gujr", "guj"),
        ("ਸਤ ਸ੍ਰੀ ਅਕਾਲ", "pa", "Gurmukhi", "pan_Guru", "pan"),
        ("ଓଡ଼ିଆ ଭାଷା", "or", "Odia", "ory_Orya", "ori"),
        ("اردو زبان", "ur", "Arabic", "urd_Arab", "urd"),
    ],
)
def test_detects_supported_scripts(text, code, script, nllb, tesseract):
    detected = detect_language(text)
    assert detected.code == code
    assert detected.script == script
    assert detected.nllb == nllb
    assert detected.tesseract == tesseract
    assert detected.is_english is False


def test_english_is_latin_dominant():
    detected = detect_language("The applicant submitted a loan application.")
    assert detected.code == "en"
    assert detected.script == "Latin"
    assert detected.nllb == "eng_Latn"
    assert detected.is_english is True


def test_romanised_indic_is_classified_english():
    # Documented limitation: script counting cannot see romanised Tamil.
    assert detect_language("vanakkam, naan Chennai-la irukken").is_english is True


def test_empty_and_numeric_text_default_to_english():
    assert detect_language("").is_english is True
    assert detect_language("1234 5678 9012").is_english is True


def test_latin_dominance_wins_a_mixed_document():
    text = "This is an English sentence with one Tamil word: வணக்கம்."
    assert detect_language(text).is_english is True
