"""Script detection for uploaded text.

The detector is deliberately simple and offline: it counts characters inside
fixed Unicode blocks and picks the dominant script. It is not a language
identifier.

Known limitation: romanised Indic text (for example "vanakkam", "mera naam")
uses Latin characters and is therefore classified as English. The UI and
``docs/INGEST.md`` say so explicitly. Supporting romanised detection would need
a different model and is out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Script name, ISO code, NLLB code, Tesseract code, Unicode ranges.
_SCRIPTS: tuple[tuple[str, str, str, str, tuple[tuple[int, int], ...]], ...] = (
    ("Devanagari", "hi", "hin_Deva", "hin", ((0x0900, 0x097F), (0xA8E0, 0xA8FF))),
    ("Tamil", "ta", "tam_Taml", "tam", ((0x0B80, 0x0BFF),)),
    ("Telugu", "te", "tel_Telu", "tel", ((0x0C00, 0x0C7F),)),
    ("Kannada", "kn", "kan_Knda", "kan", ((0x0C80, 0x0CFF),)),
    ("Malayalam", "ml", "mal_Mlym", "mal", ((0x0D00, 0x0D7F),)),
    ("Bengali", "bn", "ben_Beng", "ben", ((0x0980, 0x09FF),)),
    ("Gujarati", "gu", "guj_Gujr", "guj", ((0x0A80, 0x0AFF),)),
    ("Gurmukhi", "pa", "pan_Guru", "pan", ((0x0A00, 0x0A7F),)),
    ("Odia", "or", "ory_Orya", "ori", ((0x0B00, 0x0B7F),)),
    ("Arabic", "ur", "urd_Arab", "urd", ((0x0600, 0x06FF), (0x0750, 0x077F))),
)

_LATIN_RANGES: tuple[tuple[int, int], ...] = (
    (0x0041, 0x005A),
    (0x0061, 0x007A),
    (0x00C0, 0x024F),
)

@dataclass(frozen=True)
class DetectedLanguage:
    code: str
    script: str
    nllb: str
    tesseract: str
    is_english: bool


_ENGLISH = DetectedLanguage(
    code="en",
    script="Latin",
    nllb="eng_Latn",
    tesseract="eng",
    is_english=True,
)


def _count(text: str, ranges: tuple[tuple[int, int], ...]) -> int:
    return sum(1 for character in text if any(low <= ord(character) <= high for low, high in ranges))


def contains_supported_non_latin_script(text: str) -> bool:
    """Return whether text contains characters from a supported native script."""

    return any(
        any(low <= ord(character) <= high for low, high in ranges)
        for character in text
        for _script, _code, _nllb, _tesseract, ranges in _SCRIPTS
    )


def detect_language(text: str) -> DetectedLanguage:
    """Return the dominant script and its language metadata.

    Latin dominance (including ties) resolves to English. Text with no letters
    also resolves to English, which keeps numeric-only documents on the existing
    English path.
    """

    if not text:
        return _ENGLISH

    latin = _count(text, _LATIN_RANGES)
    best: tuple[str, str, str, str, int] | None = None
    for script, code, nllb, tesseract, ranges in _SCRIPTS:
        count = _count(text, ranges)
        if count and (best is None or count > best[4]):
            best = (script, code, nllb, tesseract, count)

    if best is None:
        return _ENGLISH
    if latin >= best[4]:
        return _ENGLISH

    script, code, nllb, tesseract, _count_value = best
    return DetectedLanguage(
        code=code,
        script=script,
        nllb=nllb,
        tesseract=tesseract,
        is_english=False,
    )
