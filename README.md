# Usage Metering & Billing Engine

A backend service that answers the three questions every SaaS product must answer:
**how much has this customer used, what does it cost, and have they hit their limit?**

Built with FastAPI and PostgreSQL, with Stripe (test mode) for subscription management.
The whole stack starts with one command.

> FlyRank Internship · Backend Development Track · Capstone

---

## What it guarantees

| Guarantee | How |
|---|---|
| A retried request is billed **once** | `UNIQUE (tenant_id, idempotency_key)` enforced by the database, not by application code |
| Quotas are exact at the boundary | reaching the limit is allowed, exceeding it is refused, and the refusal explains itself |
| Money never drifts | integer nano-dollars end to end; rounding happens once, at display |
| Payment state cannot be forged | webhook signatures verified against the raw request body before anything is read |
| A replayed webhook changes nothing twice | Stripe event ids are a primary key |

---

## Architecture

```
                     ┌──────────────────────────────┐
   Client ──HTTP──▶  │  src/app/api/                │  routing · auth · status codes
                     │  routes_usage · routes_stripe│
                     └───────────────┬──────────────┘
                                     │  domain calls, domain errors
                     ┌───────────────▼──────────────┐
                     │  src/app/services/           │  meter · quota · pricing
                     │                              │  subscriptions · usage
                     └───────────────┬──────────────┘
                                     │  the only layer that writes SQL
                     ┌───────────────▼──────────────┐
                     │  src/app/repositories/       │  tenants · usage_events
                     │                              │  subscriptions
                     └───────────────┬──────────────┘
                                     ▼
                     ┌──────────────────────────────┐
                     │  PostgreSQL 16 (Docker)      │  schema in migrations/
                     │  volume: billing_data        │
                     └──────────────────────────────┘

   Stripe ──signed webhook──▶ /webhooks/stripe
                                 ├─ verify signature   (forged → 400)
                                 ├─ claim event id     (replay → ignored)
                                 └─ update tenant plan and status
```

The HTTP layer writes no SQL and calculates no money. The service layer raises domain
errors (`QuotaExceeded`, `SubscriptionInactive`, `IdempotencyConflict`) which the HTTP
layer maps to status codes, so nothing below the API knows what HTTP is.

| Path | Responsibility |
|---|---|
| `src/app/api/` | routes, request and response models, auth dependencies |
| `src/app/services/` | metering, quota rules, pricing, subscription sync, rollups |
| `src/app/repositories/` | every SQL statement in the project |
| `migrations/001_init.sql` | schema and plan seed data, run once on first database boot |
| `scripts/seed.py` | demo tenants and their API keys |
| `scripts/acceptance.py` | runs the acceptance probes and prints a transcript |
| `tests/` | 33 tests |

---

## Quick start

**Requirements:** Docker Desktop. Nothing else.

```bash
git clone https://github.com/MTahaR-dev/flyrank-capstone-metering-billing.git
cd flyrank-capstone-metering-billing

copy .env.example .env        # Windows   (cp .env.example .env elsewhere)

docker compose up --build
```

The database creates itself, applies `migrations/001_init.sql`, and inserts the Free and
Pro plans. The API waits for the database's health check before starting.

```bash
python scripts/seed.py        # creates demo tenants, prints their API keys
```

Store the printed keys — only their SHA-256 hashes are kept, so they cannot be recovered.

- API: **http://localhost:8000**
- Interactive docs: **http://localhost:8000/docs**
- Health: **http://localhost:8000/health** — returns `503` if the database round trip fails

### Verify

```bash
python -m pytest tests -q      # 33 tests
python scripts/acceptance.py   # the acceptance probes, end to end
```

---

## Plans

| Plan | API calls / month | AI tokens / month | Base price |
|---|---|---|---|
| Free | 1,000 | 100,000 | $0.00 |
| Pro | 50,000 | 5,000,000 | $29.00 |

Quotas reset with the calendar month, in UTC.

