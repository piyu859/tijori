"""Multilingual intake evaluation.

For ten labelled dataset documents, translate English→Tamil/Telugu/Kannada/Hindi
with the local NLLB model, run extract → detect → translate back → ``/v1/sanitize``,
and write ``reports/multilingual.md``.

Honesty rules:

* There are no native-language gold labels. This report does **not** claim
  detection precision/recall for native text.
* Leakage is measured against the original gold values that survive the
  translation round-trip, and must be zero.
* Numeric-ID recall only counts identifiers whose digits survived translation;
  a lost digit is a translation failure, not a detector miss.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ingest.extract import ExtractionError, extract
from ingest.language import detect_language
from ingest.translate import TranslationUnavailable, translate_text, translate_to_english

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "data" / "dataset.jsonl"
REPORT_PATH = ROOT / "reports" / "multilingual.md"
DEFAULT_API_URL = os.getenv("TIJORI_API_URL", "http://127.0.0.1:8000")
DOC_COUNT = 10
TARGETS: tuple[tuple[str, str, str], ...] = (
    ("ta", "tam_Taml", "Tamil"),
    ("te", "tel_Telu", "Telugu"),
    ("kn", "kan_Knda", "Kannada"),
    ("hi", "hin_Deva", "Hindi"),
)
NUMERIC_TYPES = {"AADHAAR", "PHONE", "BANK_ACCOUNT", "CREDIT_CARD"}
_LATIN_RE = re.compile(r"[A-Za-z]")
_DIGITS_RE = re.compile(r"\d+")


def load_documents(path: Path = DATASET_PATH, limit: int = DOC_COUNT) -> list[dict]:
    documents = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return documents[:limit]


def commit_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def leak_occurrences(entity: dict, payload: str) -> int:
    value = entity.get("text", "")
    variants = {value}
    if entity.get("type") == "AADHAAR":
        variants.add(re.sub(r"[\s-]", "", value))
    best = 0
    for variant in variants:
        if not variant:
            continue
        if variant.isdigit():
            best = max(best, payload.count(variant))
        else:
            best = max(best, payload.casefold().count(variant.casefold()))
    return best


def digit_sequences(text: str) -> list[str]:
    return list(dict.fromkeys(_DIGITS_RE.findall(text)))


def _md_table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return "\n".join(lines)


def _pct(numerator: int, denominator: int) -> str:
    return "—" if denominator == 0 else f"{100 * numerator / denominator:.1f}%"


def evaluate_document(document: dict, api_url: str) -> list[dict]:
    """Run one document through all target languages."""

    results: list[dict] = []
    source_text = document["text"]
    source_digits = digit_sequences(source_text)

    for code, nllb, script in TARGETS:
        row = {
            "doc_id": document["id"],
            "language": script,
            "language_code": code,
            "translation_ok": False,
            "digits_total": len(source_digits),
            "digits_preserved": 0,
            "leakage": 0,
            "numeric_total": 0,
            "numeric_detected": 0,
            "error": "",
        }
        try:
            native_text = translate_text(
                source_text,
                source_nllb="eng_Latn",
                target_nllb=nllb,
            )
            extraction = extract(f"{document['id']}.txt", native_text.encode("utf-8"))
            detected = detect_language(extraction.text)
            if detected.code != code:
                raise ValueError(
                    f"detected {detected.code}/{detected.script}, expected {code}/{script}"
                )

            back = translate_to_english(extraction.text, detected)
            if not back.text.strip() or not _LATIN_RE.search(back.text):
                raise ValueError("back-translation is empty or not Latin script")

            back_digits = set(digit_sequences(back.text))
            row["digits_preserved"] = sum(
                1 for digits in source_digits if digits in back_digits
            )
            row["translation_ok"] = True

            response = httpx.post(
                f"{api_url}/v1/sanitize",
                json={
                    "text": back.text,
                    "doc_type": document.get("doc_type", "other"),
                    "source_lang": code,
                },
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
            sanitized = payload.get("sanitized_text", "")

            for entity in document.get("entities", []):
                row["leakage"] += leak_occurrences(entity, sanitized)

            detected_values = [
                back.text[entity.get("start", 0) : entity.get("end", 0)]
                for entity in payload.get("entities", [])
                if isinstance(entity.get("start"), int) and isinstance(entity.get("end"), int)
            ]
            compact_detected = [
                re.sub(r"[\s-]", "", value) for value in detected_values
            ]
            for entity in document.get("entities", []):
                if entity.get("type") not in NUMERIC_TYPES:
                    continue
                compact = re.sub(r"[\s-]", "", entity.get("text", ""))
                if not compact or not compact.isdigit():
                    continue
                if compact not in re.sub(r"[\s-]", "", back.text):
                    # Translation lost the digits; exclude from detector recall.
                    continue
                row["numeric_total"] += 1
                if any(compact in value for value in compact_detected):
                    row["numeric_detected"] += 1

        except (TranslationUnavailable, ExtractionError, ValueError, httpx.HTTPError) as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        results.append(row)
    return results


def render_report(rows: list[dict], documents: int, gateway_ok: bool) -> str:
    lines = [
        "# Multilingual intake evaluation",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Commit: `{commit_hash()}`",
        f"- Documents: {documents}",
        "- Target languages: Tamil, Telugu, Kannada, Hindi",
        "- Translation model: `facebook/nllb-200-distilled-600M` (CPU, on-device)",
        f"- Gateway: {'reachable' if gateway_ok else 'offline (results blocked)'}",
        "",
        "## What this measures",
        "",
        "The English dataset text is translated to each target language, passed",
        "through the local extractor and script detector, translated back to",
        "English, and then sent to `/v1/sanitize`. The report measures translation",
        "survival, digit preservation, leakage on the translated path, and recall",
        "for numeric identifiers whose digits survived translation.",
        "",
        "It does **not** measure detection precision/recall for native text: there",
        "are no native-language gold labels. Reporting such a number would be",
        "dishonest.",
        "",
        "## Per-language results",
        "",
    ]

    table_rows: list[list] = []
    for code, _nllb, script in TARGETS:
        language_rows = [row for row in rows if row["language_code"] == code]
        attempts = len(language_rows)
        successes = sum(1 for row in language_rows if row["translation_ok"])
        digits_total = sum(row["digits_total"] for row in language_rows)
        digits_preserved = sum(row["digits_preserved"] for row in language_rows)
        leakage = sum(row["leakage"] for row in language_rows)
        numeric_total = sum(row["numeric_total"] for row in language_rows)
        numeric_detected = sum(row["numeric_detected"] for row in language_rows)
        table_rows.append(
            [
                script,
                attempts,
                _pct(successes, attempts),
                _pct(digits_preserved, digits_total),
                leakage,
                _pct(numeric_detected, numeric_total),
            ]
        )

    lines.append(
        _md_table(
            [
                "Language",
                "Attempts",
                "Translation success",
                "Digit preservation",
                "Leakage",
                "Numeric-ID recall",
            ],
            table_rows,
        )
    )
    lines.append("")

    total_attempts = len(rows)
    total_success = sum(1 for row in rows if row["translation_ok"])
    total_digits = sum(row["digits_total"] for row in rows)
    total_preserved = sum(row["digits_preserved"] for row in rows)
    total_leakage = sum(row["leakage"] for row in rows)
    total_numeric = sum(row["numeric_total"] for row in rows)
    total_detected = sum(row["numeric_detected"] for row in rows)

    lines.extend(
        [
            "## Overall",
            "",
            _md_table(
                ["Metric", "Result"],
                [
                    ["Translation success rate", _pct(total_success, total_attempts)],
                    ["Digit-preservation rate", _pct(total_preserved, total_digits)],
                    ["Leakage count on translated path", total_leakage],
                    ["Numeric-ID recall", _pct(total_detected, total_numeric)],
                ],
            ),
            "",
        ]
    )

    if total_leakage:
        lines.append(
            f"**FAIL:** {total_leakage} gold value occurrence(s) survived into the "
            "sanitized translated text. Investigate before shipping."
        )
    else:
        lines.append(
            "**Leakage gate:** 0 gold value occurrences in the sanitized translated "
            "text. This is the required result for the slice."
        )
    lines.append("")

    errors = [row for row in rows if row["error"]]
    if errors:
        lines.extend(["## Errors", "", _md_table(["Document", "Language", "Error"], [
            [row["doc_id"], row["language"], row["error"]] for row in errors
        ]), ""])

    lines.extend(
        [
            "## Caveats",
            "",
            "- The dataset is English/Hindi-English. Translation quality, not",
            "  detection quality, dominates the round-trip numbers.",
            "- `eng_Latn` is used as the source for Hinglish documents; NLLB may",
            "  leave code-mixed phrases partly untranslated.",
            "- Numeric-ID recall excludes identifiers whose digits did not survive",
            "  translation. Those are translation failures, not detector misses.",
            "- Native-language answers (back-translation of the model output) are",
            "  out of scope; see `docs/INGEST.md`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    documents = load_documents()
    if not documents:
        print("no documents found", file=sys.stderr)
        return 2

    gateway_ok = False
    try:
        gateway_ok = httpx.get(f"{DEFAULT_API_URL}/health", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        gateway_ok = False

    if not gateway_ok:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            render_report([], len(documents), gateway_ok=False),
            encoding="utf-8",
        )
        print(f"gateway offline at {DEFAULT_API_URL}; wrote {REPORT_PATH}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    for index, document in enumerate(documents, start=1):
        print(f"[{index}/{len(documents)}] {document['id']}", file=sys.stderr)
        rows.extend(evaluate_document(document, DEFAULT_API_URL))

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render_report(rows, len(documents), gateway_ok=True), encoding="utf-8")

    leakage = sum(row["leakage"] for row in rows)
    print(f"wrote {REPORT_PATH}; leakage={leakage}", file=sys.stderr)
    return 0 if leakage == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
