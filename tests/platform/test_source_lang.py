"""Additive source_lang audit metadata and old-database migration."""

from __future__ import annotations

import sqlite3

from app.audit.repository import AuditRepository
from app.contracts import AuditRecord
from tests.platform.conftest import SAMPLE_TEXT


def test_round_trip_preserves_source_lang(tmp_path):
    repository = AuditRepository(str(tmp_path / "audit.db"))
    repository.insert(
        AuditRecord(
            request_id="req_ta",
            created_at="2026-11-13T00:00:00.000Z",
            source_lang="ta",
        )
    )

    record = repository.get("req_ta")
    assert record is not None
    assert record.source_lang == "ta"


def test_old_database_is_migrated_with_english_default(tmp_path):
    db_path = tmp_path / "old.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE audit_requests (
            request_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            purpose TEXT NOT NULL,
            task TEXT NOT NULL,
            entity_counts TEXT NOT NULL,
            outbound_payload TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            latency_ms INTEGER NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            reinsertion_replaced INTEGER NOT NULL,
            reinsertion_missing TEXT NOT NULL,
            reinsertion_unknown TEXT NOT NULL,
            leakage_count INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO audit_requests VALUES (
            'req_old', '2026-11-13T00:00:00.000Z', 'kyc', 'general', 'sanitize',
            '{}', '', '', '', 0, 'ok', NULL, 0, '[]', '[]', 0
        )
        """
    )
    connection.commit()
    connection.close()

    repository = AuditRepository(str(db_path))
    record = repository.get("req_old")
    assert record is not None
    assert record.source_lang == "en"

    columns = {
        row[1] for row in sqlite3.connect(db_path).execute("PRAGMA table_info(audit_requests)")
    }
    assert "source_lang" in columns


def test_sanitize_api_stores_source_lang(gateway):
    client, _provider, _repository = gateway
    response = client.post(
        "/v1/sanitize",
        json={"text": SAMPLE_TEXT, "source_lang": "ta"},
    )
    assert response.status_code == 200
    request_id = response.json()["request_id"]

    audit = client.get(f"/v1/audit/{request_id}")
    assert audit.status_code == 200
    assert audit.json()["source_lang"] == "ta"


def test_sanitize_api_defaults_source_lang_to_english(gateway):
    client, _provider, _repository = gateway
    response = client.post("/v1/sanitize", json={"text": SAMPLE_TEXT})
    assert response.status_code == 200
    request_id = response.json()["request_id"]

    audit = client.get(f"/v1/audit/{request_id}")
    assert audit.status_code == 200
    assert audit.json()["source_lang"] == "en"
