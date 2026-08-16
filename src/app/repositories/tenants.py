import hashlib


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def find_by_api_key(conn, raw_key: str) -> dict | None:
    """Resolve the tenant from the key's hash; the raw key is never stored."""
    return conn.execute(
        """
        SELECT t.id, t.name, t.plan_code, t.subscription_status, t.stripe_customer_id,
               p.api_call_quota, p.ai_token_quota, p.monthly_price_cents
        FROM tenants t
        JOIN plans p ON p.code = t.plan_code
        WHERE t.api_key_hash = %s
        """,
        (hash_api_key(raw_key),),
    ).fetchone()


def lock_for_update(conn, tenant_id) -> dict | None:
    """Serialise billable writes for one tenant without affecting any other."""
    return conn.execute(
        """
        SELECT t.id, t.plan_code, t.subscription_status,
               p.api_call_quota, p.ai_token_quota
        FROM tenants t
        JOIN plans p ON p.code = t.plan_code
        WHERE t.id = %s
        FOR UPDATE OF t
        """,
        (tenant_id,),
    ).fetchone()


def set_plan(conn, tenant_id, plan_code: str, subscription_status: str) -> None:
    conn.execute(
        "UPDATE tenants SET plan_code = %s, subscription_status = %s WHERE id = %s",
        (plan_code, subscription_status, tenant_id),
    )


def set_stripe_customer(conn, tenant_id, stripe_customer_id: str) -> None:
    conn.execute(
        "UPDATE tenants SET stripe_customer_id = %s WHERE id = %s",
        (stripe_customer_id, tenant_id),
    )
