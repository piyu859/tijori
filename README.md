# Tijori — DPDP Privacy Gateway

Tijori sits between an enterprise document workflow and a large language model.
It detects Indian personal data, replaces it with typed placeholders, forwards
only the sanitized text, restores the originals locally in the response, and
records a PII-free audit trail for every request.

The design follows the pattern named in the DPDP Rules, 2025, Rule 6(1)(a) —
"masking or the use of virtual tokens mapped to that personal data" — so the
model never receives an identifier it could memorise, leak, or train on.

```
document ──► detect ──► mask ──► forward sanitized ──► reinsert ──► answer
                │          │              │                │
          Presidio +   <PERSON_1>   external LLM     original values
          GLiNER       <AADHAAR_1>  (Ollama/OpenAI)  restored locally
                                     │
                                audit row (no PII)
```

## Capabilities

- **Detection** — Microsoft Presidio with custom India recognisers (Aadhaar with
  Verhoeff checksum, PAN, GSTIN, Voter EPIC, Passport, each context-gated) plus
  regex patterns for phone, email, date of birth, bank accounts and cards, and
  GLiNER (`urchade/gliner_multi_pii-v1`) with spaCy `en_core_web_sm` for people
  and addresses. Overlapping spans are resolved deterministically; detection
  failures are fail-closed.
- **Masking** — every entity becomes a typed placeholder such as `<AADHAAR_1>`
  or `<PERSON_1>`. Originals exist only in a per-request in-memory vault.
- **Reinsertion** — exact-token restoration, case tolerant, with duplicate and
  reordered tokens supported. Dropped or malformed placeholders are reported as
  `missing` or `unknown`; the gateway never guesses a value.
- **Leakage gate** — the outbound payload is scanned for every mapped original
  before the model is called. A hit aborts the request and is audited.
- **Pluggable provider** — any OpenAI-compatible endpoint. Ollama is the local
  default; switching to OpenRouter, Groq or OpenAI is an environment change.
- **Audit** — SQLite, one PII-free row per request: request id, entity counts,
  outbound payload, provider/model, latency, status, leakage count.
- **Access control** — optional constant-time `X-API-Key` gate on every `/v1/*`
  route.
- **Retention** — 365-day default window, explicit purge CLI, startup dry-run
  report.
- **Monitoring** — structured warnings on leakage, provider failure and error
  records; no document text is ever logged.
- **Demo UI** — Streamlit three-pane view (input, outbound payload, final
  response) with a custom instruction field and an audit browser.
- **Multilingual intake** — upload txt, md, csv, docx, pdf or a scanned image;
  OCR and script detection run locally, and Tamil, Telugu, Kannada, Malayalam,
  Hindi, Marathi, Bengali, Gujarati, Punjabi or Odia text is translated to
  English on-device with NLLB-200 before the unchanged gateway runs. A
  native-script upload is refused if translation is unavailable. See
  `docs/INGEST.md`.
- **Evaluation** — 50-document labelled dataset (English and Hindi-English),
  precision/recall, leakage sweep, reinsertion accuracy and latency baseline.

## Stack

Python 3.10+ (verified on 3.14) · FastAPI · Streamlit · SQLite · Microsoft
Presidio · GLiNER · spaCy · Ollama (`qwen2.5:3b`) · NLLB-200 (on-device intake).

## Quickstart

```bash
source .venv/bin/activate
pip install -r requirements.txt

ollama pull qwen2.5:3b        # one-time, ~1.9 GB
cp .env.example .env

make test
make api                      # http://127.0.0.1:8000/docs
make ui                       # http://127.0.0.1:8501
```

Once the model weights are present locally, the gateway needs no internet
access: detection runs on-device and the default model is a local Ollama
instance.

## Configuration

All settings are environment variables, read once at startup (except the API
key and retention window, which are re-read per request).