---

## API

Every tenant-scoped request carries `X-API-Key`. The tenant is resolved from the hash of
that key and never from the request body.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | none | liveness plus a database round trip |
| `POST` | `/generate` | `X-API-Key` + `Idempotency-Key` | the billable action |
| `GET` | `/usage` | `X-API-Key` | this month's usage, limits and cost |
| `POST` | `/billing/checkout` | `X-API-Key` | create a Stripe Checkout session |
| `POST` | `/webhooks/stripe` | `Stripe-Signature` | subscription lifecycle events |

### `POST /generate`

```bash
curl -i -X POST http://localhost:8000/generate \
  -H "X-API-Key: sk_demo_..." \
  -H "Idempotency-Key: any-unique-value" \
  -H "Content-Type: application/json" \
  -d '{"input_tokens":1000,"cached_input_tokens":500,"output_tokens":200,"reasoning_tokens":50}'
```

```json
{
  "event_id": "913716f2-946f-434a-a0f6-5f52e6e42d8a",
  "idempotency_key": "any-unique-value",
  "duplicate": false,
  "quantity": 1,
  "tokens_charged": 1750,
  "cost_nanos": 1062500,
  "cost_usd": "0.0010625",
  "pricing_version": "2026-08-01"
}
```

| Status | Meaning |
|---|---|
| `201` | usage recorded |
| `200` | this idempotency key was already used; the original result is returned unchanged |
| `400` | malformed body, or a missing `Idempotency-Key` |
| `401` | missing or unknown API key |
| `402` | subscription is `past_due` or `canceled` |
| `409` | idempotency key reused for a different request |
| `429` | monthly quota would be exceeded |

Errors share one shape:

```json
{ "error": "Monthly ai_tokens quota exceeded", "quota": 100000, "used": 100000, "requested": 1 }
```

### `GET /usage`

```json
{
  "plan": "free",
  "period_start": "2026-08-01T00:00:00+00:00",
  "period_end": "2026-09-01T00:00:00+00:00",
  "api_calls": { "used": 1, "limit": 1000, "remaining": 999 },
  "ai_tokens": { "used": 1750, "limit": 100000, "remaining": 98250 },
  "cost": {
    "events": 1,
    "metered_nanos": 1062500,
    "metered_usd": "0.001062500",
    "plan_base_cents": 0,
    "total_cents": 0,
    "total_usd": "0.00"
  }
}
```

---

## How the guarantees work

### Exactly-once metering

The write path attempts the insert and lets the database referee it:

```sql
INSERT INTO usage_events (...) VALUES (...)
ON CONFLICT ON CONSTRAINT usage_events_tenant_idempotency_key DO NOTHING
RETURNING ...
```

Zero rows returned means the key was already used, so the original event is loaded and
returned instead. A check-then-insert in Python cannot provide this: two concurrent
retries both find nothing and both insert.

The idempotency lookup happens **before** the quota check. If quota came first, a retry
of a request that already succeeded could be refused, because the original had since
pushed the tenant to its limit — the client would see a failure for work that was in
fact completed and billed.

A `SELECT … FOR UPDATE` on the tenant row serialises billable writes for that tenant, so
two concurrent requests cannot both read a usage total below the quota and both insert.
Other tenants are unaffected.

### Money

Costs are integer **nano-dollars** (1 USD = 1,000,000,000 nanos). Token prices are
integers *per token*, so every per-event calculation is exact integer multiplication
with no rounding:

| Category | Nanos per token | Rule |
|---|---|---|
| input | 300 | full rate |
| cached input | 75 | discounted — the provider already held these |
| output | 2500 | higher than input |
| reasoning | 2500 | billed as output, not as a free category |

Categories are priced separately and never summed before pricing. Rounding to cents
happens once, at display. Every event records the `pricing_version` used, so changing a
price never rewrites history.

### Webhooks

