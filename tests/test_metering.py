"""The guarantee that a retried request cannot be billed twice."""

import pytest

from app.errors import IdempotencyConflict
from app.services.meter import record
from app.services.pricing import TokenUsage


def count_events(conn, tenant_id) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM usage_events WHERE tenant_id = %s", (tenant_id,)
    ).fetchone()["n"]


def test_same_key_records_exactly_one_event(conn, tenant):
    tokens = TokenUsage(input_tokens=1000, output_tokens=200)

    first = record(conn, tenant["id"], tokens, "key-abc")
    second = record(conn, tenant["id"], tokens, "key-abc")

    assert first.created is True
    assert second.created is False
    assert first.event["id"] == second.event["id"]
    assert count_events(conn, tenant["id"]) == 1


def test_retry_mirrors_the_original_result(conn, tenant):
    tokens = TokenUsage(input_tokens=500, cached_input_tokens=100, output_tokens=50)

    first = record(conn, tenant["id"], tokens, "key-mirror")
    second = record(conn, tenant["id"], tokens, "key-mirror")

    assert second.event["cost_nanos"] == first.event["cost_nanos"]
    assert second.event["quantity"] == first.event["quantity"]
    assert second.event["occurred_at"] == first.event["occurred_at"]


def test_different_keys_record_separate_events(conn, tenant):
    tokens = TokenUsage(input_tokens=100)

    record(conn, tenant["id"], tokens, "key-1")
    record(conn, tenant["id"], tokens, "key-2")

    assert count_events(conn, tenant["id"]) == 2


def test_same_key_for_different_work_is_rejected(conn, tenant):
    record(conn, tenant["id"], TokenUsage(input_tokens=100), "key-conflict")

    with pytest.raises(IdempotencyConflict):
        record(conn, tenant["id"], TokenUsage(input_tokens=999), "key-conflict")

    assert count_events(conn, tenant["id"]) == 1


def test_same_key_across_tenants_is_independent(conn, tenant_factory):
    a, b = tenant_factory(), tenant_factory()
    tokens = TokenUsage(input_tokens=100)

    record(conn, a["id"], tokens, "shared-key")
    record(conn, b["id"], tokens, "shared-key")

    assert count_events(conn, a["id"]) == 1
    assert count_events(conn, b["id"]) == 1
