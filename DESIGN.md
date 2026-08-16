# Design

## Problem

A SaaS backend must answer three questions for every customer, at any moment, correctly:

1. **How much have they used?** — usage must be recorded exactly once, even when the client retries.
2. **What does it cost?** — money must be computed with exact arithmetic, not floating point.
3. **Have they hit their limit?** — quotas must be enforced *before* the billable work happens.

Getting any of these wrong has a direct financial consequence: double-charging a customer, giving
away unlimited access, or losing revenue silently.

---

## Layers

```
HTTP            src/app/api/          routing, request models, status codes
Services        src/app/services/     metering, quota, pricing, subscriptions
Repositories    src/app/repositories/ all SQL; the only layer that knows Postgres exists
Database        migrations/           schema as SQL
```

The HTTP layer never writes SQL and never calculates money. The service layer never raises
`HTTPException` — it returns results and raises domain errors, which the HTTP layer translates into
status codes. Swapping Postgres for another database, or FastAPI for another framework, touches one
layer each.

---

## Data model

### `plans`

Static reference data. Two rows.

| Column | Type | Notes |
|---|---|---|
| `code` | `text` PK | `free`, `pro` |
| `name` | `text` | display name |
| `api_call_quota` | `integer` | calls per calendar month |
| `ai_token_quota` | `bigint` | tokens per calendar month |
| `monthly_price_cents` | `integer` | base subscription price, integer cents |

| code | api_call_quota | ai_token_quota | monthly_price_cents |
|---|---|---|---|
| `free` | 1,000 | 100,000 | 0 |
| `pro` | 50,000 | 5,000,000 | 2,900 |

### `tenants`

One customer organisation. Every other row in the system belongs to exactly one tenant.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `name` | `text` | |
| `plan_code` | `text` FK → `plans.code` | the tenant's current plan |
| `subscription_status` | `text` | `active`, `past_due`, `canceled` |
| `api_key_hash` | `text` UNIQUE | SHA-256 of the tenant's API key; the raw key is never stored |
| `stripe_customer_id` | `text` UNIQUE NULL | set on first Checkout |
| `created_at` | `timestamptz` | |

### `subscriptions`

Mirrors Stripe. Stripe is the source of truth for payment state; this table is a local copy updated
only through signature-verified webhook events.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `tenant_id` | `uuid` FK → `tenants.id` | |
| `stripe_subscription_id` | `text` UNIQUE | |
| `plan_code` | `text` FK → `plans.code` | |
| `status` | `text` | mirrors Stripe's subscription status |
| `current_period_start` | `timestamptz` | |
| `current_period_end` | `timestamptz` | |
| `updated_at` | `timestamptz` | |

### `usage_events`

The ledger. Append-only: rows are never updated or deleted.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `tenant_id` | `uuid` FK → `tenants.id` | |
| `event_type` | `text` | `api_call` or `ai_tokens` |
| `quantity` | `bigint` | calls, or total tokens |
| `input_tokens` | `integer` | |
| `cached_input_tokens` | `integer` | |
| `output_tokens` | `integer` | |
| `reasoning_tokens` | `integer` | |
| `cost_nanos` | `bigint` | cost of this event, in nano-dollars |
| `pricing_version` | `text` | which price table produced `cost_nanos` |
| `idempotency_key` | `text` | client-supplied |
| `request_fingerprint` | `text` | hash of the request this key was first used for |
| `occurred_at` | `timestamptz` | |

**Constraints and indexes**

- `UNIQUE (tenant_id, idempotency_key)` — the mechanism that makes double-counting impossible.
- `INDEX (tenant_id, occurred_at)` — every rollup query filters on exactly this.

### `processed_webhook_events`

| Column | Type | Notes |
|---|---|---|
| `event_id` | `text` PK | Stripe's `evt_…` identifier |
| `event_type` | `text` | |
| `processed_at` | `timestamptz` | |

The primary key is the deduplication: inserting a Stripe event id that already exists fails, and a
failed insert means the event was already handled.

---

## Idempotency strategy

**The rule.** A billable request carries an `Idempotency-Key` header. The same key, from the same
tenant, records exactly one usage event no matter how many times the request arrives.

**The mechanism is a database constraint, not application code.** The naive approach — check whether
the key exists, then insert if it does not — contains a race: two concurrent retries both find
nothing and both insert. `UNIQUE (tenant_id, idempotency_key)` cannot be raced, because uniqueness
is decided inside the database at write time.

So the write path attempts the insert and treats a unique-violation as a successful no-op:

```
try INSERT usage_event
    -> succeeded: this is the first time; return the new event
    -> unique violation: this key was already used; SELECT the original and return that
```

**Order of operations.** The idempotency lookup happens *before* the quota check:

```
1. key already used?  -> return the original event immediately, unchanged
2. lock the tenant row
3. sum this month's usage
4. usage + requested > quota?  -> refuse, record nothing
5. compute cost
6. insert the usage event
7. commit
```

Step 1 comes first deliberately. If quota were checked first, a retry of a request that already
succeeded could be refused because the original had since pushed the tenant to its limit — the
client would see a failure for work that was in fact completed and billed. A retry must return the
original answer, not a new judgement.

