# Build log

An honest account of how this project was built, including where AI assistance helped,
where it was wrong, and what I changed as a result.

## How AI was used

I used AI assistance heavily throughout this build — for the design document, the
database schema, the service and repository code, the test suite, and the documentation.
This was a deliberate choice rather than a shortcut: the capstone brief encourages AI-
assisted building, and the skill I wanted to practise was directing and reviewing that
work rather than typing every line of it.

My role was to set the direction and to verify everything. I decided the scope, chose
the stack, ran every command on my own machine, and checked each phase against its gate
before moving on. Nothing was committed that I had not run and seen working. The Stripe
account, product, price and CLI pairing were set up by hand in the dashboard.

I worked in phases, committing after each one, so the history shows the order the system
was actually built in: schema, then idempotent metering, then quotas, then cost, then
Stripe, then the submission pack.

## What AI assistance was good at

- **Boilerplate with a known shape.** The Docker Compose file, the Dockerfile, the
  repository functions and the FastAPI wiring were fast and mostly correct first time.
- **Test coverage.** The tests are more thorough than I would have written unprompted,
  particularly the boundary cases around quotas and the webhook signature tests that
  generate valid signatures locally instead of requiring a live Stripe account.
- **Explaining trade-offs.** The reasoning behind verifying tokens with the provider
  versus verifying locally against public keys, and the argument for integer money, were
  things I understood better after having them laid out than before.

## Where it was wrong, and what I changed

These are real failures from this build, in the order they happened.

**1. Postgres `SUM()` returns `Decimal`, not `int`.**
`GET /usage` returned `500 Internal Server Error` the moment a currency string was
formatted, because `SUM()` over a `BIGINT` column comes back as `numeric`, which psycopg
gives you as a `Decimal`, and `f"{value:09d}"` rejects it. Fixed by casting the row to
plain integers inside `month_totals()` in the repository, so every caller gets `int` and
the problem cannot recur elsewhere.

**2. `StripeObject` does not support `.get()`.**
Three webhook tests failed with `AttributeError: get`. `stripe.Webhook.construct_event()`
returns Stripe's own object type, not a dictionary, and the service layer assumed a
dictionary. Rather than patch the call sites, I changed the boundary: Stripe is now used
only to *verify* the signature, after which the already-verified raw bytes are parsed
with `json.loads`. That fixed the failures and also stopped a third-party type leaking
into the service layer, which fits the layering the rest of the project follows.

**3. The database migration silently did not run.**
The schema file is mounted into `/docker-entrypoint-initdb.d`, which Postgres executes
only on the first boot of an empty volume. Because the volume already existed from an
earlier phase, adding the migration appeared to do nothing. The fix was
`docker compose down -v` to drop the volume and rebuild. Worth knowing rather than
worth working around — the same behaviour is what makes reruns safe in normal use.

**4. Sub-cent costs displayed as `$0.00`.**
Technically correct — a single metered request costs about a tenth of a cent — but it
made the cost rollup look like nothing had been recorded. I added an exact
`metered_usd` field rendered with integer arithmetic, so small amounts are visible
without introducing floating point anywhere.

**5. A Definition-of-Done item had no evidence.**
The acceptance script proved `429` at the quota boundary but never exercised `402` for a
lapsed subscription, even though the code and a unit test both handled it. I added a
probe that sets a tenant to `past_due` and captures the response, because a claim
without a transcript is not evidence.

## What I would do differently

- **Verify the shape of every value that crosses a boundary.** Both of the bugs that
  cost me real time were type assumptions — a `Decimal` where an `int` was expected, and
  a library object where a `dict` was expected. Neither was a logic error.
- **Write the evidence file alongside the code, not after it.** Reconstructing which
  transcripts I needed at the end was slower than capturing them as each phase landed,
  which is why `scripts/acceptance.py` exists — it regenerates all of them on demand.

## What I would want to be asked about

The parts of this system I understand best, because they took the most thinking:

- Why idempotency is enforced by a `UNIQUE` constraint and `ON CONFLICT DO NOTHING`
  rather than a check-then-insert, and why the check-then-insert version is broken under
  concurrency.
- Why the idempotency lookup runs *before* the quota check, and what breaks if the order
  is reversed.
- Why `429` and `402` are different answers to different questions.
- Why every amount is an integer number of nano-dollars, and where the single rounding
  step lives.
