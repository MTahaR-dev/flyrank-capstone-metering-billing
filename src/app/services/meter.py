import hashlib
import json
from dataclasses import dataclass

from app.errors import IdempotencyConflict, TenantNotFound
from app.repositories import tenants as tenants_repo
from app.repositories import usage_events as usage_repo
from app.services import quota
from app.services.pricing import (
    PRICING_VERSION,
    TokenUsage,
    api_call_cost_nanos,
    token_cost_nanos,
)

EVENT_TYPE = "api_call"


@dataclass(frozen=True)
class MeterResult:
    event: dict
    created: bool


def fingerprint(event_type: str, quantity: int, tokens: TokenUsage) -> str:
    """Identifies the work a key was first used for, so reuse can be detected."""
    payload = json.dumps(
        {
            "event_type": event_type,
            "quantity": quantity,
            "input_tokens": tokens.input_tokens,
            "cached_input_tokens": tokens.cached_input_tokens,
            "output_tokens": tokens.output_tokens,
            "reasoning_tokens": tokens.reasoning_tokens,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def record(conn, tenant_id, tokens: TokenUsage, idempotency_key: str) -> MeterResult:
    """Record one billable event, exactly once per idempotency key.

    Returns created=True the first time a key is seen and created=False for any
    repeat of it. Raises IdempotencyConflict if the key was first used for
    different work, SubscriptionInactive if the plan is not in good standing, and
    QuotaExceeded if the request would take the tenant past its monthly limit.
    """
    quantity = 1
    cost_nanos = api_call_cost_nanos(quantity) + token_cost_nanos(tokens)
    request_fingerprint = fingerprint(EVENT_TYPE, quantity, tokens)

    tenant = tenants_repo.lock_for_update(conn, tenant_id)
    if tenant is None:
        raise TenantNotFound(str(tenant_id))

    # A retry must return the original answer, never a fresh judgement, so the
    # idempotency lookup happens before any quota or subscription check.
    original = usage_repo.find_by_idempotency_key(conn, tenant_id, idempotency_key)
    if original is not None:
        conn.commit()
        if original["request_fingerprint"] != request_fingerprint:
            raise IdempotencyConflict(
                "Idempotency key was already used for a different request"
            )
        return MeterResult(event=original, created=False)

    quota.require_active_subscription(tenant["subscription_status"])

    period_start, period_end = quota.current_period()
    totals = usage_repo.month_totals(conn, tenant_id, period_start, period_end)

    quota.require_within_quota(
        "api_calls", totals["api_calls"], quantity, tenant["api_call_quota"]
    )
    quota.require_within_quota(
        "ai_tokens", totals["ai_tokens"], tokens.total, tenant["ai_token_quota"]
    )

    inserted = usage_repo.insert_if_new(
        conn,
        tenant_id,
        EVENT_TYPE,
        quantity,
        tokens,
        cost_nanos,
        PRICING_VERSION,
        idempotency_key,
        request_fingerprint,
    )

    # None means a concurrent request inserted this key first; return that row.
    if inserted is None:
        original = usage_repo.find_by_idempotency_key(conn, tenant_id, idempotency_key)
        conn.commit()
        return MeterResult(event=original, created=False)

    conn.commit()
    return MeterResult(event=inserted, created=True)
