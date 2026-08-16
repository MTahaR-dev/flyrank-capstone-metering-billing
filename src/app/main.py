from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from app import db
from app.api.routes_usage import router as usage_router
from app.errors import (
    IdempotencyConflict,
    QuotaExceeded,
    SubscriptionInactive,
    TenantNotFound,
)

app = FastAPI(
    title="Usage Metering & Billing Engine",
    description="Usage metering, quota enforcement, cost calculation and Stripe subscriptions.",
    version="0.1.0",
)

app.include_router(usage_router)


@app.get("/health", tags=["Meta"])
def health():
    """Liveness plus a real database round trip."""
    try:
        database_ok = db.ping()
    except Exception:
        database_ok = False

    return JSONResponse(
        status_code=200 if database_ok else 503,
        content={"status": "ok" if database_ok else "degraded", "database": database_ok},
    )


@app.exception_handler(QuotaExceeded)
def quota_exceeded_handler(request: Request, exc: QuotaExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "error": str(exc),
            "resource": exc.resource,
            "quota": exc.quota,
            "used": exc.used,
            "requested": exc.requested,
        },
    )


@app.exception_handler(SubscriptionInactive)
def subscription_inactive_handler(request: Request, exc: SubscriptionInactive):
    return JSONResponse(
        status_code=402,
        content={
            "error": "Subscription is not active; payment or upgrade required",
            "subscription_status": exc.status,
        },
    )


@app.exception_handler(IdempotencyConflict)
def idempotency_conflict_handler(request: Request, exc: IdempotencyConflict):
    return JSONResponse(status_code=409, content={"error": str(exc)})


@app.exception_handler(TenantNotFound)
def tenant_not_found_handler(request: Request, exc: TenantNotFound):
    return JSONResponse(status_code=401, content={"error": "Invalid API key"})


@app.exception_handler(StarletteHTTPException)
def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
