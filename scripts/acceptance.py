"""Run the acceptance probes against a live instance and print a transcript.

Usage:  python scripts/acceptance.py [base_url]

Creates throwaway tenants, exercises every guarantee, then removes them.
"""

import hashlib
import hmac
import json
import secrets
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from app.config import settings
from app.db import connection
from app.repositories.tenants import hash_api_key

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
FREE_TOKEN_QUOTA = 100_000

created_tenants: list[str] = []


def make_tenant(plan_code: str = "free") -> str:
    raw_key = f"sk_probe_{secrets.token_urlsafe(16)}"
    with connection() as conn:
        row = conn.execute(
            """
            INSERT INTO tenants (name, plan_code, api_key_hash)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (f"Probe {uuid.uuid4().hex[:6]}", plan_code, hash_api_key(raw_key)),
        ).fetchone()
        conn.commit()
    created_tenants.append(row["id"])
    return raw_key


def cleanup() -> None:
    with connection() as conn:
        for tenant_id in created_tenants:
            conn.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
        conn.execute("DELETE FROM processed_webhook_events WHERE event_id LIKE 'evt_probe%%'")
        conn.commit()


def show(label: str, response: httpx.Response) -> dict:
    body = response.json()
    print(f"\n$ {label}")
    print(f"HTTP {response.status_code}")
    print(json.dumps(body, indent=2))
    return body


def sign(payload: str) -> str:
    timestamp = int(time.time())
    signature = hmac.new(
        settings.stripe_webhook_secret.encode(),
        f"{timestamp}.{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def banner(text: str) -> None:
    print(f"\n{'=' * 72}\n{text}\n{'=' * 72}")


def probe_idempotency(client: httpx.Client) -> None:
    banner("PROBE 1  the same request twice with one idempotency key")
    key = make_tenant()
    headers = {"X-API-Key": key, "Idempotency-Key": "probe-idem-1"}
    body = {"input_tokens": 1000, "cached_input_tokens": 500,
            "output_tokens": 200, "reasoning_tokens": 50}

    first = show("POST /generate", client.post("/generate", json=body, headers=headers))
    second = show("POST /generate  (identical request, identical key)",
                  client.post("/generate", json=body, headers=headers))

    print(f"\nsame event_id:      {first['event_id'] == second['event_id']}")
    print(f"second is duplicate: {second['duplicate']}")

    usage = show("GET /usage", client.get("/usage", headers={"X-API-Key": key}))
    print(f"\nevents recorded: {usage['cost']['events']}  (must be 1)")


def probe_quota_boundary(client: httpx.Client) -> None:
    banner("PROBE 2  the exact quota boundary")
    key = make_tenant()
    headers = {"X-API-Key": key}

    show(
        f"POST /generate  ({FREE_TOKEN_QUOTA} tokens, taking usage to exactly the limit)",
        client.post(
            "/generate",
            json={"input_tokens": FREE_TOKEN_QUOTA},
            headers={**headers, "Idempotency-Key": "probe-at-limit"},
        ),
    )
    show(
        "POST /generate  (one token more)",
        client.post(
            "/generate",
            json={"input_tokens": 1},
            headers={**headers, "Idempotency-Key": "probe-over-limit"},
        ),
    )
    show(
        "POST /generate  (retry of the request that succeeded at the limit)",
        client.post(
            "/generate",
            json={"input_tokens": FREE_TOKEN_QUOTA},
            headers={**headers, "Idempotency-Key": "probe-at-limit"},
        ),
    )


def probe_payment_required(client: httpx.Client) -> None:
    banner("PROBE 2b  a lapsed subscription is refused with 402, not 429")
    key = make_tenant()
    with connection() as conn:
        conn.execute(
            "UPDATE tenants SET subscription_status = 'past_due' WHERE api_key_hash = %s",
            (hash_api_key(key),),
        )
        conn.commit()

    show(
        "POST /generate  (subscription past_due, usage well under quota)",
        client.post(
            "/generate",
            json={"input_tokens": 10},
            headers={"X-API-Key": key, "Idempotency-Key": "probe-past-due"},
        ),
    )


def probe_webhooks(client: httpx.Client) -> None:
    banner("PROBE 4  webhook signature verification and replay protection")
    key = make_tenant()
    with connection() as conn:
        tenant_id = conn.execute(
            "SELECT id FROM tenants WHERE api_key_hash = %s", (hash_api_key(key),)
        ).fetchone()["id"]

    payload = json.dumps({
        "id": "evt_probe_1",
        "type": "checkout.session.completed",
        "data": {"object": {
            "object": "checkout_session",
            "client_reference_id": str(tenant_id),
            "customer": "cus_probe_1",
            "subscription": "sub_probe_1",
        }},
    })

    forged = hmac.new(b"whsec_wrong_secret",
                      f"{int(time.time())}.{payload}".encode(), hashlib.sha256).hexdigest()
    show(
        "POST /webhooks/stripe  (forged signature)",
        client.post("/webhooks/stripe", content=payload,
                    headers={"Stripe-Signature": f"t={int(time.time())},v1={forged}",
                             "Content-Type": "application/json"}),
    )

    before = show("GET /usage  (plan before any valid event)",
                  client.get("/usage", headers={"X-API-Key": key}))
    print(f"\nplan unchanged by the forgery: {before['plan']}")

    signature = sign(payload)
    show("POST /webhooks/stripe  (valid signature)",
         client.post("/webhooks/stripe", content=payload,
                     headers={"Stripe-Signature": signature,
                              "Content-Type": "application/json"}))
    show("POST /webhooks/stripe  (same event replayed)",
         client.post("/webhooks/stripe", content=payload,
                     headers={"Stripe-Signature": signature,
                              "Content-Type": "application/json"}))

    after = show("GET /usage  (plan after the verified event)",
                 client.get("/usage", headers={"X-API-Key": key}))
    print(f"\nplan now: {after['plan']}   token limit now: {after['ai_tokens']['limit']}")


def probe_pricing(client: httpx.Client) -> None:
    banner("PROBE 5  metered cost matches the pinned pricing tests")
    key = make_tenant()
    body = {"input_tokens": 1000, "cached_input_tokens": 500,
            "output_tokens": 200, "reasoning_tokens": 50}

    client.post("/generate", json=body,
                headers={"X-API-Key": key, "Idempotency-Key": "probe-price"})
    usage = show("GET /usage", client.get("/usage", headers={"X-API-Key": key}))

    expected = 100_000 + (1000 * 300 + 500 * 75 + 200 * 2500 + 50 * 2500)
    actual = usage["cost"]["metered_nanos"]
    print(f"\nexpected nanos: {expected}")
    print(f"actual nanos:   {actual}")
    print(f"match:          {expected == actual}")


def main() -> None:
    print(f"Acceptance probes against {BASE_URL}")
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        show("GET /health", client.get("/health"))
        try:
            probe_idempotency(client)
            probe_quota_boundary(client)
            probe_payment_required(client)
            probe_webhooks(client)
            probe_pricing(client)
        finally:
            cleanup()

    banner("PROBE 3  Stripe Checkout is run manually against Stripe test mode")


if __name__ == "__main__":
    main()
