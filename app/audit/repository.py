"""SQLite audit log (lane 2).

One row per request. The placeholder mapping never reaches this module: only
the sanitized outbound payload and entity counts are stored (CONTRACT.md §5).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.contracts import AuditRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_requests (
    request_id           TEXT PRIMARY KEY,
    created_at           TEXT NOT NULL,
    doc_type             TEXT NOT NULL,
    purpose              TEXT NOT NULL,
    source_lang          TEXT NOT NULL DEFAULT 'en',
    task                 TEXT NOT NULL,
    entity_counts        TEXT NOT NULL,
    outbound_payload     TEXT NOT NULL,
    provider             TEXT NOT NULL,
    model                TEXT NOT NULL,
    latency_ms           INTEGER NOT NULL,
    status               TEXT NOT NULL,
    error                TEXT,
    reinsertion_replaced INTEGER NOT NULL,
    reinsertion_missing  TEXT NOT NULL,
    reinsertion_unknown  TEXT NOT NULL,
    leakage_count        INTEGER NOT NULL
)
"""

_COLUMNS = (
    "request_id",
    "created_at",
    "doc_type",
    "purpose",
    "source_lang",
    "task",
    "entity_counts",
    "outbound_payload",
    "provider",
    "model",
    "latency_ms",
    "status",
    "error",
    "reinsertion_replaced",
    "reinsertion_missing",
    "reinsertion_unknown",
    "leakage_count",
)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class AuditRepository:
    """Thin, synchronous SQLite repository for `audit_requests`."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.db_path
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)
            self._migrate(conn)
            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Additive migrations for databases created before this column existed."""

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit_requests)")}
        if "source_lang" not in columns:
            conn.execute(
                "ALTER TABLE audit_requests ADD COLUMN source_lang TEXT NOT NULL DEFAULT 'en'"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def insert(self, record: AuditRecord) -> None:
        values = (
            record.request_id,
            record.created_at,
            record.doc_type,
            record.purpose,
            record.source_lang,
            record.task,
            _dumps(record.entity_counts),
            record.outbound_payload,
            record.provider,
            record.model,
            record.latency_ms,
            record.status,
            record.error,
            record.reinsertion_replaced,
            _dumps(record.reinsertion_missing),
            _dumps(record.reinsertion_unknown),
            record.leakage_count,
        )
        placeholders = ", ".join("?" for _ in _COLUMNS)
        with closing(self._connect()) as conn:
            conn.execute(
                f"INSERT INTO audit_requests ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                values,
            )
            conn.commit()

    def get(self, request_id: str) -> AuditRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM audit_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return None if row is None else self._to_record(row)

    def list(self, limit: int = 50) -> list[AuditRecord]:
        limit = max(1, min(int(limit), 500))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM audit_requests "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._to_record(row) for row in rows]

    def purge_older_than(self, days: int, *, dry_run: bool = False) -> int:
        """Purge rows whose created_at is older than now - days.

        ISO-8601 UTC strings in the table sort lexicographically, so string
        comparison against an ISO-8601 UTC cutoff timestamp is valid.
        When dry_run=True, returns the count of matching rows without deleting.
        """
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(days=days))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        with closing(self._connect()) as conn:
            if dry_run:
                row = conn.execute(
                    "SELECT COUNT(*) FROM audit_requests WHERE created_at < ?",
                    (cutoff,),
                ).fetchone()
                return int(row[0]) if row else 0

            cursor = conn.execute(
                "DELETE FROM audit_requests WHERE created_at < ?",
                (cutoff,),
            )
            deleted = cursor.rowcount
            conn.commit()
            return int(deleted)

    @staticmethod
    def _to_record(row: sqlite3.Row) -> AuditRecord:
        return AuditRecord(
            request_id=row["request_id"],
            created_at=row["created_at"],
            doc_type=row["doc_type"],
            purpose=row["purpose"],
            source_lang=row["source_lang"],
            task=row["task"],
            entity_counts=_loads(row["entity_counts"], {}),
            outbound_payload=row["outbound_payload"],
            provider=row["provider"],
            model=row["model"],
            latency_ms=row["latency_ms"],
            status=row["status"],
            error=row["error"],
            reinsertion_replaced=row["reinsertion_replaced"],
            reinsertion_missing=_loads(row["reinsertion_missing"], []),
            reinsertion_unknown=_loads(row["reinsertion_unknown"], []),
            leakage_count=row["leakage_count"],
        )
