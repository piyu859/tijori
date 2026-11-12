# DPDP alignment — control mapping for Tijori

Status: engineering mapping, **not legal advice**. Tijori is one technical
control inside a Data Fiduciary's compliance programme; it does not make an
organisation "DPDP compliant" by itself.

Verified against the Gazette text and the commencement notification in
November 2026. See [Sources](#sources).

## Sources

| # | Document | Identifier | Used for |
|---|---|---|---|
| 1 | Digital Personal Data Protection Rules, 2025 | G.S.R. 846(E), Gazette of India No. 760, 13 Nov 2025 | Rule text quoted below (r. 1, 6, 7, 8, Third Schedule, 14) |
| 2 | DPDP Act commencement notification | G.S.R. 843(E), 13 Nov 2025 | Three-stage commencement of the Act |
| 3 | Microsoft Presidio — supported entities | `microsoft/presidio/docs/supported_entities.md` | Entity taxonomy and coverage (see `ENTITY-COVERAGE.md`) |
| 4 | MeitY DPDP Rules library page | `meity.gov.in` → Act & Policies → DPDP Rules 2025 | Official landing page for the Rules |
| 5 | PIB release, "DPDP Rules, 2025 Notified" | 17 Nov 2025 | Plain-language confirmation of the notification |

Official files:

- Rules (byte-identical copies):
  <https://egazette.gov.in/WriteReadData/2025/267650.pdf> ·
  <https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf>
- Commencement notification G.S.R. 843(E):
  <https://egazette.gov.in/WriteReadData/2025/267647.pdf>
- Presidio entities:
  <https://github.com/microsoft/presidio/blob/main/docs/supported_entities.md>

## What is in force, and when

Both instruments count from **13 November 2025**, the date of publication in the
Official Gazette.

| Date | Rules 2025 (r. 1) | Act 2023 (G.S.R. 843(E)) |
|---|---|---|
| **13 Nov 2025** | Rules 1, 2, 17–21 | s. 1(2), s. 2, ss. 18–26, 35, 38–43, s. 44(1), 44(3) |
| **13 Nov 2026** | Rule 4 (Consent Manager registration) | s. 6(9), s. 27(1)(d) |
| **13 May 2027** | Rules 3, 5–16, 22, 23 | ss. 3–5, s. 6(1)–(8) and (10), ss. 7–17, s. 27 except (1)(d), ss. 28–34, 36, 37, s. 44(2) |

The 13 Nov 2026 and 13 May 2027 dates are the `+1 year` and `+18 months` periods
printed in r. 1(3) and r. 1(4); the calendar dates are computed, not printed.
Rule 6 (security safeguards) and Rule 7 (breach intimation) therefore bind from
**13 May 2027** under the Rules as notified. Design against them now: the
hackathon brief asks for a DPDP-ready gateway, not a currently-binding control.

## Why Rule 6 is the anchor for this project

Rule 6(1)(a) requires, at minimum, "appropriate data security measures, such as
securing of personal data through encryption, obfuscation, **masking or the use
of virtual tokens mapped to that personal data**".

That is the Tijori design: typed placeholders (`<AADHAAR_1>`) are virtual tokens,
the mapping table is the local vault, and the external LLM only ever receives
tokenised text. Rule 6(1)(c) and 6(1)(e) additionally require logs, monitoring
and one-year retention — which is why the audit table exists.

## Obligation → control → evidence

| Provision | Requirement (abridged) | Tijori control | Evidence | Status |
|---|---|---|---|---|
| Rule 6(1)(a) | Mask personal data or use virtual tokens mapped to it | Detect → mask with typed placeholders; originals only in a per-request vault | `privacy/masking/__init__.py`, `privacy/vault.py`; `pytest tests/privacy -q`; leakage 0 in `reports/metrics.md` | **Implemented** |
| Rule 6(1)(b) | Control access to computer resources | Optional `X-API-Key` gate on every `/v1/*` route; unset by default for local development | `app/main.py`; `python -m pytest tests/platform -q`; `curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/v1/audit` (401 with `TIJORI_API_KEY` set); `curl -s -o /dev/null -w '%{http_code}\n' -H 'X-API-Key: secret' localhost:8000/v1/audit` (200) | **Implemented** — configure a key before exposing the service beyond localhost |
| Rule 6(1)(c) | Logs and monitoring to detect unauthorised access | One PII-free audit row per request; structured warnings on leakage and error records/provider failures | `app/audit/repository.py`, `app/api/routes.py`; `python -m pytest tests/platform -q` | **Partial** — audit and warning signals exist; no anomaly detection |
| Rule 6(1)(d) | Backups / continued processing | SQLite file can be copied; no automated backup | — | **Out of scope** (deployment concern) |
| Rule 6(1)(e) | Retain logs and data one year for investigation | 365-day default retention window and explicit purge CLI; startup reports eligible rows without deleting | `app/config.py`, `app/audit/repository.py`, `app/audit/retention.py`; `python -m pytest tests/platform -q`; `python -m app.audit.retention --days 365 --dry-run` | **Implemented** — run the CLI as an operator action; startup never deletes rows |
| Rule 6(1)(f) | Contractual safeguards with a Data Processor | The external LLM is a processor that receives only placeholders | `app/providers/openai_compatible.py`; outbound payload scan | **Partial** — technical minimisation done; contract is organisational |
| Rule 6(1)(g) | Technical and organisational measures | Fail-closed detection, leakage gate before the provider call, tests, this mapping | `privacy/detection/__init__.py` (`DetectionError`), `app/pipeline/pipeline.py` (`OutboundLeakageError`) | **Partial** — no formal ISMS or review process |
| Rule 7(1)–(2) | Notify Data Principals and the Board of a breach; 72-hour detailed report | PII-free audit trail with stable request ids supports forensics; no notification workflow | `GET /v1/audit/{request_id}` | **Out of scope** (organisational workflow) |
| Rule 8 + Third Schedule | Erase data after inactivity periods; retain processing logs at least one year | Gateway stores no originals, so it holds no personal data to erase; PII-free audit rows have a 365-day default and explicit purge CLI | `app/audit/repository.py`, `app/audit/retention.py`; `python -m pytest tests/platform -q`; `python -m app.audit.retention --days 365 --dry-run` | **Implemented** — deletion requires an explicit operator action |
| Rule 9 | Publish contact details for processing questions | — | — | **Out of scope** |
| Rule 3 / Rule 4 | Notice content, Consent Manager | `purpose` is recorded on each request but not enforced against a consent record | `ProcessRequest.purpose`, audit column | **Out of scope** — purpose policies were explicitly deferred |
| Rule 14 | Publish means to exercise Data Principal rights | Request ids allow locating a processing event; no rights workflow | `GET /v1/audit/{request_id}` | **Out of scope** |
| Rule 15 | Transfers outside India | Originals never leave the host; only tokens and non-PII metadata go to the configured LLM endpoint | pipeline stage 3; outbound payload evidence | **Implemented (technical)** — subject to the endpoint actually being used |

## What the audit row contains

`audit_requests` stores: `request_id`, `created_at`, `doc_type`, `purpose`,
`task`, `entity_counts`, `outbound_payload` (sanitized), `provider`, `model`,
`latency_ms`, `status`, `error`, reinsertion counters, `leakage_count`.

It does **not** store: original document text, the placeholder→original mapping,
raw LLM completions, or `final_text` (which contains originals). That is
deliberate: the audit trail must be safe to retain under Rule 6(1)(e).

`doc_type`, `purpose` and `entity_counts` are metadata, not personal data by
themselves; treat them as confidential business records.

Multilingual intake (OCR and NLLB translation) runs entirely on-device, so it
introduces no new data processor, and extracted or translated text is held in
memory only and never persisted.

## Verification commands

```bash
pytest -q                                   # 83 tests
python -m evaluation.run                    # leakage must be 0
curl -s localhost:8000/v1/process -H 'content-type: application/json' \
  -d '{"doc_type":"kyc","purpose":"verify","task":"extract","text":"Aadhaar 2234-5678-9012"}' | jq
sqlite3 data/audit.db "select outbound_payload from audit_requests order by created_at desc limit 1;" \
  | grep -E '2234|Rahul' && echo LEAK || echo CLEAN
```

## Open gaps, in priority order

1. **Monitoring and alerting (Rule 6(1)(c)).** Add anomaly detection and
  operational alert delivery for `status=error` or `leakage_count>0`. The
  second should be impossible; alert if it ever happens.
2. **Breach playbook (Rule 7).** Document who is notified and how, using
   `request_id` and the audit row as the forensic unit.
3. **Purpose enforcement (Rule 3 / consent).** `purpose` is currently recorded,
   not validated. Purpose policies were out of scope for the first 6 hours.

## Claims we must not make

- **"DPDP compliant."** Tijori implements a subset of technical safeguards
  (Rule 6(1)(a), access gating under 6(1)(b), and log retention under
  6(1)(e)/Rule 8), plus partial support for 6(1)(c) and 15. Compliance
  is the Data Fiduciary's obligation and includes notice, consent, rights,
  retention and breach processes that this repo does not provide.
- **"No data leaves the country."** True for originals. Sanitized text, the
  instruction and metadata are sent to whatever `TIJORI_LLM_BASE_URL` points at;
  point it at a local or India-hosted endpoint if that matters.
- **"All PII is detected."** Detection is pattern + NER with measured recall
  0.957 macro on the synthetic dataset; novel formats can be missed. The
  fail-closed behaviour protects against detector failure, not against an
  unknown identifier format.
