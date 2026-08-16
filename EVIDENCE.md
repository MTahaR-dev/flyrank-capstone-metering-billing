# Evidence

One proof per Definition-of-Done item. Every transcript below is real output from a
local run: `docker compose up --build`, then `python scripts/acceptance.py`.

Reproduce all of it with:

```bash
docker compose up --build      # start the stack
python scripts/seed.py         # create demo tenants
python -m pytest tests -q      # the test suite
python scripts/acceptance.py   # the acceptance probes
```

---

## Metering

### A billable action creates exactly one usage event, even under retries

The same request, sent twice with the same `Idempotency-Key`. The second call returns
`200` instead of `201`, carries `"duplicate": true`, and mirrors the original — same
`event_id`, same `occurred_at`, same cost.

```
$ POST /generate
HTTP 201
{
  "event_id": "913716f2-946f-434a-a0f6-5f52e6e42d8a",
  "idempotency_key": "probe-idem-1",
  "duplicate": false,
  "tokens_charged": 1750,
  "cost_nanos": 1062500,
  "occurred_at": "2026-08-16T22:50:07.241685+00:00"
}

$ POST /generate  (identical request, identical key)
HTTP 200
{
  "event_id": "913716f2-946f-434a-a0f6-5f52e6e42d8a",
  "idempotency_key": "probe-idem-1",
  "duplicate": true,
  "tokens_charged": 1750,
  "cost_nanos": 1062500,
  "occurred_at": "2026-08-16T22:50:07.241685+00:00"
}

same event_id:      True
second is duplicate: True
```

And the ledger holds one row, not two:

```
$ GET /usage
HTTP 200
  "api_calls": { "used": 1, "limit": 1000, "remaining": 999 },
  "cost": { "events": 1, "metered_nanos": 1062500 }

events recorded: 1  (must be 1)
```

### A test proves double-counting cannot happen

`tests/test_metering.py` — five tests around the guarantee, run against the real
database because the guarantee *is* a database constraint:

| Test | Asserts |
|---|---|
| `test_same_key_records_exactly_one_event` | two calls, one row, `created` True then False |
| `test_retry_mirrors_the_original_result` | cost, quantity and timestamp are unchanged on retry |
| `test_different_keys_record_separate_events` | distinct keys are not merged |
| `test_same_key_for_different_work_is_rejected` | reuse for different work raises `IdempotencyConflict`, still one row |
| `test_same_key_across_tenants_is_independent` | the same key string in two tenants is two events |

The mechanism, from `src/app/repositories/usage_events.py`:

```sql
INSERT INTO usage_events (...) VALUES (...)
ON CONFLICT ON CONSTRAINT usage_events_tenant_idempotency_key DO NOTHING
RETURNING ...
```

with the constraint declared in `migrations/001_init.sql`:

```sql
CONSTRAINT usage_events_tenant_idempotency_key UNIQUE (tenant_id, idempotency_key)
```

A check-then-insert in application code cannot provide this: two concurrent retries
both find nothing and both insert. Uniqueness decided inside the database at write
time cannot be raced.

---

## Quotas

### Usage is checked against the plan; requests over the limit are rejected

The Free plan allows 100,000 tokens per month. A request taking usage to *exactly*
the limit succeeds; the next request, for a single token, is refused.

```
$ POST /generate  (100000 tokens, taking usage to exactly the limit)
HTTP 201
{ "idempotency_key": "probe-at-limit", "tokens_charged": 100000, "duplicate": false }

$ POST /generate  (one token more)
HTTP 429
{
  "error": "Monthly ai_tokens quota exceeded",
  "resource": "ai_tokens",
  "quota": 100000,
  "used": 100000,
  "requested": 1
}
```

A retry of the request that succeeded at the limit still returns its original result,
because the idempotency lookup runs before the quota check:

```
$ POST /generate  (retry of the request that succeeded at the limit)
HTTP 200
{ "idempotency_key": "probe-at-limit", "duplicate": true }
```

