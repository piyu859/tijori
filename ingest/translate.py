"""On-device English translation with NLLB-200.

The model is loaded lazily and only in the process that calls this module (the
Streamlit UI). The gateway process never imports the weights, so its memory
profile and behaviour are unchanged.

Failure policy: any load or inference failure becomes
:class:`TranslationUnavailable`. The UI must refuse to send native-script text
to the gateway when this happens; forwarding it would let detection miss
native-script identifiers.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

from app.config import get_translate_enabled, get_translate_model
from ingest.language import DetectedLanguage, contains_supported_non_latin_script

#: Character budget per NLLB chunk. 512 tokens is the model limit; Indic text
#: is roughly 2-3 characters per token, so this leaves headroom.
_CHUNK_CHARS = 800

_MODEL_CACHE: tuple[str, object, object] | None = None
_MODEL_LOCK = threading.Lock()


class TranslationUnavailable(RuntimeError):
    """The local translation path cannot run. The caller must fail closed."""


@dataclass(frozen=True)
class Translation:
    text: str
    source_lang: str
    model: str
    duration_ms: int
    warnings: tuple[str, ...]


def translate_to_english(text: str, detected: DetectedLanguage) -> Translation:
    """Translate ``text`` to English locally.

    English input is returned unchanged with a zero duration and no model call.
    """

    started = time.perf_counter()
    if detected.is_english:
        return Translation(
            text=text,
            source_lang=detected.code,
            model="none",
            duration_ms=0,
            warnings=(),
        )

    if not get_translate_enabled():
        raise TranslationUnavailable(
            "on-device translation is disabled (TIJORI_TRANSLATE_ENABLED=0); "
            "refusing to forward un-translated text"
        )

    model_name = get_translate_model()
    try:
        translated = translate_text(
            text,
            source_nllb=detected.nllb,
            target_nllb="eng_Latn",
            model_name=model_name,
        )
    except TranslationUnavailable:
        raise
    except (OSError, ImportError, RuntimeError, ValueError, IndexError) as exc:
        raise TranslationUnavailable(f"on-device translation failed: {exc}") from exc

    if not translated.strip():
        raise TranslationUnavailable("on-device translation returned empty text")
    if contains_supported_non_latin_script(translated):
        raise TranslationUnavailable(
            "on-device translation left native-script text in its output; "
            "refusing to forward it"
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    return Translation(
        text=translated,
        source_lang=detected.code,
        model=model_name,
        duration_ms=duration_ms,
        warnings=_digit_warnings(text, translated),
    )


def translate_text(
    text: str,
    *,
    source_nllb: str,
    target_nllb: str,
    model_name: str | None = None,
) -> str:
    """Translate between two NLLB codes.

    Public for the evaluation harness, which needs English→Indic as well as
    Indic→English. The UI uses :func:`translate_to_english`.
    """

    if not text.strip():
        return text

    model_name = model_name or get_translate_model()
    tokenizer, model = _load_model(model_name)
    target_id = tokenizer.convert_tokens_to_ids(target_nllb)
    if target_id is None or int(target_id) < 0:
        raise TranslationUnavailable(f"unknown NLLB target language: {target_nllb}")

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    translated_lines: list[str] = []
    for line in lines:
        if not line.strip():
            translated_lines.append(line)
            continue
        pieces = _split_long(line)
        translated_lines.append(
            " ".join(
                _translate_chunk(
                    tokenizer,
                    model,
                    piece,
                    source_nllb=source_nllb,
                    target_id=int(target_id),
                )
                for piece in pieces
            )
        )
    return "\n".join(translated_lines).strip()


def _load_model(model_name: str) -> tuple[object, object]:
    """Load and cache the tokenizer and model. Returns ``(tokenizer, model)``."""

    global _MODEL_CACHE
    if _MODEL_CACHE is not None and _MODEL_CACHE[0] == model_name:
        return _MODEL_CACHE[1], _MODEL_CACHE[2]

    with _MODEL_LOCK:
        if _MODEL_CACHE is not None and _MODEL_CACHE[0] == model_name:
            return _MODEL_CACHE[1], _MODEL_CACHE[2]

        try:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        except ImportError as exc:
            raise TranslationUnavailable(
                "on-device translation needs the 'transformers' and 'torch' packages"
            ) from exc

        try:
            torch.set_num_threads(4)
            tokenizer = AutoTokenizer.from_pretrained(model_name)
            model = AutoModelForSeq2SeqLM.from_pretrained(
                model_name,
                torch_dtype=torch.float32,
            )
            model.to("cpu")
            model.eval()
        except (OSError, ImportError, RuntimeError, ValueError) as exc:
            raise TranslationUnavailable(
                f"could not load the on-device translation model '{model_name}': {exc}"
            ) from exc

        _MODEL_CACHE = (model_name, tokenizer, model)
        return tokenizer, model


def _translate_chunk(
    tokenizer: object,
    model: object,
    chunk: str,
    *,
    source_nllb: str,
    target_id: int,
) -> str:
    import torch

    inputs = tokenizer(
        chunk,
        return_tensors="pt",
        truncation=True,
        max_length=512,
        src_lang=source_nllb,
    )
    with torch.inference_mode():
        generated = model.generate(
            **inputs,
            max_length=512,
            num_beams=2,
            forced_bos_token_id=target_id,
        )
    return tokenizer.batch_decode(generated, skip_special_tokens=True)[0].strip()


def _split_long(line: str, limit: int = _CHUNK_CHARS) -> list[str]:
    """Split an over-long line on sentence/word boundaries, never mid-word."""

    if len(line) <= limit:
        return [line]

    pieces: list[str] = []
    for sentence in re.split(r"(?<=[.!?।])\s+", line):
        if not sentence.strip():
            continue
        if len(sentence) <= limit:
            pieces.append(sentence)
            continue
        current = ""
        for word in sentence.split():
            candidate = f"{current} {word}".strip()
            if len(candidate) > limit and current:
                pieces.append(current)
                current = word
            else:
                current = candidate
        if current:
            pieces.append(current)
    return pieces or [line]


def _digit_warnings(source: str, output: str) -> tuple[str, ...]:
    """Warn about every digit sequence that did not survive translation."""

    source_digits = list(dict.fromkeys(re.findall(r"\d+", source)))
    output_digits = set(re.findall(r"\d+", output))
    return tuple(
        f"digit sequence {digits} is missing from the translation"
        for digits in source_digits
        if digits not in output_digits
    )
