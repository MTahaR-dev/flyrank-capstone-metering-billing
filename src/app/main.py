from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from app import db

app = FastAPI(
    title="Usage Metering & Billing Engine",
    description="Usage metering, quota enforcement, cost calculation and Stripe subscriptions.",
    version="0.1.0",
)


@app.get("/health", tags=["Meta"])
def health():
    """Liveness plus a real database round trip."""
    try:
        database_ok = db.ping()
    except Exception:
        database_ok = False

    status = "ok" if database_ok else "degraded"
    return JSONResponse(
        status_code=200 if database_ok else 503,
        content={"status": status, "database": database_ok},
    )


@app.exception_handler(StarletteHTTPException)
def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