Step 2 exists because steps 3 and 6 must not interleave with another request for the same tenant.
Without the row lock, two concurrent requests can both read a usage total below the quota and both
insert, taking the tenant over its limit. `SELECT … FOR UPDATE` on the tenant row serialises billable
writes per tenant while leaving other tenants unaffected.

**Key reuse with a different request.** A key is stored alongside a fingerprint of the request that
first used it. If the same key arrives describing different work, that is a client bug, not a retry:
the request is rejected with `409 Conflict` rather than silently returning an unrelated result.

---

## Quota enforcement

**The boundary rule.** Quotas are inclusive. A request whose usage brings the monthly total to
*exactly* the limit is allowed. The next request, which would exceed it, is refused.

```
used = 999, requested = 1, quota = 1000   ->  allowed   (total becomes 1000)
used = 1000, requested = 1, quota = 1000  ->  refused
```

**Which status code.** The two failures are different and are reported differently:

| Code | Meaning here | When |
|---|---|---|
| `429 Too Many Requests` | the plan is in good standing, but its monthly allowance is spent | `subscription_status = active` and usage would exceed quota |
| `402 Payment Required` | the plan itself does not currently entitle the tenant to service | `subscription_status` is `past_due` or `canceled` |

A `429` is temporary and resolves when the month rolls over or the tenant upgrades; the response
carries the quota, the current usage, and the reset time. A `402` requires the customer to do
something about payment. Collapsing both into one code would leave the client unable to tell
"wait or upgrade" from "your card failed".

**The usage window** is the current calendar month in UTC, derived from `occurred_at`. Billing
periods that follow the Stripe subscription cycle instead are a deliberate non-goal.

---

## Money arithmetic

**No floating point anywhere.** `0.1 + 0.2 != 0.3` in binary floating point, and a billing system
that accumulates that error across millions of events will not reconcile.

Costs are stored as **integer nano-dollars** (1 USD = 1,000,000,000 nanos) in `cost_nanos`.

Token prices are expressed as integer nanos per token, which makes every per-event calculation exact
integer multiplication with no rounding:

```
$0.30 per 1M input tokens   ->  300 nanos per token
$2.50 per 1M output tokens  -> 2500 nanos per token
```

Rounding happens once, at the very end, when a monthly total is converted to cents for display.
Per-event truncation is never performed, so no error accumulates.

### Token pricing rules

The four token categories are priced separately and are never summed into a single number before
pricing:

| Category | Rule |
|---|---|
| `input_tokens` | full input rate |
| `cached_input_tokens` | discounted input rate — the provider already held these, so they cost less |
| `output_tokens` | output rate, higher than input |
| `reasoning_tokens` | billed at the **output** rate; they are hidden generation, not a free category |

```
cost_nanos =  input_tokens         * input_nanos
            + cached_input_tokens  * cached_input_nanos
            + output_tokens        * output_nanos
            + reasoning_tokens     * output_nanos
```

Prices live in one configuration module, are tagged with a `pricing_version`, and are covered by
tests that pin exact expected totals. Each event stores the cost that was computed **at the time it
happened**, together with the pricing version that produced it, so a later price change never
rewrites history.

---

## API surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | none | liveness plus a database round trip |
| `POST` | `/generate` | `X-API-Key` | the billable action: meters usage, enforces quota, returns cost |
| `GET` | `/usage` | `X-API-Key` | monthly rollup: used, limit, cost |
| `POST` | `/billing/checkout` | `X-API-Key` | create a Stripe Checkout session for the Pro plan |
| `POST` | `/webhooks/stripe` | signature | subscription lifecycle events from Stripe |

**Authentication.** Every tenant-scoped request carries `X-API-Key`. The tenant is resolved from the
hash of that key and never from anything in the request body — a client cannot name a tenant it does
not hold a key for. Only the SHA-256 hash is stored.

**`POST /generate`**

```
X-API-Key: <tenant key>
Idempotency-Key: <client-generated unique value>

{ "input_tokens": 1200, "cached_input_tokens": 800,
  "output_tokens": 400, "reasoning_tokens": 150 }
```

| Response | Meaning |
|---|---|
| `201` | usage recorded |
| `200` | this idempotency key was already used; the original result is returned unchanged |
| `400` | malformed body, or a missing `Idempotency-Key` |
| `401` | missing or unknown API key |
| `402` | subscription not in good standing |
| `409` | idempotency key reused for a different request |
| `429` | monthly quota would be exceeded |

Errors share one shape:

```json
{ "error": "Monthly ai_tokens quota exceeded", "quota": 100000, "used": 100000 }
```

---

## Non-goals

Explicitly out of scope, so that the parts that are in scope can be correct:

- **No overage billing.** Usage beyond a quota is refused, never charged.
- **No invoicing or proration.** No monthly statements; a mid-cycle plan change takes effect
  immediately with no partial-period arithmetic.
- **No real payments.** Stripe test mode only.
- **No AI model calls.** Token counts arrive in the request body; the service meters numbers.
