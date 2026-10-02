# Seat Reservation Service

A small JSON HTTP API that sells assigned seats for a show and stays correct
under an on-sale stampede: **no seat is ever sold twice, no user exceeds the
booking limit, and a retried request never double-books.**

FastAPI + asyncpg + PostgreSQL 16, containerised, deployed on Render.

| | |
|---|---|
| **Live URL** | `https://<your-service>.onrender.com` |
| **Health** | `GET /healthz` (liveness), `GET /readyz` (readiness, checks the DB, 503 if down) |
| **Metrics** | `GET /metrics` (Prometheus text format) |
| **API docs** | `GET /docs` (Swagger UI) |
| **Design write-up** | [WRITEUP.md](WRITEUP.md) |

> Free-tier note: a cold start can take ~30–60 s. Hit `/readyz` first.

## Behaviour at a glance

- **Identity** comes only from the bearer token (JWT `sub`). A `user_id` in a request body is ignored.
- **Money** is integer paise (`price_paise`, `amount_paise`). Floats are rejected.
- **Multi-seat requests are all-or-nothing.** `["A12","A13"]` with only A12 free
  → 409 `SEAT_TAKEN`, nothing is booked. This holds under concurrency.
- **Declines are 4xx, never 5xx:** `409 SEAT_TAKEN`, `409 PER_USER_LIMIT_EXCEEDED`,
  `409 IDEMPOTENCY_KEY_REUSED`, `429 SERVER_BUSY` (retryable load shedding).
- **Release model:** explicit `POST /reservations/{id}/cancel` (owner only, idempotent). There is no timed hold.
- Per-user limit defaults to 4 seats per show (`per_user_limit` at show creation).

## API

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /auth/token` `{"user_id": "alice"}` | none | Test-only token issuer |
| `POST /shows` | bearer | Create a show: `{"name","seats":[...],"price_paise","per_user_limit"?}` |
| `GET /shows/{id}` | none | Per-seat status + counts (`available + held + confirmed == total_seats`) |
| `POST /shows/{id}/reserve` | bearer | `{"seats":["A12"],"idempotency_key":"..."}` (key may be an `Idempotency-Key` header instead) |
| `POST /reservations/{id}/cancel` | bearer | Release a booking; only its owner may |
| `GET /healthz`, `GET /readyz`, `GET /metrics` | none | Ops |

Example:

```bash
BASE=https://<your-service>.onrender.com
TOKEN=$(curl -s -X POST $BASE/auth/token -H 'content-type: application/json' \
        -d '{"user_id":"alice"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

SHOW=$(curl -s -X POST $BASE/shows -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
        -d '{"name":"friday-night","seats":["A1","A2","A3"],"price_paise":25000}' \
        | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

curl -s -X POST $BASE/shows/$SHOW/reserve -H "authorization: Bearer $TOKEN" \
     -H 'content-type: application/json' -d '{"seats":["A1"],"idempotency_key":"k1"}'
```

## Run locally (clean checkout)

```bash
docker compose up --build        # app on http://localhost:8000, Postgres 16 included
```

The schema is applied automatically on startup (`app/db/schema/*.sql`, idempotent,
guarded by an advisory lock so several instances can start together).

Without Docker:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env             # set DATABASE_URL to a reachable Postgres
uvicorn app.main:app --reload
```

### Configuration (environment variables)

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | local Postgres | Render provides this |
| `JWT_SECRET` | dev value | **set a long random value in production** |
| `DB_POOL_MIN` / `DB_POOL_MAX` | 2 / 20 | keep modest on free Postgres |
| `DB_ACQUIRE_TIMEOUT_SECONDS` | 60 | pool wait before shedding with 429 |
| `READY_CHECK_TIMEOUT_SECONDS` | 2 | readiness probe timeout |
| `METRICS_MAX_SHOWS` | 50 | shows exported in the `seats_available` gauge |
| `LOG_LEVEL` | INFO | |

## One-command burst

Reproduces the on-sale stampede against any running instance (live or local).
Needs only Python 3.9+ (standard library, no installs).

```bash
./burst.sh https://<your-service>.onrender.com                      # default: ~5,000-request stampede
./burst.sh https://<your-service>.onrender.com --requests 20000     # full-size run
python3 burst.py --help                                             # all knobs
```

It creates its own fresh shows and runs five scenarios:

1. **Hot-seat storm**: 5 hot seats × 200 distinct users hitting the same seat at once → exactly one 201 per seat, every loser a clean 409.
2. **Stampede**: N mixed single/multi-seat requests over a 5,000-seat show, about half aimed at 20 "good" seats, plus exact retries (same key) and same-key-different-seats requests. The reconciliation invariant is **sampled during the burst** and re-checked after.
3. **Per-user limit**: one user fires 10 parallel reserves on a limit=4 show → at most 4 confirmed.
4. **Identity**: spoofed body `user_id` is ignored; another user cannot cancel your booking; a released seat is re-bookable; a second cancel never frees a re-booked seat.
5. **Idempotency**: same key + same seats → original reservation; same key + other seats → 409.

It prints the outcome distribution (`201 confirmed`, `201 idempotent_replay`, `409 SEAT_TAKEN`, `409 PER_USER_LIMIT_EXCEEDED`, …, 5xx), the final reconciliation, and compares `/metrics` counter deltas with what it observed. Exit code is `0` only if every check passes.

Sample of the output shape:

```
=== OUTCOME DISTRIBUTION ===
      2873  409 SEAT_TAKEN
      1248  201 confirmed
       135  201 idempotent_replay
       ...
   [PASS] zero 5xx across the whole run (got 0)
   [PASS] final invariant: 3691 + 0 + 1309 == 5000
=== RESULT: 31/31 checks passed ===
```

Run it against a quiet service: the metrics comparison assumes nobody else is hitting it.
Free-tier hosts are slow under 20k requests; if you see timeouts, lower `--workers` or `--requests`.

## Observability

**Metrics**: `GET /metrics`

| Metric | Type | Meaning |
|---|---|---|
| `reservations_confirmed_total` | counter | new bookings committed (HTTP 201, not a replay) |
| `reservations_declined_total{reason}` | counter | `seat_taken`, `per_user_limit`, `idempotent_replay`, `idempotency_key_reused` |
| `seats_available{show_id}` | gauge | available seats per show, read from the DB at scrape time |

For a show: `total_seats - seats_available` equals `confirmed + held` from `GET /shows/{id}`.
Counters are per process and reset on restart. The `*_created` series are
Prometheus client bookkeeping (start timestamps), not business metrics.

```bash
curl -s https://<your-service>.onrender.com/metrics | grep -E '^(reservations|seats)'
```

## Project layout

```
app/
  main.py                 app factory, lifespan (pool + migrations)
  config.py               env-driven settings
  errors.py               unified error model (4xx domain outcomes vs 5xx)
  auth/                   JWT issue/verify, get_current_user dependency
  api/routes/             thin HTTP layer: auth, shows, reservations, health, metrics
  backend/                business logic; services own the transactions
  db/operations/          SQL only (conn in, rows out)
  db/schema/              idempotent SQL migrations
  observability/metrics.py  Prometheus counters/gauge
burst.py / burst.sh   the stampede
Dockerfile / docker-compose.yml
```
