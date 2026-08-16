# Usage Metering & Billing Engine

A backend service that answers the three questions every SaaS product must answer:
**how much has this customer used, what does it cost, and have they hit their limit?**

Built with FastAPI and PostgreSQL, with Stripe (test mode) for subscription management.

> FlyRank Internship · Backend Development Track · Capstone

---

## What it does

| Concern | Behaviour |
|---|---|
| **Usage metering** | Every billable action records a usage event against a tenant. The same request retried with the same idempotency key records **exactly one** event. |
| **Quota enforcement** | Usage is checked against the tenant's plan *before* the action is allowed. Requests over the limit are refused with an honest status code and a message explaining why. |
| **Cost calculation** | Monthly usage rolls up into a cost per tenant, including AI token pricing rules where categories are priced separately. |
| **Subscriptions** | Stripe Checkout in test mode, with signature-verified, deduplicated webhooks keeping each tenant's plan in sync. |

## Plans

| Plan | API calls / month | AI tokens / month |
|---|---|---|
| Free | 1,000 | 100,000 |
| Pro | 50,000 | 5,000,000 |

---

## Status

Under active development. Setup instructions, architecture diagram, API reference and
evidence are added as each phase lands.

## Licence

MIT — see [LICENSE](LICENSE).
