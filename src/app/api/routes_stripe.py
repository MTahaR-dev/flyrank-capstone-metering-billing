import json

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_db, get_tenant
from app.config import settings
from app.repositories import subscriptions as subs_repo
from app.services.subscriptions import apply_event

router = APIRouter()


@router.post("/billing/checkout", tags=["Billing"])
def create_checkout_session(tenant=Depends(get_tenant)):
    """Start a Stripe Checkout session for the Pro plan."""
    if not settings.stripe_secret_key or not settings.stripe_pro_price_id:
        raise HTTPException(status_code=503, detail="Stripe is not configured")

    stripe.api_key = settings.stripe_secret_key

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": settings.stripe_pro_price_id, "quantity": 1}],
        success_url=settings.checkout_success_url,
        cancel_url=settings.checkout_cancel_url,
        client_reference_id=str(tenant["id"]),
        metadata={"tenant_id": str(tenant["id"])},
        customer=tenant["stripe_customer_id"] or None,
    )

    return {"checkout_url": session.url, "session_id": session.id}


@router.post("/webhooks/stripe", tags=["Billing"])
async def stripe_webhook(request: Request, conn=Depends(get_db)):
    """Verify the signature first, then process the event exactly once."""
    # The signature covers the exact bytes Stripe sent; parsing before verifying
    # would re-serialise them and the comparison would fail.
    payload = await request.body()
    signature = request.headers.get("Stripe-Signature")

    if not signature:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    try:
        stripe.Webhook.construct_event(
            payload, signature, settings.stripe_webhook_secret
        )
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    # Verified bytes, parsed here so the service layer works with plain dicts
    # rather than Stripe's own object types.
    event = json.loads(payload)

    if not subs_repo.claim_event(conn, event["id"], event["type"]):
        conn.commit()
        return {"received": True, "status": "duplicate ignored"}

    outcome = apply_event(conn, event)
    conn.commit()

    return {"received": True, "status": outcome}


@router.get("/billing/success", tags=["Billing"])
def checkout_success():
    return {"status": "checkout complete"}


@router.get("/billing/cancel", tags=["Billing"])
def checkout_cancel():
    return {"status": "checkout cancelled"}
