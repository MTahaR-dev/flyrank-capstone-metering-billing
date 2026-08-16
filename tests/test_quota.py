"""Quota boundaries: just under, exactly at, and over the limit."""

import pytest

from app.errors import QuotaExceeded, SubscriptionInactive
from app.services.meter import record
from app.services.pricing import TokenUsage
from app.services.quota import current_period, require_within_quota

FREE_TOKEN_QUOTA = 100_000


def test_reaching_the_limit_exactly_is_allowed():
    require_within_quota("ai_tokens", used=999, requested=1, quota=1000)


def test_exceeding_the_limit_by_one_is_refused():
    with pytest.raises(QuotaExceeded) as exc:
        require_within_quota("ai_tokens", used=1000, requested=1, quota=1000)

    assert exc.value.quota == 1000
    assert exc.value.used == 1000


def test_a_single_request_larger_than_the_whole_quota_is_refused():
    with pytest.raises(QuotaExceeded):
        require_within_quota("ai_tokens", used=0, requested=1001, quota=1000)


def test_token_quota_is_enforced_end_to_end(conn, tenant):
    record(conn, tenant["id"], TokenUsage(input_tokens=FREE_TOKEN_QUOTA), "fill-quota")

    with pytest.raises(QuotaExceeded) as exc:
        record(conn, tenant["id"], TokenUsage(input_tokens=1), "one-too-many")

    assert exc.value.resource == "ai_tokens"
    assert exc.value.quota == FREE_TOKEN_QUOTA


def test_refused_request_records_no_event(conn, tenant):
    record(conn, tenant["id"], TokenUsage(input_tokens=FREE_TOKEN_QUOTA), "fill")

    with pytest.raises(QuotaExceeded):
        record(conn, tenant["id"], TokenUsage(input_tokens=1), "refused")

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_events WHERE tenant_id = %s", (tenant["id"],)
    ).fetchone()["n"]
    assert count == 1


def test_retry_of_an_allowed_request_still_succeeds_at_the_limit(conn, tenant):
    """A retry returns the original answer even once the quota is exhausted."""
    first = record(conn, tenant["id"], TokenUsage(input_tokens=FREE_TOKEN_QUOTA), "at-limit")
    retry = record(conn, tenant["id"], TokenUsage(input_tokens=FREE_TOKEN_QUOTA), "at-limit")

    assert retry.created is False
    assert retry.event["id"] == first.event["id"]


def test_inactive_subscription_is_refused(conn, tenant_factory):
    tenant = tenant_factory(subscription_status="past_due")

    with pytest.raises(SubscriptionInactive):
        record(conn, tenant["id"], TokenUsage(input_tokens=10), "past-due")


def test_period_window_is_a_calendar_month():
    from datetime import datetime, timezone

    start, end = current_period(datetime(2026, 12, 17, 4, 30, tzinfo=timezone.utc))

    assert start == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)
