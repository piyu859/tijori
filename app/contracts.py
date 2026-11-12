"""Frozen shared types for the Tijori privacy gateway.

Lanes import from this module; they do not redefine these types.
See CONTRACT.md for the behavioural rules that go with them.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Entity taxonomy
# ---------------------------------------------------------------------------


class EntityType(StrEnum):
    AADHAAR = "AADHAAR"
    PAN = "PAN"
    GSTIN = "GSTIN"
    VOTER_ID = "VOTER_ID"
    PASSPORT = "PASSPORT"
    PERSON = "PERSON"
    ADDRESS = "ADDRESS"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    DOB = "DOB"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    CREDIT_CARD = "CREDIT_CARD"
    OTHER = "OTHER"


MANDATORY_ENTITIES: tuple[EntityType, ...] = (
    EntityType.AADHAAR,
    EntityType.PAN,
    EntityType.GSTIN,
    EntityType.VOTER_ID,
    EntityType.PASSPORT,
    EntityType.PERSON,
    EntityType.ADDRESS,
)

#: Higher index wins when two detections overlap.
ENTITY_PRIORITY: tuple[EntityType, ...] = (
    EntityType.AADHAAR,
    EntityType.PAN,
    EntityType.GSTIN,
    EntityType.PASSPORT,
    EntityType.VOTER_ID,
    EntityType.CREDIT_CARD,
    EntityType.BANK_ACCOUNT,
    EntityType.EMAIL,
    EntityType.PHONE,
    EntityType.DOB,
    EntityType.PERSON,
    EntityType.ADDRESS,
    EntityType.OTHER,
)

PLACEHOLDER_PATTERN = (
    r"^<(AADHAAR|PAN|GSTIN|VOTER_ID|PASSPORT|PERSON|ADDRESS|PHONE|EMAIL|DOB|"
    r"BANK_ACCOUNT|CREDIT_CARD|OTHER)_([1-9][0-9]*)>$"
)
PLACEHOLDER_RE = re.compile(PLACEHOLDER_PATTERN)
#: Tolerant scanner for tokens already present in LLM output (case-insensitive).
PLACEHOLDER_SCAN_RE = re.compile(
    r"<(AADHAAR|PAN|GSTIN|VOTER_ID|PASSPORT|PERSON|ADDRESS|PHONE|EMAIL|DOB|"
    r"BANK_ACCOUNT|CREDIT_CARD|OTHER)_([1-9][0-9]*)>",
    re.IGNORECASE,
)

EntityTypeLiteral = Literal[
    "AADHAAR",
    "PAN",
    "GSTIN",
    "VOTER_ID",
    "PASSPORT",
    "PERSON",
    "ADDRESS",
    "PHONE",
    "EMAIL",
    "DOB",
    "BANK_ACCOUNT",
    "CREDIT_CARD",
    "OTHER",
]


def make_placeholder(entity_type: EntityType | str, counter: int) -> str:
    """Return the canonical placeholder for a type and 1-based counter."""
    if counter < 1:
        raise ValueError("placeholder counter starts at 1")
    return f"<{EntityType(entity_type).value}_{counter}>"


# ---------------------------------------------------------------------------
# Detection / masking / reinsertion
# ---------------------------------------------------------------------------


class DetectedEntity(BaseModel):
    type: EntityType
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str
    score: float = Field(ge=0.0, le=1.0, default=1.0)
    source: str = "unknown"

    @model_validator(mode="after")
    def _span_is_ordered(self) -> "DetectedEntity":
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class DetectionResult(BaseModel):
    text: str
    entities: list[DetectedEntity] = Field(default_factory=list)
    language: str = "auto"
    duration_ms: int = 0


class PlaceholderEntry(BaseModel):
    placeholder: str
    type: EntityType
    original: str
    occurrences: int = 1

    @model_validator(mode="after")
    def _placeholder_matches_type(self) -> "PlaceholderEntry":
        match = PLACEHOLDER_RE.match(self.placeholder)
        if match is None:
            raise ValueError(f"invalid placeholder: {self.placeholder!r}")
        if match.group(1) != self.type.value:
            raise ValueError("placeholder type does not match entity type")
        return self


class MaskingResult(BaseModel):
    sanitized_text: str
    mapping: list[PlaceholderEntry] = Field(default_factory=list)
    entities: list[DetectedEntity] = Field(default_factory=list)
    duration_ms: int = 0


class ReinsertionResult(BaseModel):
    text: str
    replaced: int = 0
    missing: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

Task = Literal["summarize", "extract"]


class SanitizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    doc_type: str = "other"
    purpose: str = "general"
    #: Language of the original document. The gateway always receives English
    #: text; this is audit metadata, not a request for native-language output.
    source_lang: str = "en"


class MaskedEntityView(BaseModel):
    """What the API may expose about an entity: never the original value."""

    type: EntityType
    placeholder: str
    start: int
    end: int


class SanitizeResponse(BaseModel):
    request_id: str
    sanitized_text: str
    entities: list[MaskedEntityView] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    duration_ms: int = 0


class ProcessRequest(SanitizeRequest):
    task: Task = "summarize"
    instruction: str | None = None
    max_tokens: int = Field(default=512, ge=16, le=4096)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)


class ReinsertionStats(BaseModel):
    replaced: int = 0
    missing: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)


class ProcessResponse(BaseModel):
    request_id: str
    final_text: str
    sanitized_text: str
    outbound_payload: str
    entities: list[MaskedEntityView] = Field(default_factory=list)
    provider: str
    model: str
    task: Task
    latency_ms: int = 0
    reinsertion: ReinsertionStats = Field(default_factory=ReinsertionStats)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditRecord(BaseModel):
    """Stored in SQLite. Never contains original PII values."""

    request_id: str
    created_at: str
    doc_type: str = "other"
    purpose: str = "general"
    source_lang: str = "en"
    task: str = "sanitize"
    entity_counts: dict[str, int] = Field(default_factory=dict)
    outbound_payload: str = ""
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    status: str = "ok"
    error: str | None = None
    reinsertion_replaced: int = 0
    reinsertion_missing: list[str] = Field(default_factory=list)
    reinsertion_unknown: list[str] = Field(default_factory=list)
    leakage_count: int = 0


# ---------------------------------------------------------------------------
# LLM provider
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str
    latency_ms: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
