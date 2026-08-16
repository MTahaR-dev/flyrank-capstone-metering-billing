import hashlib
import json
from dataclasses import dataclass

from app.errors import IdempotencyConflict
from app.repositories import tenants as tenants_repo
from app.repositories import usage_events as usage_repo
from app.services.pricing import PRICING_VERSION, TokenUsage, token_cost_nanos


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
    """Record one ai_tokens usage event, exactly once per idempotency key.

    Returns MeterResult(event, created=True) the first time a key is seen, and
    MeterResult(event, created=False) for any repeat of that same key.

    Raises IdempotencyConflict if the key was first used for different work.
    """
    event_type = "ai_tokens"
    quantity = tokens.total
    cost_nanos = token_cost_nanos(tokens)
    request_fingerprint = fingerprint(event_type, quantity, tokens)

    tenants_repo.lock_for_update(conn, tenant_id)

    inserted = usage_repo.insert_if_new(
        conn,
        tenant_id,
        event_type,
        quantity,
        tokens,
        cost_nanos,
        PRICING_VERSION,
        idempotency_key,
        request_fingerprint,
    )

    if inserted is not None:
        conn.commit()
        return MeterResult(event=inserted, created=True)

    # No row came back: this tenant has already used this key.
    original = usage_repo.find_by_idempotency_key(conn, tenant_id, idempotency_key)
    conn.commit()

    if original["request_fingerprint"] != request_fingerprint:
        raise IdempotencyConflict(
            "Idempotency key was already used for a different request"
        )

    return MeterResult(event=original, created=False)
