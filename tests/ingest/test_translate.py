"""Translation tests. The model is faked; one slow test uses real weights."""

from __future__ import annotations

import re

import pytest

import ingest.translate as translate_module
from ingest.language import DetectedLanguage, detect_language
from ingest.translate import TranslationUnavailable, translate_to_english

ENGLISH = DetectedLanguage("en", "Latin", "eng_Latn", "eng", True)
TAMIL = DetectedLanguage("ta", "Tamil", "tam_Taml", "tam", False)


class FakeTokenizer:
    def convert_tokens_to_ids(self, token: str) -> int:
        return 42


def fake_model(monkeypatch):
    monkeypatch.setattr(
        translate_module,
        "_load_model",
        lambda name: (FakeTokenizer(), object()),
    )


def test_english_short_circuit_makes_no_model_call(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("the model must not be loaded for English input")

    monkeypatch.setattr(translate_module, "_load_model", explode)
    result = translate_to_english("Hello world", ENGLISH)

    assert result.text == "Hello world"
    assert result.model == "none"
    assert result.duration_ms == 0
    assert result.warnings == ()


def test_missing_digit_sequences_are_warned(monkeypatch):
    fake_model(monkeypatch)
    monkeypatch.setattr(
        translate_module,
        "_translate_chunk",
        lambda *args, **kwargs: "The number is missing.",
    )

    result = translate_to_english("Aadhaar 1234 5678 9012", TAMIL)

    assert result.text == "The number is missing."
    assert any("1234" in warning for warning in result.warnings)
    assert any("5678" in warning for warning in result.warnings)
    assert any("9012" in warning for warning in result.warnings)


def test_preserved_digits_produce_no_warning(monkeypatch):
    fake_model(monkeypatch)
    monkeypatch.setattr(
        translate_module,
        "_translate_chunk",
        lambda *args, **kwargs: "Aadhaar 1234 5678 9012",
    )

    result = translate_to_english("Aadhaar 1234 5678 9012", TAMIL)
    assert result.warnings == ()


def test_line_structure_is_preserved(monkeypatch):
    fake_model(monkeypatch)
    monkeypatch.setattr(
        translate_module,
        "_translate_chunk",
        lambda *args, **kwargs: "translated line",
    )

    result = translate_to_english("line one\n\nline three", TAMIL)
    assert result.text == "translated line\n\ntranslated line"


def test_load_failure_maps_to_translation_unavailable(monkeypatch):
    def fail(name):
        raise OSError("weights are not cached")

    monkeypatch.setattr(translate_module, "_load_model", fail)
    with pytest.raises(TranslationUnavailable, match="on-device translation failed"):
        translate_to_english("வணக்கம்", TAMIL)


def test_import_failure_maps_to_translation_unavailable(monkeypatch):
    def fail(name):
        raise ImportError("transformers is missing")

    monkeypatch.setattr(translate_module, "_load_model", fail)
    with pytest.raises(TranslationUnavailable, match="on-device translation failed"):
        translate_to_english("வணக்கம்", TAMIL)


def test_disabled_translation_blocks_indic_but_not_english(monkeypatch):
    monkeypatch.setenv("TIJORI_TRANSLATE_ENABLED", "0")

    with pytest.raises(TranslationUnavailable, match="disabled"):
        translate_to_english("வணக்கம்", TAMIL)

    result = translate_to_english("Hello", ENGLISH)
    assert result.text == "Hello"


def test_empty_translation_maps_to_unavailable(monkeypatch):
    fake_model(monkeypatch)
    monkeypatch.setattr(translate_module, "_translate_chunk", lambda *args, **kwargs: "")

    with pytest.raises(TranslationUnavailable, match="empty"):
        translate_to_english("வணக்கம்", TAMIL)


def test_native_script_left_in_translation_fails_closed(monkeypatch):
    fake_model(monkeypatch)
    monkeypatch.setattr(translate_module, "_translate_chunk", lambda *args, **kwargs: "வணக்கம்")

    with pytest.raises(TranslationUnavailable, match="native-script"):
        translate_to_english("வணக்கம்", TAMIL)


def _nllb_weights_cached() -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache
    except Exception:  # noqa: BLE001 - optional dependency in some envs
        return False

    def cached(filename: str) -> bool:
        try:
            return isinstance(
                try_to_load_from_cache("facebook/nllb-200-distilled-600M", filename),
                str,
            )
        except Exception:  # noqa: BLE001 - incomplete cache metadata
            return False

    try:
        has_config = cached("config.json")
        has_tokenizer = cached("tokenizer.json") or cached("sentencepiece.bpe.model")
        has_weights = cached("model.safetensors") or cached("pytorch_model.bin")
    except Exception:  # noqa: BLE001
        return False
    return has_config and has_tokenizer and has_weights


@pytest.mark.slow
@pytest.mark.skipif(not _nllb_weights_cached(), reason="NLLB weights are not cached")
def test_translates_short_tamil_sentence_with_real_model(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    text = "வணக்கம். என் பெயர் ராகுல்."
    detected = detect_language(text)
    assert detected.code == "ta"

    result = translate_to_english(text, detected)

    assert result.text.strip()
    assert re.search(r"[A-Za-z]", result.text)
    assert not re.search(r"[\u0B80-\u0BFF]", result.text)
    assert result.model.endswith("nllb-200-distilled-600M")
