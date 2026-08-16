CREATE EXTENSION IF NOT EXISTS pgcrypto;


CREATE TABLE IF NOT EXISTS plans (
    code                TEXT PRIMARY KEY,
    name                TEXT   NOT NULL,
    api_call_quota      INTEGER NOT NULL CHECK (api_call_quota >= 0),
    ai_token_quota      BIGINT  NOT NULL CHECK (ai_token_quota >= 0),
    monthly_price_cents INTEGER NOT NULL CHECK (monthly_price_cents >= 0)
);


CREATE TABLE IF NOT EXISTS tenants (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    plan_code           TEXT NOT NULL REFERENCES plans (code),
    subscription_status TEXT NOT NULL DEFAULT 'active'
                        CHECK (subscription_status IN ('active', 'past_due', 'canceled')),
    api_key_hash        TEXT NOT NULL UNIQUE,
    stripe_customer_id  TEXT UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE IF NOT EXISTS subscriptions (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id              UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    stripe_subscription_id TEXT NOT NULL UNIQUE,
    plan_code              TEXT NOT NULL REFERENCES plans (code),
    status                 TEXT NOT NULL,
    current_period_start   TIMESTAMPTZ,
    current_period_end     TIMESTAMPTZ,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS subscriptions_tenant_idx ON subscriptions (tenant_id);


CREATE TABLE IF NOT EXISTS usage_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    event_type          TEXT NOT NULL CHECK (event_type IN ('api_call', 'ai_tokens')),
    quantity            BIGINT NOT NULL CHECK (quantity >= 0),

    input_tokens        INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    cached_input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (cached_input_tokens >= 0),
    output_tokens       INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    reasoning_tokens    INTEGER NOT NULL DEFAULT 0 CHECK (reasoning_tokens >= 0),

    cost_nanos          BIGINT NOT NULL CHECK (cost_nanos >= 0),
    pricing_version     TEXT   NOT NULL,

    idempotency_key     TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,

    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- the guarantee that a retry cannot create a second event
    CONSTRAINT usage_events_tenant_idempotency_key UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS usage_events_tenant_period_idx
    ON usage_events (tenant_id, occurred_at);


CREATE TABLE IF NOT EXISTS processed_webhook_events (
    event_id     TEXT PRIMARY KEY,
    event_type   TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


INSERT INTO plans (code, name, api_call_quota, ai_token_quota, monthly_price_cents)
VALUES
    ('free', 'Free',   1000,    100000,    0),
    ('pro',  'Pro',   50000,   5000000, 2900)
ON CONFLICT (code) DO NOTHING;
