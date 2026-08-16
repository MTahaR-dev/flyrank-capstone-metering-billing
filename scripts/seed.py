"""Create demo tenants and print their API keys."""

import hashlib
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from app.db import connection

DEMO_TENANTS = [
    ("Acme Corp", "free"),
    ("Globex Industries", "pro"),
]


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def seed() -> None:
    with connection() as conn:
        for name, plan_code in DEMO_TENANTS:
            existing = conn.execute(
                "SELECT id FROM tenants WHERE name = %s", (name,)
            ).fetchone()

            if existing:
                print(f"{name:20} already seeded (id {existing['id']})")
                continue

            raw_key = f"sk_demo_{secrets.token_urlsafe(24)}"
            row = conn.execute(
                """
                INSERT INTO tenants (name, plan_code, api_key_hash)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (name, plan_code, hash_key(raw_key)),
            ).fetchone()

            print(f"{name:20} plan={plan_code:5} id={row['id']}")
            print(f"{'':20} API key: {raw_key}")

        conn.commit()

    print("\nStore these keys now; only their hashes are kept.")


if __name__ == "__main__":
    seed()
