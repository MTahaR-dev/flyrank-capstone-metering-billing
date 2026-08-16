def claim_event(conn, event_id: str, event_type: str) -> bool:
    """Insert the Stripe event id. False means it was already processed."""
    row = conn.execute(
        """
        INSERT INTO processed_webhook_events (event_id, event_type)
        VALUES (%s, %s)
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id
        """,
        (event_id, event_type),
    ).fetchone()
    return row is not None


def upsert(
    conn,
    tenant_id,
    stripe_subscription_id: str,
    plan_code: str,
    status: str,
    current_period_start=None,
    current_period_end=None,
) -> None:
    conn.execute(
        """
        INSERT INTO subscriptions (
            tenant_id, stripe_subscription_id, plan_code, status,
            current_period_start, current_period_end, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (stripe_subscription_id) DO UPDATE SET
            plan_code            = EXCLUDED.plan_code,
            status               = EXCLUDED.status,
            current_period_start = EXCLUDED.current_period_start,
            current_period_end   = EXCLUDED.current_period_end,
            updated_at           = now()
        """,
        (
            tenant_id,
            stripe_subscription_id,
            plan_code,
            status,
            current_period_start,
            current_period_end,
        ),
    )


def find_tenant_by_stripe_customer(conn, stripe_customer_id: str) -> dict | None:
    return conn.execute(
        "SELECT id FROM tenants WHERE stripe_customer_id = %s", (stripe_customer_id,)
    ).fetchone()


def find_tenant_by_subscription(conn, stripe_subscription_id: str) -> dict | None:
    return conn.execute(
        "SELECT tenant_id AS id FROM subscriptions WHERE stripe_subscription_id = %s",
        (stripe_subscription_id,),
    ).fetchone()
