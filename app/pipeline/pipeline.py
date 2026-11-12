"""Four-stage privacy gateway orchestration (lane 2).

detect -> mask -> forward sanitized text -> reinsert locally.

The placeholder mapping exists only in this call frame. Audit rows receive the
sanitized payload and counts, never originals or mappings (CONTRACT.md §1, §5).
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from privacy import detection, masking, reinsertion
from privacy.vault import normalize_value

from app.audit.repository import AuditRepository
from app.config import settings
from app.contracts import (
    AuditRecord,
    ChatMessage,
    EntityType,
    LLMResponse,
    MaskedEntityView,
    ProcessRequest,
    ProcessResponse,
    ReinsertionStats,
    SanitizeRequest,
    SanitizeResponse,
)
from app.providers import LLMProvider, get_provider

SYSTEM_PROMPT = (
    "Preserve every placeholder like <PERSON_1> exactly. "
    "Never alter, guess, or invent placeholders."
)

_DEFAULT_INSTRUCTIONS = {
    "summarize": "Summarize the following document.",
    "extract": "Extract the key details from the following document.",
}
_AADHAAR_SEPARATORS = re.compile(r"[\s-]+")
#: Internal boundary for masking instruction + document in one vault. Never sent.
_INSTRUCTION_BOUNDARY = "\x1eTIJORI_BOUNDARY\x1e"


class PipelineError(Exception):
    """Base class for gateway pipeline failures."""


class PipelineFailure(PipelineError):
    """A local stage failed. API maps this to a JSON HTTP 500."""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        super().__init__("gateway request failed")


class ProviderFailure(PipelineError):
    """The configured LLM backend failed. API maps this to HTTP 502."""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        super().__init__("LLM provider request failed")


class OutboundLeakageError(PipelineError):
    """A detected original survived masking. Fail closed: nothing is sent."""

    def __init__(self, request_id: str, leakage_count: int) -> None:
        self.request_id = request_id
        self.leakage_count = leakage_count
        super().__init__("outbound payload contained unmasked PII")


def new_request_id() -> str:
    return "req_" + uuid4().hex


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _entity_views(
    entities: list,
    mapping: list,
) -> tuple[list[MaskedEntityView], dict[str, int]]:
    placeholder_by_key: dict[tuple[EntityType, str], str] = {}
    for entry in mapping:
        key = (entry.type, normalize_value(entry.type, entry.original))
        placeholder_by_key.setdefault(key, entry.placeholder)

    views: list[MaskedEntityView] = []
    for entity in entities:
        key = (entity.type, normalize_value(entity.type, entity.text))
        placeholder = placeholder_by_key.get(key)
        if placeholder is None:
            raise ValueError("masking result is missing a placeholder for a detected entity")
        views.append(
            MaskedEntityView(
                type=entity.type,
                placeholder=placeholder,
                start=entity.start,
                end=entity.end,
            )
        )

    counts: Counter[str] = Counter()
    for entry in mapping:
        counts[entry.type.value] += entry.occurrences
    if not counts:
        for view in views:
            counts[view.type.value] += 1
    return views, dict(counts)


def _build_messages(
    task: str,
    sanitized_instruction: str,
    sanitized_text: str,
) -> list[ChatMessage]:
    instruction = sanitized_instruction or _DEFAULT_INSTRUCTIONS[task]
    return [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=f"{instruction}\n\n{sanitized_text}"),
    ]


def _mask_process_input(req: ProcessRequest):
    """Mask document and instruction together so numbering stays request-wide.

    The instruction is part of the outbound prompt, so it must pass through the
    same detector/masker. The document comes first so its placeholders keep the
    layout callers expect; the instruction reuses a document placeholder when
    the value and type match. A sentinel keeps the regions separable without
    splitting on text that may itself contain blank lines.
    """

    instruction = (req.instruction or "").strip()
    if not instruction:
        result = detection.detect(req.text)
        masked = masking.mask(req.text, result.entities)
        return "", masked.sanitized_text, result.entities, masked

    if _INSTRUCTION_BOUNDARY in req.text or _INSTRUCTION_BOUNDARY in instruction:
        raise ValueError("reserved instruction boundary in input")

    combined = req.text + _INSTRUCTION_BOUNDARY + instruction
    result = detection.detect(combined)
    masked = masking.mask(combined, result.entities)
    sanitized_text, found, sanitized_instruction = masked.sanitized_text.partition(
        _INSTRUCTION_BOUNDARY
    )
    if not found:
        raise ValueError("instruction boundary was lost during masking")

    body_entities = [
        entity for entity in result.entities if entity.end <= len(req.text)
    ]
    return sanitized_instruction, sanitized_text, body_entities, masked


def serialize_messages(messages: list[ChatMessage]) -> str:
    """Deterministic JSON of the exact message list handed to the provider."""

    return json.dumps(
        [message.model_dump() for message in messages],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def count_leakage(text: str, mapping: list) -> int:
    """Count raw originals in prompt text, tolerating Aadhaar separators.

    Call this on unescaped message content, never on JSON-serialized text:
    ``json.dumps`` escapes quotes/newlines and would hide a multi-line address.
    """

    compact_text = _AADHAAR_SEPARATORS.sub("", text)
    total = 0
    for entry in mapping:
        if entry.type is EntityType.AADHAAR:
            compact = _AADHAAR_SEPARATORS.sub("", entry.original)
            if compact:
                total += compact_text.count(compact)
        else:
            total += text.count(entry.original)
    return total


def _redact(text: str, mapping: list) -> str:
    for entry in mapping:
        text = re.sub(
            re.escape(entry.original),
            "[REDACTED]",
            text,
            flags=re.IGNORECASE,
        )
        if entry.type is EntityType.AADHAAR:
            compact = _AADHAAR_SEPARATORS.sub("", entry.original)
            if compact:
                pattern = r"(?<!\d)" + r"[\s-]*".join(compact) + r"(?!\d)"
                text = re.sub(pattern, "[REDACTED]", text)
    return text[:500]


class PrivacyPipeline:
    """Owns one request's detect/mask/forward/reinsert flow."""

    def __init__(
        self,
        *,
        provider: LLMProvider | None = None,
        repository: AuditRepository | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository

    @property
    def provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = get_provider()
        return self._provider

    @property
    def repository(self) -> AuditRepository:
        if self._repository is None:
            self._repository = AuditRepository()
        return self._repository

    def sanitize(self, req: SanitizeRequest) -> SanitizeResponse:
        started = time.perf_counter()
        request_id = new_request_id()
        try:
            result = detection.detect(req.text)
            masked = masking.mask(req.text, result.entities)
            views, counts = _entity_views(result.entities, masked.mapping)
            duration_ms = _elapsed_ms(started)
            response = SanitizeResponse(
                request_id=request_id,
                sanitized_text=masked.sanitized_text,
                entities=views,
                counts=counts,
                duration_ms=duration_ms,
            )
            self.repository.insert(
                AuditRecord(
                    request_id=request_id,
                    created_at=_utc_now(),
                    doc_type=req.doc_type,
                    purpose=req.purpose,
                    source_lang=req.source_lang,
                    task="sanitize",
                    entity_counts=counts,
                    outbound_payload="",
                    provider="",
                    model="",
                    latency_ms=duration_ms,
                    status="ok",
                )
            )
            return response
        except Exception as exc:
            self._audit_failure(
                request_id=request_id,
                req=req,
                task="sanitize",
                entity_counts={},
                outbound_payload="",
                provider="",
                model="",
                latency_ms=_elapsed_ms(started),
                error=f"{type(exc).__name__}: pipeline failure",
            )
            raise PipelineFailure(request_id) from exc

    async def process(self, req: ProcessRequest) -> ProcessResponse:
        started = time.perf_counter()
        request_id = new_request_id()

        try:
            provider = self.provider
        except Exception as exc:
            self._audit_failure(
                request_id=request_id,
                req=req,
                task=req.task,
                entity_counts={},
                outbound_payload="",
                provider=settings.llm_provider,
                model=settings.llm_model,
                latency_ms=_elapsed_ms(started),
                error=f"{type(exc).__name__}: provider configuration failure",
            )
            raise PipelineFailure(request_id) from exc

        try:
            sanitized_instruction, sanitized_text, body_entities, masked = _mask_process_input(req)
            views, counts = _entity_views(body_entities, masked.mapping)
        except Exception as exc:
            self._audit_failure(
                request_id=request_id,
                req=req,
                task=req.task,
                entity_counts={},
                outbound_payload="",
                provider=provider.name,
                model=provider.model,
                latency_ms=_elapsed_ms(started),
                error=f"{type(exc).__name__}: pipeline failure",
            )
            raise PipelineFailure(request_id) from exc

        messages = _build_messages(req.task, sanitized_instruction, sanitized_text)
        outbound_payload = serialize_messages(messages)
        leakage_count = sum(count_leakage(message.content, masked.mapping) for message in messages)

        if leakage_count > 0:
            self._audit_failure(
                request_id=request_id,
                req=req,
                task=req.task,
                entity_counts=counts,
                # The payload itself is unsafe, so it is never persisted.
                outbound_payload="",
                provider=provider.name,
                model=provider.model,
                latency_ms=_elapsed_ms(started),
                error="blocked: outbound payload contained unmasked PII",
                leakage_count=leakage_count,
            )
            raise OutboundLeakageError(request_id, leakage_count)

        try:
            response: LLMResponse = await provider.complete(
                messages,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            )
        except Exception as exc:
            self._audit_failure(
                request_id=request_id,
                req=req,
                task=req.task,
                entity_counts=counts,
                outbound_payload=outbound_payload,
                provider=provider.name,
                model=provider.model,
                latency_ms=_elapsed_ms(started),
                error=_redact(f"{type(exc).__name__}: {exc}", masked.mapping),
                leakage_count=leakage_count,
            )
            raise ProviderFailure(request_id) from exc

        try:
            restored = reinsertion.reinsert(response.text, masked.mapping)
        except Exception as exc:
            self._audit_failure(
                request_id=request_id,
                req=req,
                task=req.task,
                entity_counts=counts,
                outbound_payload=outbound_payload,
                provider=response.provider,
                model=response.model,
                latency_ms=_elapsed_ms(started),
                error=f"{type(exc).__name__}: reinsertion failure",
                leakage_count=leakage_count,
            )
            raise PipelineFailure(request_id) from exc

        latency_ms = _elapsed_ms(started)
        try:
            self.repository.insert(
                AuditRecord(
                    request_id=request_id,
                    created_at=_utc_now(),
                    doc_type=req.doc_type,
                    purpose=req.purpose,
                    source_lang=req.source_lang,
                    task=req.task,
                    entity_counts=counts,
                    outbound_payload=outbound_payload,
                    provider=response.provider,
                    model=response.model,
                    latency_ms=latency_ms,
                    status="ok",
                    reinsertion_replaced=restored.replaced,
                    reinsertion_missing=restored.missing,
                    reinsertion_unknown=restored.unknown,
                    leakage_count=leakage_count,
                )
            )
        except Exception as exc:
            raise PipelineFailure(request_id) from exc
        return ProcessResponse(
            request_id=request_id,
            final_text=restored.text,
            sanitized_text=sanitized_text,
            outbound_payload=outbound_payload,
            entities=views,
            provider=response.provider,
            model=response.model,
            task=req.task,
            latency_ms=latency_ms,
            reinsertion=ReinsertionStats(
                replaced=restored.replaced,
                missing=restored.missing,
                unknown=restored.unknown,
            ),
        )

    def _audit_failure(
        self,
        *,
        request_id: str,
        req: SanitizeRequest,
        task: str,
        entity_counts: dict[str, int],
        outbound_payload: str,
        provider: str,
        model: str,
        latency_ms: int,
        error: str,
        leakage_count: int = 0,
    ) -> None:
        try:
            self.repository.insert(
                AuditRecord(
                    request_id=request_id,
                    created_at=_utc_now(),
                    doc_type=req.doc_type,
                    purpose=req.purpose,
                    source_lang=req.source_lang,
                    task=task,
                    entity_counts=entity_counts,
                    outbound_payload=outbound_payload,
                    provider=provider,
                    model=model,
                    latency_ms=latency_ms,
                    status="error",
                    error=error,
                    leakage_count=leakage_count,
                )
            )
        except Exception:
            # Preserve the original failure; the route still returns JSON.
            pass
