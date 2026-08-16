from fastapi import APIRouter, Depends, Response

from app.api.deps import get_db, get_tenant, require_idempotency_key
from app.api.schemas import GenerateRequest, GenerateResponse
from app.services.meter import record
from app.services.pricing import TokenUsage

router = APIRouter()


def usd(nanos: int) -> str:
    return f"{nanos / 1_000_000_000:.9f}".rstrip("0").rstrip(".") or "0"


@router.post("/generate", response_model=GenerateResponse, tags=["Billing"])
def generate(
    payload: GenerateRequest,
    response: Response,
    tenant=Depends(get_tenant),
    idempotency_key: str = Depends(require_idempotency_key),
    conn=Depends(get_db),
):
    """Billable action: meters usage, enforces quota, returns the cost of this call."""
    tokens = TokenUsage(
        input_tokens=payload.input_tokens,
        cached_input_tokens=payload.cached_input_tokens,
        output_tokens=payload.output_tokens,
        reasoning_tokens=payload.reasoning_tokens,
    )

    result = record(conn, tenant["id"], tokens, idempotency_key)
    event = result.event

    # 201 the first time a key is used, 200 when mirroring an earlier result.
    response.status_code = 201 if result.created else 200

    return GenerateResponse(
        event_id=str(event["id"]),
        tenant_id=str(event["tenant_id"]),
        idempotency_key=event["idempotency_key"],
        duplicate=not result.created,
        quantity=event["quantity"],
        tokens_charged=(
            event["input_tokens"]
            + event["cached_input_tokens"]
            + event["output_tokens"]
            + event["reasoning_tokens"]
        ),
        cost_nanos=event["cost_nanos"],
        cost_usd=usd(event["cost_nanos"]),
        pricing_version=event["pricing_version"],
        occurred_at=event["occurred_at"].isoformat(),
    )
