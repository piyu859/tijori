# Multilingual intake evaluation

- Generated: 2026-11-12T21:05:15+00:00
- Commit: `db1978a`
- Documents: 10
- Target languages: Tamil, Telugu, Kannada, Hindi
- Translation model: `facebook/nllb-200-distilled-600M` (CPU, on-device)
- Gateway: reachable

## What this measures

The English dataset text is translated to each target language, passed
through the local extractor and script detector, translated back to
English, and then sent to `/v1/sanitize`. The report measures translation
survival, digit preservation, leakage on the translated path, and recall
for numeric identifiers whose digits survived translation.

It does **not** measure detection precision/recall for native text: there
are no native-language gold labels. Reporting such a number would be
dishonest.

## Per-language results

| Language | Attempts | Translation success | Digit preservation | Leakage | Numeric-ID recall |
|---|---|---|---|---|---|
| Tamil | 10 | 20.0% | 17.4% | 3 | 83.3% |
| Telugu | 10 | 80.0% | 74.9% | 5 | 100.0% |
| Kannada | 10 | 50.0% | 47.9% | 11 | 85.7% |
| Hindi | 10 | 100.0% | 92.8% | 12 | 65.5% |

## Overall

| Metric | Result |
|---|---|
| Translation success rate | 62.5% |
| Digit-preservation rate | 58.2% |
| Leakage count on translated path | 31 |
| Numeric-ID recall | 81.9% |

**FAIL:** 31 gold value occurrence(s) survived into the sanitized translated text. Investigate before shipping.

## Errors

| Document | Language | Error |
|---|---|---|
| loan_001 | Telugu | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_003 | Tamil | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_003 | Kannada | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_004 | Tamil | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_004 | Kannada | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_005 | Tamil | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_005 | Kannada | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_006 | Tamil | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_006 | Kannada | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_007 | Tamil | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_008 | Tamil | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_008 | Telugu | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_009 | Tamil | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_010 | Tamil | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |
| loan_010 | Kannada | TranslationUnavailable: on-device translation left native-script text in its output; refusing to forward it |

## Caveats

- The dataset is English/Hindi-English. Translation quality, not
  detection quality, dominates the round-trip numbers.
- `eng_Latn` is used as the source for Hinglish documents; NLLB may
  leave code-mixed phrases partly untranslated.
- Numeric-ID recall excludes identifiers whose digits did not survive
  translation. Those are translation failures, not detector misses.
- Native-language answers (back-translation of the model output) are
  out of scope; see `docs/INGEST.md`.