### Responses carry the correct status codes and explain why

`429` and `402` describe different failures and are never collapsed into one.

```
$ POST /generate  (subscription past_due, usage well under quota)
HTTP 402
{
  "error": "Subscription is not active; payment or upgrade required",
  "subscription_status": "past_due"
}
```

| Code | Condition | Client's next move |
|---|---|---|
| `429` | plan active, monthly allowance spent | wait for the reset, or upgrade |
| `402` | subscription `past_due` or `canceled` | fix payment |

Boundary behaviour is also covered by unit tests in `tests/test_quota.py`:
`test_reaching_the_limit_exactly_is_allowed`,
`test_exceeding_the_limit_by_one_is_refused`,
`test_a_single_request_larger_than_the_whole_quota_is_refused`,
`test_refused_request_records_no_event`,
`test_inactive_subscription_is_refused`.

---

## Cost calculation

### Monthly usage rolls up into a cost figure per tenant

```
$ GET /usage
HTTP 200
{
  "plan": "free",
  "period_start": "2026-08-01T00:00:00+00:00",
  "period_end": "2026-09-01T00:00:00+00:00",
  "api_calls":  { "used": 1, "limit": 1000, "remaining": 999 },
  "ai_tokens":  { "used": 1750, "limit": 100000, "remaining": 98250 },
  "cost": {
    "events": 1,
    "metered_nanos": 1062500,
    "metered_usd": "0.001062500",
    "metered_cents": 0,
    "plan_base_cents": 0,
    "total_cents": 0,
    "total_usd": "0.00"
  }
}
```

### Token pricing handles cached input, reasoning tokens and output correctly

The rollup agrees exactly with the pinned per-event arithmetic:

```
$ python scripts/acceptance.py   (probe 5)
expected nanos: 1062500
actual nanos:   1062500
match:          True
```

That figure decomposes as:

```
api call            1 x 100_000  =   100_000
input tokens     1000 x     300  =   300_000
cached input      500 x      75  =    37_500     cheaper than fresh input
output tokens     200 x    2500  =   500_000
reasoning tokens   50 x    2500  =   125_000     billed at the output rate
                                    ---------
                                     1_062_500 nano-dollars
```

### Pricing constants are pinned and covered by tests

`tests/test_pricing.py` asserts the rates themselves and the rules that follow from
them:

| Test | Asserts |
|---|---|
| `test_pinned_rates` | 300 / 75 / 2500 nanos per token, 100,000 per call |
| `test_cached_input_is_cheaper_than_fresh_input` | cached rate is a quarter of input, and strictly lower |
| `test_reasoning_tokens_are_billed_at_the_output_rate` | reasoning cost equals output cost for the same count |
| `test_categories_are_priced_separately_not_summed` | pricing the summed token count gives a different, wrong answer |
| `test_full_request_cost` | the complete request totals exactly 1,062,500 nanos |
| `test_cents_conversion_rounds_half_up_once` | rounding happens once, at the display boundary |

All money is stored as **integer nano-dollars** (`cost_nanos BIGINT`), never floats.
Each event also stores the `pricing_version` that produced its cost, so a later price
change cannot rewrite history.

---

## Stripe integration

### Webhooks verify signatures, ignore duplicates, and update plan and status

A forged signature is rejected and changes nothing:

```
$ POST /webhooks/stripe  (forged signature)
HTTP 400
{ "error": "Invalid webhook signature" }

$ GET /usage  (plan before any valid event)
plan unchanged by the forgery: free
```

A correctly signed event is applied, and replaying the identical event is ignored:

```
$ POST /webhooks/stripe  (valid signature)
HTTP 200
{ "received": true, "status": "upgraded to pro" }

$ POST /webhooks/stripe  (same event replayed)
HTTP 200
{ "received": true, "status": "duplicate ignored" }
```

The tenant's plan and limits changed as a result:

