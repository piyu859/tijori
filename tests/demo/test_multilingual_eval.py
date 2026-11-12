"""Pure helpers in the multilingual evaluation harness (no model load)."""

from __future__ import annotations

from evaluation.multilingual import digit_sequences, leak_occurrences, render_report


def test_digit_sequences_are_unique_and_ordered():
    assert digit_sequences("1234 1234 5678-9012") == ["1234", "5678", "9012"]


def test_leak_occurrences_matches_aadhaar_without_separators():
    entity = {"type": "AADHAAR", "text": "1234 5678 9012"}
    assert leak_occurrences(entity, "masked 123456789012") == 1
    assert leak_occurrences(entity, "masked <AADHAAR_1>") == 0


def test_report_states_zero_leakage_when_clean():
    rows = [
        {
            "doc_id": "loan_001",
            "language": "Tamil",
            "language_code": "ta",
            "translation_ok": True,
            "digits_total": 2,
            "digits_preserved": 2,
            "leakage": 0,
            "numeric_total": 1,
            "numeric_detected": 1,
            "error": "",
        }
    ]
    report = render_report(rows, documents=1, gateway_ok=True)
    assert "Leakage gate" in report
    assert "100.0%" in report
    assert "does **not** measure detection precision/recall" in report