```python
payload = await request.body()          # raw bytes, before anything parses them
stripe.Webhook.construct_event(payload, signature, secret)
```

Stripe signs the exact bytes it sent. Parsing the JSON first and re-serialising it
shifts whitespace and key order, and the signature will never match. After verification
the payload is parsed into a plain dict, so no Stripe type reaches the service layer.

Deduplication is a primary key on `processed_webhook_events.event_id` — inserting an id
that already exists returns no row, which means the event was already handled.

---

## Stripe test mode

No real money and no card is ever involved. Test cards such as `4242 4242 4242 4242`
work with any future expiry.

1. Create a free Stripe account and stay in **test mode**.
2. Create a recurring product named *Pro* at $29.00/month and copy its **price ID**.
3. Copy the **test secret key** from Developers → API keys.
4. Install the Stripe CLI and run:

```bash
stripe login
stripe listen --forward-to localhost:8000/webhooks/stripe
```

The CLI prints a `whsec_…` signing secret. Put all three values in `.env`:

```
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRO_PRICE_ID=price_...
```

Restart the app, then start a Checkout session:

```bash
curl -X POST http://localhost:8000/billing/checkout -H "X-API-Key: sk_demo_..."
```

Open the returned `checkout_url`, pay with the test card, and the CLI forwards
`checkout.session.completed` to the webhook. `GET /usage` will show the tenant on the
Pro plan with the higher limits.

Events can also be replayed without the browser:

```bash
stripe trigger checkout.session.completed
```

`.env` is git-ignored. `.env.example` documents every variable with placeholder values.

---

## Configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string. `localhost` when running the app directly; docker-compose overrides it to `db` |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | used by the database container on first boot |
| `STRIPE_SECRET_KEY` | test-mode secret key |
| `STRIPE_WEBHOOK_SECRET` | signing secret from `stripe listen` |
| `STRIPE_PRO_PRICE_ID` | price ID of the Pro subscription |
| `CHECKOUT_SUCCESS_URL` / `CHECKOUT_CANCEL_URL` | where Stripe returns the customer |

---

## Useful commands

| Command | What it does |
|---|---|
| `docker compose up --build` | start the whole stack |
| `docker compose down` | stop and remove containers, keep the data |
| `docker compose down -v` | also delete the volume — destroys all data |
| `docker compose logs -f app` | follow application logs |
| `docker compose exec db psql -U billing -d billing` | a SQL shell inside the database |
| `python scripts/seed.py` | create demo tenants |
| `python scripts/acceptance.py` | run the acceptance probes |
| `python -m pytest tests -q` | run the test suite |

---

## Limitations

**No overage billing, invoicing or proration.** Usage past a quota is refused, never
charged. There are no monthly statements, and a mid-cycle plan change takes effect
immediately with no partial-period arithmetic. These were left out deliberately so the
parts that are implemented could be correct; proration in particular is subtle enough to
deserve its own build.

**Billing periods are calendar months, not Stripe subscription cycles.** A tenant that
upgrades on the 20th gets the Pro quota for the remainder of that calendar month rather
than a period aligned to their Stripe billing anniversary. Aligning the two would mean
reading `current_period_start` and `current_period_end` from the subscription mirror —
the columns exist, the rollup does not yet use them.

**Quota reads are serialised per tenant, which caps write throughput for a single
tenant.** `SELECT … FOR UPDATE` is the simplest correct answer for a quota check that
must not race. A high-volume tenant would need a different approach — a reservation
counter or an approximate check with periodic reconciliation.

**The cache of plan limits is the plans table itself, read on every billable request.**
That is one extra join per call. Fine at this scale, worth caching at a larger one.

**Webhook processing is synchronous.** The handler verifies, deduplicates and applies
the event inside the request. Stripe retries on non-2xx, so a failure is not lost, but a
slow database would slow the webhook response. Moving the apply step to a background job
with the same deduplication key is the natural next step.

---

## Licence

MIT — see [LICENSE](LICENSE).