| Variable | Default | Purpose |
|---|---|---|
| `TIJORI_LLM_PROVIDER` | `openai_compatible` | Provider adapter |
| `TIJORI_LLM_BASE_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compatible endpoint |
| `TIJORI_LLM_API_KEY` | `ollama` | Provider credential |
| `TIJORI_LLM_MODEL` | `qwen2.5:3b` | Model name |
| `TIJORI_LLM_TIMEOUT_S` | `120` | Provider timeout |
| `TIJORI_LLM_TEMPERATURE` | `0.2` | Sampling temperature |
| `TIJORI_LLM_MAX_TOKENS` | `512` | Response cap |
| `TIJORI_DB_PATH` | `data/audit.db` | Audit database |
| `TIJORI_API_URL` | `http://127.0.0.1:8000` | Gateway URL used by the UI |
| `TIJORI_MAX_INPUT_CHARS` | `100000` | Request size limit |
| `TIJORI_LOG_LEVEL` | `INFO` | Log verbosity |
| `TIJORI_API_KEY` | *(empty)* | Enables `X-API-Key` auth on `/v1/*` |
| `TIJORI_AUDIT_RETENTION_DAYS` | `365` | Audit retention window |
| `TIJORI_TRANSLATE_MODEL` | `facebook/nllb-200-distilled-600M` | On-device NLLB model for uploads |
| `TIJORI_TRANSLATE_ENABLED` | `1` | Set `0` to refuse native-script uploads |

To use an external provider, change only the three `TIJORI_LLM_*` values and
keep the adapter unchanged.

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Provider and model status |
| `POST` | `/v1/sanitize` | Detect and mask; returns placeholders, never originals |
| `POST` | `/v1/process` | Sanitize, call the model, reinsert, audit |
| `GET` | `/v1/audit` | Recent audit records (`?limit=1..500`) |
| `GET` | `/v1/audit/{request_id}` | One audit record |

Interactive schema at `/docs`. When `TIJORI_API_KEY` is set, every `/v1/*`
request needs `X-API-Key: <key>`; `/health` stays public. Missing or wrong keys
return `401 {"detail": "unauthorized"}`.

```bash
curl -s localhost:8000/v1/process \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $TIJORI_API_KEY" \
  -d '{"text": "My Aadhaar is 1234-5678-9012.", "doc_type": "kyc",
       "task": "summarize",
       "instruction": "Summarize in 3 bullet points and keep all placeholders."}'
```

## Demo UI

```bash
# shell 1 — model, kept resident
OLLAMA_KEEP_ALIVE=30m ollama run qwen2.5:3b "Reply with OK."

# shell 2 — gateway
uvicorn app.main:app --port 8000

# shell 3 — UI
streamlit run ui/app.py
```

### Uploading documents

Use **Upload document** in the sidebar to load a `.txt`, `.md`, `.csv`, `.docx`,
`.pdf`, `.png`, `.jpg`, `.jpeg`, `.tif` or `.tiff` file. Extraction and OCR run
locally; Indic text is translated to English on-device with NLLB-200 and placed
in the editable document box. The language badge (`Tamil → English, on-device`)
and an expander with the native original, page count and OCR metadata show what
happened. If translation fails, **Run gateway** is disabled rather than sending
un-translated native-script text to the gateway. Limits and install notes are in
`docs/INGEST.md`.

Load a sample, edit the instruction, and run. The default sample `loan_001`
should return three bullets with all ten placeholders restored (Replaced 10,
Missing 0), and the audit tab should show one PII-free row. A faithful summary
is often as long as the input because every identifier token has to travel
through reinsertion; the **Missing** counter is the honest signal. Some
documents lose placeholders to the model (`loan_005` drops seven of nine) and the
gateway reports them instead of guessing.

Warm latency (`reports/metrics.md`): `/v1/process` p50 ≈ 4.8 s, sanitize-only
p50 ≈ 0.26 s. Cold starts add several seconds of model load.

## Evaluation

`python -m evaluation.run` replays the labelled dataset against a running
gateway and writes `reports/metrics.md`.

