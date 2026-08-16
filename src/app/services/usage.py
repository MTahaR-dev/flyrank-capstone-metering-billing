from app.repositories import usage_events as usage_repo
from app.services import quota
from app.services.pricing import nanos_to_cents


def _resource(used: int, limit: int) -> dict:
    return {"used": used, "limit": limit, "remaining": max(limit - used, 0)}


def nanos_to_usd_string(nanos: int) -> str:
    """Exact decimal rendering, formatted with integer arithmetic only."""
    return f"{nanos // 1_000_000_000}.{nanos % 1_000_000_000:09d}"


def monthly_rollup(conn, tenant: dict) -> dict:
    """Aggregate this calendar month's usage events into used / limit / cost."""
    period_start, period_end = quota.current_period()
    totals = usage_repo.month_totals(conn, tenant["id"], period_start, period_end)

    cost_nanos = totals["cost_nanos"]
    base_cents = tenant["monthly_price_cents"]
    metered_cents = nanos_to_cents(cost_nanos)

    return {
        "tenant_id": str(tenant["id"]),
        "tenant_name": tenant["name"],
        "plan": tenant["plan_code"],
        "subscription_status": tenant["subscription_status"],
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "api_calls": _resource(totals["api_calls"], tenant["api_call_quota"]),
        "ai_tokens": _resource(totals["ai_tokens"], tenant["ai_token_quota"]),
        "cost": {
            "events": totals["event_count"],
            "metered_nanos": cost_nanos,
            "metered_usd": nanos_to_usd_string(cost_nanos),
            "metered_cents": metered_cents,
            "plan_base_cents": base_cents,
            "total_cents": base_cents + metered_cents,
            "total_usd": f"{(base_cents + metered_cents) / 100:.2f}",
        },
    }
