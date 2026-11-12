# Multilingual intake

Tijori accepts a document in a major Indic script, extracts its text locally,
translates it to English on-device, and then runs the existing gateway
unchanged:

```
upload → extract (local) → detect script → translate to English (local NLLB)
       → existing gateway: detect → mask → LLM → reinsert → English answer
```

The gateway itself is unchanged. It still receives English text and still
returns an English answer with every identifier restored.

## Supported formats and limits

| Format | How it is read |
|---|---|
| `.txt`, `.md`, `.csv` | UTF-8 (invalid bytes replaced), BOM stripped |
| `.docx` | stdlib `zipfile` + `word/document.xml`; paragraphs and table rows become lines |
| `.pdf` | `pdftotext -layout -enc UTF-8`; scanned PDFs fall back to `pdftoppm` + OCR |
| `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff` | `tesseract` CLI |

Limits, enforced before or during extraction:

- 10 MB per upload
- 20 pages per PDF/DOCX
- 200,000 extracted characters

A file that exceeds a limit, has an unsupported extension, or yields no text is
rejected with a human-readable error. Nothing is silently truncated.

## Language table

Detection is Unicode-block counting over the dominant script. It is a script
detector, not a language identifier.

| Script | Code | NLLB | Tesseract |
|---|---|---|---|
| Devanagari | `hi` | `hin_Deva` | `hin` |
| Tamil | `ta` | `tam_Taml` | `tam` |
| Telugu | `te` | `tel_Telu` | `tel` |
| Kannada | `kn` | `kan_Knda` | `kan` |
| Malayalam | `ml` | `mal_Mlym` | `mal` |
| Bengali | `bn` | `ben_Beng` | `ben` |
| Gujarati | `gu` | `guj_Gujr` | `guj` |
| Gurmukhi | `pa` | `pan_Guru` | `pan` |
| Odia | `or` | `ory_Orya` | `ori` |
| Arabic (Urdu) | `ur` | `urd_Arab` | `urd` |
| Latin | `en` | `eng_Latn` | `eng` |

Latin-dominant text is treated as English. A document with more Latin letters
than any single Indic block is therefore **not** translated.

### Romanised-script limitation

Romanised Indic text — `vanakkam`, `mera naam`, `naan Chennai-la irukken` — uses
Latin characters and is classified as English. Tijori will not translate it,
and native-script identifiers written in Latin letters are outside the
translation path. This is a known limitation, not a silent success.

## Translation model

The default model is `facebook/nllb-200-distilled-600M` (~2.4 GB), loaded lazily
in the Streamlit process only. The gateway process never imports the weights, so
its memory profile is unchanged.

```bash
# one-time download; afterwards the UI works offline
python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('facebook/nllb-200-distilled-600M')"
```

Configuration (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `TIJORI_TRANSLATE_MODEL` | `facebook/nllb-200-distilled-600M` | NLLB model id |
| `TIJORI_TRANSLATE_ENABLED` | `1` | Set `0` to disable translation |

`TIJORI_TRANSLATE_ENABLED=0` is the fail-closed switch: native-script uploads
are refused, while English uploads continue to work.

## OCR installation

Tesseract and the Indic traineddata packs are system packages. On Debian/Ubuntu:

```bash
sudo apt-get install -y tesseract-ocr-tam tesseract-ocr-tel tesseract-ocr-kan \
  tesseract-ocr-mal tesseract-ocr-hin tesseract-ocr-mar tesseract-ocr-ben \
  tesseract-ocr-guj tesseract-ocr-pan tesseract-ocr-ori
tesseract --list-langs
```

Scanned PDFs also need `pdftoppm` (poppler-utils). If `tesseract` or the packs
are missing, Tijori returns a clear "OCR requires …" error instead of guessing.
The OCR helper requests `eng` plus every installed Indic pack, because script
detection runs on the OCR output.

## Privacy notes

- **On-device only.** OCR and translation run locally. The only network access
  in this path is the one-time Hugging Face model download. There is no cloud
  OCR, cloud translation, or new data processor.
- **No persistence of user content.** Extracted and translated text live in
  memory only. They are never written to the audit table, logs, or disk.
- **Temp files.** Uploaded bytes are written only inside a per-call
  `tempfile.TemporaryDirectory()`, with mode `0600`, and the directory is
  removed in a `finally` block. OCR images and `pdftoppm` output live there too.
- **Fail closed.** If the text is not English and translation is unavailable or
  failed, the UI refuses to run the gateway. Forwarding native-script text
  would let detection miss native-script identifiers and turn the request into
  a silent leak.
- **Audit metadata.** `source_lang` records the original document language. The
  audit row still contains no document text and no placeholder mapping. Old
  databases are migrated additively with `ALTER TABLE … ADD COLUMN` and default
  to `"en"`.

## Follow-up: native-language answers

Native-language **answers** are out of scope. Producing them would require
translating the masked model output inside the gateway before reinsertion, which
changes the pipeline and the response contract. The current slice deliberately
stops at an English answer with originals restored locally.

## Verification

```bash
pytest tests/ingest -q          # extraction, detection, translation
pytest tests/demo -q            # upload wiring and existing UI behaviour
pytest -q                       # full suite
python -m evaluation.multilingual   # writes reports/multilingual.md
```

The multilingual report measures translation survival, digit preservation,
leakage on the translated path (must be 0), and numeric-ID recall. It does not
report detection precision/recall for native text, because there are no
native-language gold labels.
