"""Applies Stripe subscription events to the local mirror.

Stripe is the source of truth for payment state. This module never decides anything
about payment; it only reflects what a verified event reported.
"""

from datetime import datetime, timezone

from app.repositories import subscriptions as subs_repo
from app.repositories import tenants as tenants_repo

HANDLED_EVENTS = {
    "checkout.session.completed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}

# Stripe status -> the status stored on the tenant
TENANT_STATUS = {
    "active": "active",
    "trialing": "active",
    "past_due": "past_due",
    "unpaid": "past_due",
    "incomplete": "past_due",
    "incomplete_expired": "canceled",
    "canceled": "canceled",
}


def _timestamp(value) -> datetime | None:
    return datetime.fromtimestamp(value, tz=timezone.utc) if value else None


def _resolve_tenant_id(conn, obj: dict):
    """Find the tenant a Stripe object belongs to, by reference, customer, then subscription."""
    tenant_id = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("tenant_id")
    if tenant_id:
        return tenant_id

    customer_id = obj.get("customer")
    if customer_id:
        row = subs_repo.find_tenant_by_stripe_customer(conn, customer_id)
        if row:
            return row["id"]

    subscription_id = obj.get("id") if obj.get("object") == "subscription" else None
    if subscription_id:
        row = subs_repo.find_tenant_by_subscription(conn, subscription_id)
        if row:
            return row["id"]

    return None


def apply_event(conn, event: dict) -> str:
    """Return a short description of what the event changed."""
    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type not in HANDLED_EVENTS:
        return "ignored"

    tenant_id = _resolve_tenant_id(conn, obj)
    if tenant_id is None:
        return "no matching tenant"

    if event_type == "checkout.session.completed":
        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription")

        if customer_id:
            tenants_repo.set_stripe_customer(conn, tenant_id, customer_id)
        if subscription_id:
            subs_repo.upsert(conn, tenant_id, subscription_id, "pro", "active")

        tenants_repo.set_plan(conn, tenant_id, "pro", "active")
        return "upgraded to pro"

    stripe_status = obj.get("status", "canceled")
    tenant_status = TENANT_STATUS.get(stripe_status, "past_due")

    if event_type == "customer.subscription.deleted":
        subs_repo.upsert(conn, tenant_id, obj["id"], "free", "canceled")
        tenants_repo.set_plan(conn, tenant_id, "free", "active")
        return "downgraded to free"

    plan_code = "pro" if tenant_status == "active" else "pro"
    subs_repo.upsert(
        conn,
        tenant_id,
        obj["id"],
        plan_code,
        stripe_status,
        _timestamp(obj.get("current_period_start")),
        _timestamp(obj.get("current_period_end")),
    )
    tenants_repo.set_plan(conn, tenant_id, plan_code, tenant_status)
    return f"subscription {stripe_status}"
