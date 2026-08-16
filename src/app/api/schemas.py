from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)


class GenerateResponse(BaseModel):
    event_id: str
    tenant_id: str
    idempotency_key: str
    duplicate: bool
    quantity: int
    tokens_charged: int
    cost_nanos: int
    cost_usd: str
    pricing_version: str
    occurred_at: str