```
$ GET /usage  (plan after the verified event)
  "plan": "pro",
  "api_calls": { "used": 0, "limit": 50000 },
  "ai_tokens": { "used": 0, "limit": 5000000 },
  "cost": { "plan_base_cents": 2900, "total_usd": "29.00" }

plan now: pro   token limit now: 5000000
```

Deduplication is a primary key, not a lookup — `processed_webhook_events.event_id`:

```sql
INSERT INTO processed_webhook_events (event_id, event_type)
VALUES (%s, %s)
ON CONFLICT (event_id) DO NOTHING
RETURNING event_id
```

No row returned means the event was already handled.

Covered by `tests/test_webhooks.py`: `test_forged_signature_is_rejected`,
`test_missing_signature_header_is_rejected`, `test_tampered_payload_is_rejected`,
`test_valid_event_upgrades_the_tenant`, `test_replayed_event_is_processed_only_once`,
`test_subscription_deleted_downgrades_to_free`. Signatures in those tests are
generated with the same HMAC scheme Stripe uses and verified by Stripe's own
`construct_event`, so they need no network and no account.

### Subscription checkout works end-to-end in Stripe test mode

`POST /billing/checkout` creates a Checkout session in test mode and returns its URL.
Running the hosted Checkout page with card `4242 4242 4242 4242` produces a real
`checkout.session.completed` event, forwarded locally by `stripe listen`, which the
handler above verifies and applies.

Setup and the exact commands are in the README under *Stripe test mode*.

---

## Data model, tests and documentation

### The database holds tenants, plans, subscriptions and usage events, isolated per tenant

```
$ docker compose exec db psql -U billing -d billing -c "\dt"
                  List of relations
 Schema |           Name           | Type  |  Owner
 public | plans                    | table | billing
 public | processed_webhook_events | table | billing
 public | subscriptions            | table | billing
 public | tenants                  | table | billing
 public | usage_events             | table | billing
(5 rows)

$ docker compose exec db psql -U billing -d billing -c "SELECT code, api_call_quota, ai_token_quota FROM plans;"
 code | api_call_quota | ai_token_quota
------+----------------+----------------
 free |           1000 |         100000
 pro  |          50000 |        5000000
(2 rows)
```

Isolation is enforced at the boundary: the tenant is resolved from the SHA-256 hash of
the `X-API-Key` header and never from anything in the request body, so a client cannot
address a tenant it does not hold a key for. Every usage event, subscription and
rollup query is scoped by `tenant_id`, and
`test_same_key_across_tenants_is_independent` proves two tenants using the same
idempotency key do not collide.

An unknown key is refused before any work happens:

```
$ curl -i -X POST http://localhost:8000/generate -H "X-API-Key: wrong" ...
HTTP/1.1 401 Unauthorized
{"error":"Invalid API key"}
```

A missing idempotency key is refused too:

```
$ curl -i -X POST http://localhost:8000/generate -H "X-API-Key: <valid>" ...
HTTP/1.1 400 Bad Request
{"error":"Idempotency-Key header required"}
```

### Tests cover the scary cases

```
$ python -m pytest tests -q
33 passed
```

| Area | File | Count |
|---|---|---|
| Duplicate usage prevention | `tests/test_metering.py` | 5 |
| Quota boundaries and lapsed subscriptions | `tests/test_quota.py` | 8 |
| Cost calculation, pinned | `tests/test_pricing.py` | 9 |
| Rollup agreement with pricing | `tests/test_usage_rollup.py` | 5 |
| Invalid and duplicate webhooks | `tests/test_webhooks.py` | 6 |

### README, architecture diagram, setup instructions and submission pack

| File | Present |
|---|---|
| `README.md` — overview, architecture diagram, setup, API reference, limitations | yes |
| `capstone.yaml` — run, seed, test, base_url, endpoints | yes |
| `EVIDENCE.md` — this file | yes |
| `BUILDLOG.md` | yes |
| `.env.example` | yes |
| `DESIGN.md` — data model, idempotency strategy, money rules | yes |
