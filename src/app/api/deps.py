from fastapi import Depends, Header, HTTPException

from app.db import connection
from app.repositories import tenants as tenants_repo


def get_db():
    with connection() as conn:
        yield conn


def get_tenant(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    conn=Depends(get_db),
) -> dict:
    """Resolve the tenant from the API key hash, never from the request body."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")

    tenant = tenants_repo.find_by_api_key(conn, x_api_key)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return tenant


def require_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str:
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail="Idempotency-Key header required")
    return idempotency_key.strip()
