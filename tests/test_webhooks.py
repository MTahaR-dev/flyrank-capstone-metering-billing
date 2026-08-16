"""Webhook security: forged signatures rejected, real events processed exactly once.

Signatures are generated locally with the same HMAC scheme Stripe uses, so these
tests need no network and no Stripe account.
"""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

TEST_SECRET = "whsec_test_secret_for_local_signing"


@pytest.fixture(autouse=True)
def webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", TEST_SECRET)


@pytest.fixture
def client():
    return TestClient(app)


def sign(payload: str, secret: str = TEST_SECRET) -> str:
    timestamp = int(time.time())
    signature = hmac.new(
        secret.encode(), f"{timestamp}.{payload}".encode(), hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def checkout_event(event_id: str, tenant_id: str) -> str:
    return json.dumps(
        {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "object": "checkout_session",
                    "client_reference_id": str(tenant_id),
                    "customer": f"cus_test_{event_id}",
                    "subscription": f"sub_test_{event_id}",
                }
            },
        }
    )


def post(client, payload: str, signature: str | None):
    headers = {"Content-Type": "application/json"}
    if signature:
        headers["Stripe-Signature"] = signature
    return client.post("/webhooks/stripe", content=payload, headers=headers)


def test_forged_signature_is_rejected(client, tenant, conn):
    payload = checkout_event("evt_forged", tenant["id"])

    response = post(client, payload, sign(payload, "whsec_wrong_secret"))

    assert response.status_code == 400
    assert response.json()["error"] == "Invalid webhook signature"

    plan = conn.execute(
        "SELECT plan_code FROM tenants WHERE id = %s", (tenant["id"],)
    ).fetchone()["plan_code"]
    assert plan == "free"


def test_missing_signature_header_is_rejected(client, tenant):
    payload = checkout_event("evt_nosig", tenant["id"])

    assert post(client, payload, None).status_code == 400


def test_tampered_payload_is_rejected(client, tenant):
    payload = checkout_event("evt_tampered", tenant["id"])
    signature = sign(payload)
    tampered = payload.replace("checkout.session.completed", "customer.subscription.deleted")

    assert post(client, tampered, signature).status_code == 400


def test_valid_event_upgrades_the_tenant(client, tenant, conn):
    payload = checkout_event("evt_valid", tenant["id"])

    response = post(client, payload, sign(payload))

    assert response.status_code == 200
    assert response.json()["status"] == "upgraded to pro"

    row = conn.execute(
        "SELECT plan_code, subscription_status FROM tenants WHERE id = %s",
        (tenant["id"],),
    ).fetchone()
    assert row["plan_code"] == "pro"
    assert row["subscription_status"] == "active"


def test_replayed_event_is_processed_only_once(client, tenant, conn):
    payload = checkout_event("evt_replay", tenant["id"])
    signature = sign(payload)

    first = post(client, payload, signature)
    second = post(client, payload, signature)

    assert first.json()["status"] == "upgraded to pro"
    assert second.json()["status"] == "duplicate ignored"

    processed = conn.execute(
        "SELECT COUNT(*) AS n FROM processed_webhook_events WHERE event_id = %s",
        ("evt_replay",),
    ).fetchone()["n"]
    assert processed == 1


def test_subscription_deleted_downgrades_to_free(client, tenant, conn):
    upgrade = checkout_event("evt_up", tenant["id"])
    post(client, upgrade, sign(upgrade))

    deleted = json.dumps(
        {
            "id": "evt_down",
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "object": "subscription",
                    "id": "sub_test_evt_up",
                    "status": "canceled",
                    "customer": "cus_test_evt_up",
                }
            },
        }
    )

    response = post(client, deleted, sign(deleted))

    assert response.json()["status"] == "downgraded to free"
    plan = conn.execute(
        "SELECT plan_code FROM tenants WHERE id = %s", (tenant["id"],)
    ).fetchone()["plan_code"]
    assert plan == "free"
