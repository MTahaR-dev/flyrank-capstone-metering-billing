"""The rollup must agree exactly with the pinned per-event pricing."""

from app.repositories.tenants import find_by_api_key
from app.services.meter import record
from app.services.pricing import TokenUsage
from app.services.usage import monthly_rollup


def rollup_for(conn, tenant) -> dict:
    return monthly_rollup(conn, find_by_api_key(conn, tenant["api_key"]))


def test_empty_tenant_reports_zero_usage(conn, tenant):
    result = rollup_for(conn, tenant)

    assert result["api_calls"]["used"] == 0
    assert result["ai_tokens"]["used"] == 0
    assert result["cost"]["metered_nanos"] == 0
    assert result["cost"]["events"] == 0


def test_rollup_counts_calls_and_tokens(conn, tenant):
    tokens = TokenUsage(input_tokens=1000, output_tokens=200)
    record(conn, tenant["id"], tokens, "roll-1")
    record(conn, tenant["id"], tokens, "roll-2")

    result = rollup_for(conn, tenant)

    assert result["api_calls"]["used"] == 2
    assert result["ai_tokens"]["used"] == 2400
    assert result["cost"]["events"] == 2


def test_rollup_cost_matches_pinned_pricing(conn, tenant):
    tokens = TokenUsage(
        input_tokens=1000, cached_input_tokens=500, output_tokens=200, reasoning_tokens=50
    )
    record(conn, tenant["id"], tokens, "cost-1")

    result = rollup_for(conn, tenant)

    assert result["cost"]["metered_nanos"] == 1_062_500


def test_duplicates_do_not_inflate_the_rollup(conn, tenant):
    tokens = TokenUsage(input_tokens=100)
    record(conn, tenant["id"], tokens, "same-key")
    record(conn, tenant["id"], tokens, "same-key")
    record(conn, tenant["id"], tokens, "same-key")

    result = rollup_for(conn, tenant)

    assert result["api_calls"]["used"] == 1
    assert result["cost"]["events"] == 1


def test_limits_and_remaining_come_from_the_plan(conn, tenant_factory):
    free = tenant_factory(plan_code="free")
    record(conn, free["id"], TokenUsage(input_tokens=400), "limits-1")

    result = rollup_for(conn, free)

    assert result["plan"] == "free"
    assert result["ai_tokens"]["limit"] == 100_000
    assert result["ai_tokens"]["remaining"] == 99_600
    assert result["api_calls"]["limit"] == 1_000
    assert result["api_calls"]["remaining"] == 999