| Metric | Result |
|---|---|
| Documents sanitized | 50 / 50 |
| PII leakage | **0** |
| Macro precision / recall | 0.936 / 0.957 |
| Reinsertion accuracy (API-reported) | 1.000 |
| Sanitize latency (p50 / p95) | 256 ms / 296 ms |
| `/v1/process` latency (p50 / p95) | 4 775 ms / 6 154 ms |
| Direct provider call (p50, warm) | 4 490 ms |
| Gateway overhead | 285 ms (6.0 %) |

API-reported reinsertion accuracy is `replaced / (replaced + unknown)`; dropped
placeholders are counted separately and never fabricated. Full per-type and
per-document breakdowns are in `reports/metrics.md`.

## Security and operations

- **Auth** — set `TIJORI_API_KEY` before exposing the service beyond localhost.
  Unset means no authentication, which is convenient for local development and
  unsuitable for a shared deployment.
- **Retention** — audit rows default to a 365-day window. Purge is deliberate:

  ```bash
  python -m app.audit.retention --days 365 --dry-run
  python -m app.audit.retention --days 365
  ```

  Startup only reports how many rows are eligible; it never deletes.
- **Audit content** — counts, placeholders and the outbound payload are stored;
  originals and the placeholder mapping are not.
- **Logs** — warnings carry a request id, counts and status only.

See `docs/DPDP-ALIGNMENT.md` for the obligation-by-obligation mapping and
`docs/ENTITY-COVERAGE.md` for the entity taxonomy.

## Tests

```bash
pytest -q        # 108 cases
```

The suite covers detection and checksums, masking, reinsertion (reorder,
duplicate, case, drop, malformed), the four-stage pipeline, provider failure
paths, the leakage gate, API auth, audit retention and the demo UI's offline
and PII behaviour. The live Ollama test is marked `slow` and skips when no local
endpoint is reachable.

## Project layout

| Path | Contents |
|---|---|
| `CONTRACT.md` | Frozen interfaces, placeholders, API, audit schema |
| `privacy/` | Detection, masking, vault, reinsertion |
| `app/` | FastAPI routes, pipeline, provider, audit, configuration |
| `ui/` | Streamlit demo |
| `ingest/` | On-device extraction, script detection and NLLB translation |
| `evaluation/` | Dataset replay and metrics report |
| `data/` | Labelled dataset and local audit database |
| `reports/` | Generated evaluation report |
| `docs/` | DPDP mapping, entity coverage, design notes |

## Limitations

- Detection is tuned for Indian documents and English/Hindi-English text; the
  taxonomy does not yet include vehicle registration numbers.
- Multilingual intake translates native scripts to English before detection;
  romanised Indic text (`vanakkam`, `mera naam`) is classified as English and is
  not translated. Native-language answers are out of scope.
- Reinsertion is exact-token: if the model rewrites a placeholder, it is
  reported as `unknown` rather than repaired.
- Purpose-based policies are recorded (`purpose` on each request) but not
  enforced against a consent record.
- Audit monitoring emits warnings but has no anomaly detection or alert
  delivery.
- The audit store is single-node SQLite; a multi-instance deployment needs a
  shared database.
- Tijori is a technical safeguard. It is not a DPDP compliance certificate, and
  notice, consent, breach handling and data-principal rights remain the Data
  Fiduciary's responsibility.

## Regulatory references

| Source | Purpose |
|---|---|
| [MeitY — DPDP Rules, 2025](https://www.meity.gov.in/documents/act-and-policies/digital-personal-data-protection-rules-2025-gDOxUjMtQWa) | Official landing page |
| [G.S.R. 846(E)](https://egazette.gov.in/WriteReadData/2025/267650.pdf) | Rules text (Rules 1–23 and Schedules) |
| [G.S.R. 843(E)](https://egazette.gov.in/WriteReadData/2025/267647.pdf) | Commencement notification |
| [Presidio supported entities](https://github.com/microsoft/presidio/blob/main/docs/supported_entities.md) | Entity taxonomy |

Rules 6 and 7 bind from 13 May 2027 under the notified Rules; see
`docs/DPDP-ALIGNMENT.md` for the dated phase-in.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the
full text.

Copyright 2026 Tijori contributors.
