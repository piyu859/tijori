"""Tijori demo UI (lane 3).

Talks only to the frozen HTTP contract in CONTRACT.md section 4:

    GET  /health
    POST /v1/process
    GET  /v1/audit?limit=20

Run with ``streamlit run ui/app.py``. The API base URL defaults to
``TIJORI_API_URL`` or http://127.0.0.1:8000.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import httpx
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest.extract import ExtractionError, extract  # noqa: E402
from ingest.language import detect_language  # noqa: E402
from ingest.translate import TranslationUnavailable, translate_to_english  # noqa: E402

DATASET_PATH = ROOT / "data" / "dataset.jsonl"
DEFAULT_API_URL = os.getenv("TIJORI_API_URL", "http://127.0.0.1:8000")
DOC_TYPES = ("kyc", "loan_application", "support_ticket", "insurance_claim", "other")
UPLOAD_TYPES = ["txt", "md", "csv", "docx", "pdf", "png", "jpg", "jpeg", "tif", "tiff"]
TRANSLATION_BLOCKED = (
    "Translation unavailable — refusing to send un-translated native-script text "
    "to the gateway."
)
#: Same wording the evaluation harness uses, so the demo matches `reports/metrics.md`.
#: "Do not copy the document" made qwen2.5:3b drop 9 of 10 placeholders on loan_001
#: (the model abstracts identifiers away instead of carrying the tokens through).
DEFAULT_INSTRUCTION = "Summarize in 3 bullet points and keep all placeholders."
AUTH_ERROR = "Gateway rejected the API key (check TIJORI_API_KEY)"

FALLBACK_SAMPLE = (
    "My name is Rahul Sharma. My Aadhaar number is 1234-5678-9012 "
    "and my PAN is ABCDE1234F. Mobile: +91 98765 43210."
)


@st.cache_data(show_spinner=False)
def load_samples() -> list[dict]:
    try:
        lines = DATASET_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def probe(api_url: str) -> tuple[bool, dict]:
    try:
        response = httpx.get(f"{api_url}/health", timeout=1.0)
        payload = response.json()
        if response.status_code == 200 and isinstance(payload, dict):
            return True, payload
    except Exception:  # noqa: BLE001 - any network error means offline
        pass
    return False, {}


def auth_headers() -> dict[str, str]:
    """X-API-Key only when TIJORI_API_KEY is set; never expose the key elsewhere."""
    key = os.getenv("TIJORI_API_KEY", "").strip()
    return {"X-API-Key": key} if key else {}


def short(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def ingest_upload(filename: str, data: bytes) -> dict:
    """Extract, detect and translate one upload entirely on-device."""

    extraction = extract(filename, data)
    detected = detect_language(extraction.text)
    translation = translate_to_english(extraction.text, detected)
    return {
        "extraction": extraction,
        "detected": detected,
        "translation": translation,
    }


def upload_identity(uploaded) -> str:
    return f"{uploaded.file_id}:{uploaded.name}:{uploaded.size}"


def language_badge(meta: dict | None) -> str:
    if not meta:
        return ""
    if meta.get("language_code") == "en":
        return "English — no translation needed (on-device)"
    return f"{meta.get('language', 'Unknown')} → English, on-device"


def leakage_findings(text: str, entities: list[dict], outbound: str) -> list[dict]:
    """Raw originals that survived into the outbound payload."""
    findings: list[dict] = []
    lowered = outbound.casefold()
    for entity in entities:
        try:
            start, end = int(entity.get("start", 0)), int(entity.get("end", 0))
        except (TypeError, ValueError):
            continue
        if start < 0 or end <= start or start >= len(text):
            continue
        original = text[start:end]
        if not original:
            continue
        variants = {original}
        if entity.get("type") == "AADHAAR":
            variants.add(re.sub(r"[\s-]", "", original))
        for variant in variants:
            if not variant:
                continue
            hit = variant in outbound if variant.isdigit() else variant.casefold() in lowered
            if hit:
                findings.append({"type": entity.get("type"), "value": variant})
    return findings


def audit_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                return value
    return []


def clear_result() -> None:
    """A stale result must never be shown against edited text."""
    st.session_state.pop("result", None)
    st.session_state.pop("error", None)


def show_upload_panel() -> None:
    """Badge, native original, extraction metadata and translation warnings."""

    meta = st.session_state.get("upload_meta")
    if not meta:
        return
    st.caption(language_badge(meta))
    with st.expander("Native original and extraction details"):
        st.code(st.session_state.get("upload_original") or "(empty)", language="text")
        ocr = "yes" if meta.get("ocr_used") else "no"
        langs = f" ({', '.join(meta.get('ocr_langs') or [])})" if meta.get("ocr_langs") else ""
        st.caption(
            f"File: {meta.get('filename')} · pages: {meta.get('pages')} · "
            f"OCR: {ocr}{langs} · language: {meta.get('language')} "
            f"({meta.get('language_code')})"
        )
        st.caption(
            f"Translation: {meta.get('translation_model')} · "
            f"{meta.get('translation_ms')} ms"
        )
    for warning in st.session_state.get("upload_warnings") or []:
        st.warning(warning)


def show_result(result: dict, submitted_text: str, instruction: str) -> None:
    entities = [e for e in (result.get("entities") or []) if isinstance(e, dict)]
    outbound = result.get("outbound_payload") or ""

    left, middle, right = st.columns(3)

    with left:
        st.subheader("A. Original input")
        st.text_area(
            "Original document",
            key="original_text",
            height=360,
            label_visibility="collapsed",
            on_change=clear_result,
        )
        show_upload_panel()
        st.caption(
            f"{len(submitted_text)} characters · doc type: "
            f"{st.session_state.get('result_doc_type', '—')} · task: "
            f"{st.session_state.get('result_task', '—')}"
        )
        st.caption(f"Instruction sent: {short(instruction) if instruction else '—'}")

    with middle:
        st.subheader("B. Outbound sanitized payload")
        st.code(outbound or "(empty payload)", language="text")
        if entities:
            st.dataframe(
                [
                    {
                        "Type": e.get("type"),
                        "Placeholder": e.get("placeholder"),
                        "Span": f"{e.get('start')}:{e.get('end')}",
                    }
                    for e in entities
                ],
                hide_index=True,
                use_container_width=True,
            )
            counts = Counter(e.get("placeholder") for e in entities)
            st.caption(
                "Placeholder counts: "
                + ", ".join(f"{placeholder} ×{count}" for placeholder, count in counts.items())
            )
        else:
            st.info("No PII detected in this document.")

        findings = leakage_findings(submitted_text, entities, outbound)
        if findings:
            detail = ", ".join(f"{f['type']}={f['value']}" for f in findings[:5])
            st.error(f"Leakage: {len(findings)} raw value(s) found in outbound payload — {detail}")
        else:
            st.success("No raw PII in outbound payload")

    with right:
        st.subheader("C. Final response")
        st.text_area(
            "Final text",
            value=result.get("final_text") or "",
            height=360,
            disabled=True,
            label_visibility="collapsed",
        )
        reinsertion = result.get("reinsertion")
        if not isinstance(reinsertion, dict):
            reinsertion = {}
        stats = st.columns(3)
        stats[0].metric("Replaced", reinsertion.get("replaced", 0))
        stats[1].metric("Missing", len(reinsertion.get("missing") or []))
        stats[2].metric("Unknown", len(reinsertion.get("unknown") or []))
        st.caption(
            f"Provider: {result.get('provider', '—')} · model: {result.get('model', '—')} · "
            f"gateway latency: {result.get('latency_ms', 0)} ms"
        )
        with st.expander("Reinsertion detail"):
            st.json(reinsertion)


st.set_page_config(page_title="Tijori — DPDP Privacy Gateway", layout="wide")
st.title("Tijori — DPDP Privacy Gateway")
st.caption("Detect the sensitive. Mask the personal. Process securely. Restore locally.")

samples = load_samples()
sample_by_id = {doc["id"]: doc for doc in samples}

if "original_text" not in st.session_state:
    st.session_state.original_text = samples[0]["text"] if samples else FALLBACK_SAMPLE
if "doc_type" not in st.session_state:
    st.session_state.doc_type = samples[0]["doc_type"] if samples else "kyc"
if "task" not in st.session_state:
    st.session_state.task = "summarize"
if "instruction" not in st.session_state:
    st.session_state.instruction = DEFAULT_INSTRUCTION

# Upload/translation state lives in memory only. Nothing here is written to
# disk or to the audit table.
if "source_lang" not in st.session_state:
    st.session_state.source_lang = "en"
if "translation_state" not in st.session_state:
    st.session_state.translation_state = "ok"
if "upload_id" not in st.session_state:
    st.session_state.upload_id = None
if "upload_dismissed" not in st.session_state:
    st.session_state.upload_dismissed = None
if "upload_original" not in st.session_state:
    st.session_state.upload_original = ""
if "upload_meta" not in st.session_state:
    st.session_state.upload_meta = None
if "upload_warnings" not in st.session_state:
    st.session_state.upload_warnings = []
if "upload_error" not in st.session_state:
    st.session_state.upload_error = ""

with st.sidebar:
    st.subheader("Gateway")
    api_url = st.text_input("API URL", value=DEFAULT_API_URL, key="api_url").rstrip("/")
    online, health = probe(api_url)
    if online:
        st.success(f"API online — {health.get('provider', '?')} / {health.get('model', '?')}")
    else:
        st.error(f"API offline at {api_url}")

    st.subheader("Upload")
    uploaded = st.file_uploader(
        "Upload document",
        type=UPLOAD_TYPES,
        key="document_upload",
        help=(
            "Text, Markdown, CSV, DOCX, PDF or a scanned image. Extraction and "
            "translation run on this machine; the English text is placed in the editor."
        ),
    )
    if uploaded is not None:
        identity = upload_identity(uploaded)
        if identity != st.session_state.get("upload_id") and identity != st.session_state.get(
            "upload_dismissed"
        ):
            st.session_state.upload_id = identity
            st.session_state.translation_state = "pending"
            st.session_state.upload_error = ""
            st.session_state.upload_original = ""
            st.session_state.upload_meta = None
            st.session_state.upload_warnings = []
            st.session_state.source_lang = "en"
            st.session_state.pop("result", None)
            with st.spinner("Extracting and translating on-device…"):
                try:
                    outcome = ingest_upload(uploaded.name, uploaded.getvalue())
                except ExtractionError as exc:
                    st.session_state.translation_state = "failed"
                    st.session_state.upload_error = f"Could not read {uploaded.name}: {exc}"
                except TranslationUnavailable as exc:
                    st.session_state.translation_state = "failed"
                    st.session_state.upload_error = f"{TRANSLATION_BLOCKED} {exc}"
                except Exception as exc:  # noqa: BLE001 - never crash the demo
                    st.session_state.translation_state = "failed"
                    st.session_state.upload_error = (
                        f"Could not process {uploaded.name}: {exc}"
                    )
                else:
                    extraction = outcome["extraction"]
                    detected = outcome["detected"]
                    translation = outcome["translation"]
                    st.session_state.original_text = translation.text
                    st.session_state.source_lang = detected.code
                    st.session_state.upload_original = extraction.text
                    st.session_state.upload_meta = {
                        "filename": extraction.filename,
                        "pages": extraction.pages,
                        "ocr_used": extraction.ocr_used,
                        "ocr_langs": extraction.ocr_langs,
                        "language": detected.script,
                        "language_code": detected.code,
                        "translation_model": translation.model,
                        "translation_ms": translation.duration_ms,
                    }
                    st.session_state.upload_warnings = list(extraction.warnings) + list(
                        translation.warnings
                    )
                    st.session_state.translation_state = "ok"
                    st.session_state.pop("error", None)
    if st.session_state.get("upload_error"):
        st.error(st.session_state.upload_error)

    st.subheader("Document")
    if sample_by_id:
        sample_id = st.selectbox("Sample document", sorted(sample_by_id))
        if st.button("Load sample", use_container_width=True):
            sample = sample_by_id[sample_id]
            st.session_state.original_text = sample["text"]
            st.session_state.doc_type = sample["doc_type"]
            # Ignore the file still held by the uploader so it cannot overwrite
            # the sample on the next rerun.
            st.session_state.upload_dismissed = (
                upload_identity(uploaded) if uploaded is not None else None
            )
            st.session_state.upload_id = None
            st.session_state.upload_original = ""
            st.session_state.upload_meta = None
            st.session_state.upload_warnings = []
            st.session_state.upload_error = ""
            st.session_state.translation_state = "ok"
            st.session_state.source_lang = "en"
            st.session_state.pop("result", None)
    else:
        st.caption("data/dataset.jsonl not found — using the built-in sample.")

    st.selectbox("Document type", DOC_TYPES, key="doc_type")
    st.selectbox("Task", ("summarize", "extract"), key="task")
    st.text_area(
        "Instruction",
        key="instruction",
        height=120,
        help="Optional. Sent as instruction on POST /v1/process.",
        on_change=clear_result,
    )

    st.divider()
    translation_blocked = st.session_state.get("translation_state") in {"pending", "failed"}
    run = st.button(
        "Run gateway",
        type="primary",
        use_container_width=True,
        disabled=not online or translation_blocked,
    )
    if translation_blocked:
        st.caption("Blocked until on-device translation succeeds.")
    else:
        st.caption("POST /v1/process on the sanitized document.")

if run:
    submitted = (st.session_state.get("original_text") or "").strip()
    instruction = (st.session_state.get("instruction") or "").strip()
    st.session_state.pop("error", None)
    if st.session_state.get("translation_state") == "failed":
        st.session_state.error = TRANSLATION_BLOCKED
    elif not submitted:
        st.session_state.error = "Enter a document before running."
    else:
        request = {
            "text": st.session_state.original_text,
            "doc_type": st.session_state.doc_type,
            "task": st.session_state.task,
            "purpose": "general",
            "source_lang": st.session_state.get("source_lang", "en"),
        }
        if instruction:
            request["instruction"] = instruction
        with st.spinner("Gateway is sanitizing and calling the LLM…"):
            try:
                response = httpx.post(
                    f"{api_url}/v1/process",
                    json=request,
                    headers=auth_headers(),
                    timeout=180,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("gateway returned a non-object JSON response")
                st.session_state.result = payload
                st.session_state.result_text = request["text"]
                st.session_state.result_doc_type = request["doc_type"]
                st.session_state.result_task = request["task"]
                st.session_state.result_instruction = instruction
            except httpx.HTTPStatusError as exc:
                st.session_state.pop("result", None)
                if exc.response.status_code == 401:
                    st.session_state.error = AUTH_ERROR
                else:
                    st.session_state.error = (
                        f"Gateway returned {exc.response.status_code}: {exc.response.text[:500]}"
                    )
            except Exception as exc:  # noqa: BLE001 - never crash the demo
                st.session_state.pop("result", None)
                st.session_state.error = f"Could not reach the gateway at {api_url}: {exc}"

if st.session_state.get("error"):
    st.error(st.session_state.error)

demo_tab, audit_tab = st.tabs(["Gateway demo", "Audit browser"])

with demo_tab:
    result = st.session_state.get("result")
    if result:
        show_result(
            result,
            st.session_state.get("result_text", ""),
            st.session_state.get("result_instruction", ""),
        )
    else:
        left, middle, right = st.columns(3)
        with left:
            st.subheader("A. Original input")
            st.text_area(
                "Original document",
                key="original_text",
                height=360,
                label_visibility="collapsed",
                on_change=clear_result,
            )
            show_upload_panel()
        with middle:
            st.subheader("B. Outbound sanitized payload")
            st.code("Not processed yet — click Run gateway.", language="text")
            if not online:
                st.warning("API offline. Start uvicorn app.main:app, then reload this page.")
        with right:
            st.subheader("C. Final response")
            st.info("The reinserted response will appear here.")

with audit_tab:
    st.subheader("Recent audit records")
    if not online:
        st.info("API offline — audit history is unavailable.")
    else:
        try:
            response = httpx.get(
                f"{api_url}/v1/audit",
                params={"limit": 20},
                headers=auth_headers(),
                timeout=10,
            )
            if response.status_code == 401:
                st.info(AUTH_ERROR)
            else:
                response.raise_for_status()
                rows = audit_rows(response.json())
                rows = [row for row in rows if isinstance(row, dict)]
                if rows:
                    st.dataframe(
                        [
                            {
                                "time": row.get("created_at"),
                                "doc_type": row.get("doc_type"),
                                "language": row.get("source_lang", "en"),
                                "entities": ", ".join(
                                    f"{key}:{value}"
                                    for key, value in (row.get("entity_counts") or {}).items()
                                ),
                                "provider/model": f"{row.get('provider', '')}/{row.get('model', '')}",
                                "latency_ms": row.get("latency_ms"),
                                "leakage": row.get("leakage_count", 0),
                                "status": row.get("status"),
                            }
                            for row in rows
                        ],
                        hide_index=True,
                        use_container_width=True,
                    )
                else:
                    st.info("No audit records yet.")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                st.info(AUTH_ERROR)
            else:
                st.info(f"Audit browser unavailable: {exc}")
        except Exception as exc:  # noqa: BLE001
            st.info(f"Audit browser unavailable: {exc}")

st.divider()
st.caption("Raw PII never leaves the gateway. Placeholders are restored locally.")
