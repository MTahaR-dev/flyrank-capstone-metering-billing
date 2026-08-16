RETURNED_COLUMNS = """
    id, tenant_id, event_type, quantity,
    input_tokens, cached_input_tokens, output_tokens, reasoning_tokens,
    cost_nanos, pricing_version, idempotency_key, request_fingerprint, occurred_at
"""


def insert_if_new(
    conn,
    tenant_id,
    event_type: str,
    quantity: int,
    tokens,
    cost_nanos: int,
    pricing_version: str,
    idempotency_key: str,
    request_fingerprint: str,
) -> dict | None:
    """Insert the event, or return None if this tenant already used this key."""
    return conn.execute(
        f"""
        INSERT INTO usage_events (
            tenant_id, event_type, quantity,
            input_tokens, cached_input_tokens, output_tokens, reasoning_tokens,
            cost_nanos, pricing_version, idempotency_key, request_fingerprint
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT ON CONSTRAINT usage_events_tenant_idempotency_key DO NOTHING
        RETURNING {RETURNED_COLUMNS}
        """,
        (
            tenant_id,
            event_type,
            quantity,
            tokens.input_tokens,
            tokens.cached_input_tokens,
            tokens.output_tokens,
            tokens.reasoning_tokens,
            cost_nanos,
            pricing_version,
            idempotency_key,
            request_fingerprint,
        ),
    ).fetchone()


def find_by_idempotency_key(conn, tenant_id, idempotency_key: str) -> dict | None:
    return conn.execute(
        f"SELECT {RETURNED_COLUMNS} FROM usage_events "
        "WHERE tenant_id = %s AND idempotency_key = %s",
        (tenant_id, idempotency_key),
    ).fetchone()


def month_totals(conn, tenant_id, period_start, period_end) -> dict:
    """Usage and cost for one tenant within a half-open time window."""
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(quantity) FILTER (WHERE event_type = 'api_call'), 0) AS api_calls,
            COALESCE(SUM(input_tokens + cached_input_tokens
                         + output_tokens + reasoning_tokens), 0)              AS ai_tokens,
            COALESCE(SUM(cost_nanos), 0)                                      AS cost_nanos,
            COUNT(*)                                                          AS event_count
        FROM usage_events
        WHERE tenant_id = %s AND occurred_at >= %s AND occurred_at < %s
        """,
        (tenant_id, period_start, period_end),
    ).fetchone()

    # SUM() over BIGINT returns numeric, which arrives as Decimal
    return {key: int(value) for key, value in row.items()}
