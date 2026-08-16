import secrets
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from app.db import connection
from app.repositories.tenants import hash_api_key


@pytest.fixture
def conn():
    with connection() as c:
        yield c


@pytest.fixture
def tenant_factory(conn):
    """Create throwaway tenants and remove them, and their events, afterwards."""
    created = []

    def make(plan_code: str = "free", subscription_status: str = "active") -> dict:
        raw_key = f"sk_test_{secrets.token_urlsafe(16)}"
        row = conn.execute(
            """
            INSERT INTO tenants (name, plan_code, subscription_status, api_key_hash)
            VALUES (%s, %s, %s, %s)
            RETURNING id, plan_code, subscription_status
            """,
            (f"Test {uuid.uuid4().hex[:8]}", plan_code, subscription_status,
             hash_api_key(raw_key)),
        ).fetchone()
        conn.commit()
        created.append(row["id"])
        return {**row, "api_key": raw_key}

    yield make

    for tenant_id in created:
        conn.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
    conn.commit()


@pytest.fixture
def tenant(tenant_factory):
    return tenant_factory()
