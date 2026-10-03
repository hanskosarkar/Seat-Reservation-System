# Write-up

Stack: FastAPI + asyncpg, PostgreSQL 16, one uvicorn worker, Render.
Everything below describes what the code in this repo actually does.

## 1. The atomic decision

**Mechanism:** one Postgres transaction per reservation (`reservation_service.py`),
in which the decision is made by **row locks on the seat rows plus a conditional update**:

1. `SELECT … FROM seats WHERE show_id=$1 AND seat_label = ANY($2) ORDER BY seat_label FOR UPDATE`
   takes a row lock on every requested seat.
2. We verify every seat exists and has `status = 'available'`; otherwise we raise `SeatTaken` and the transaction rolls back (409).
3. `UPDATE seats SET status='confirmed', reservation_id=…, user_id=… WHERE … AND status='available'`
   is guarded on current state, and the affected row count must equal the number of seats requested or the whole transaction aborts.

A seat is also a unique row by construction: `PRIMARY KEY (show_id, seat_label)`.

**Why it is race-free:** 500 requests for A12 all reach step 1, and Postgres lets exactly one hold the row lock at a time. The other 499 block there until the winner commits, then read the committed row (`confirmed`) and decline with 409. There is no read-then-write gap, because the read *is* the lock acquisition, and the `status='available'` guard in the UPDATE is a second line of defence. A cancel releases seats with `WHERE reservation_id = <this reservation>`, so a release can never take or resurrect a seat that now belongs to someone else.

**Multi-seat and deadlock:** requests are **all-or-nothing** (documented decision): if any requested seat is taken, nothing is booked. Every code path that locks several seats (reserve and cancel) goes through the same function, `lock_seats_ordered`, which locks in `ORDER BY seat_label`. All transactions therefore acquire seat locks in the same global order, so two overlapping requests (`[A1,A2]` vs `[A2,A1]`) cannot wait on each other in a cycle. A per-(show, user) advisory lock is always taken *before* any seat lock in both reserve and cancel, and a transaction never takes a second user lock, so the user-lock layer cannot form a cycle with the seat-lock layer either.

**Per-user limit:** `pg_advisory_xact_lock(hashtextextended('res:<show>:<user>'))` serialises all of one user's reserve/cancel calls for a show, so `count held → compare → insert` is safe. 10 parallel reserves from one user on a limit-4 show are processed one at a time and end with exactly 4. Other users are unaffected. (A hash collision between two users' lock keys would only cause extra waiting, never a wrong result.)

## 2. Idempotency

- **Where the key lives:** the `reservations` table, columns `idempotency_key` and `request_hash` (SHA-256 of the *sorted* seat list, so seat order doesn't matter), with `UNIQUE (show_id, user_id, idempotency_key)` (migration `002`). The key is read from the `Idempotency-Key` header or the body; if both are present and differ → 422.
- **Exactly once:** under the user advisory lock we look the key up first. Found with the same hash → return the **original** reservation (HTTP 201 plus an `Idempotent-Replay: true` header; nothing is written). Not found → proceed. The unique constraint is the backstop: even if the lock were bypassed, a second insert fails rather than creating a second booking.
- **Same key, different body:** stored hash ≠ new hash → `409 IDEMPOTENCY_KEY_REUSED`.
- **Scope:** per (show, user, key). Identity comes from the token, so one user can never replay or collide with another user's key.
- **Known limits:** declined requests roll back, so their key is *not* recorded. A retry of a request that got `409 SEAT_TAKEN` is evaluated afresh. A replay returns the stored reservation even if it was cancelled since (its `status` then reads `cancelled`).

## 3. Holds and expiry

Chosen model: **explicit release**, no timed holds. A booking is `confirmed` immediately, and `POST /reservations/{id}/cancel` releases it. The `held` seat state exists in the schema and in the invariant (`available + held + confirmed == total_seats`) but the write path doesn't use it, because there is no payment step that would justify a hold.
Cancel is owner-only (403 otherwise), idempotent (a second cancel touches no seat), and releases only seats still owned by that reservation, so it can never free a seat re-booked by someone else. Released seats are immediately re-bookable.
The reconciliation invariant is computed from the same rows that are listed, inside a single `REPEATABLE READ` read-only snapshot in `GET /shows/{id}`, so it cannot be observed half-way through a booking.

## 4. Consistency vs availability under a partition

This is a **CP** design: one Postgres primary is the single system of record, and we choose consistency over availability.
- If the app cannot reach the database, `/readyz` fails closed (503) and requests do not succeed. We never "accept now, reconcile later", because that is how a seat gets sold twice.
- Under overload, requests queue for a pooled connection; if the wait or a statement exceeds its timeout the client gets a retryable `429 SERVER_BUSY` (with `Retry-After`) instead of a 5xx.
- A connection lost *during* commit leaves the outcome unknown to the client; the retry with the same idempotency key is what resolves it safely (it returns the original booking, or books once).
- Gaps: it is a single DB instance with no replica or failover, and some unexpected DB errors (e.g. connection reset mid-query) would surface as 500 via the catch-all handler rather than a domain 4xx. Multi-region would need a single-writer region.

## 5. Observability: what would page me at 2am

Implemented: `reservations_confirmed_total`, `reservations_declined_total{reason}`, `seats_available{show_id}`. `burst.py` cross-checks these metric deltas against observed responses.

Pages (the ones that mean "wrong or down", not "busy"):
1. **`/readyz` failing** for >1 min: the database is unreachable and we are fail-closed.
2. **Any 5xx.** The design promises none; every 5xx is a bug or an infrastructure fault.
3. **Sustained `429 SERVER_BUSY`** (pool saturation): the service is shedding real customers.
4. **Reconciliation drift:** `total_seats − seats_available` ≠ `confirmed + held` from the API, or `seats_available` going *up* with no cancels. Either means an invariant is broken: the pager-worthy event for this system.

### 6. AI Usage

I used AI as an implementation assistant. I made the design decisions and directed the implementation.

**Decided and directed by me**

- **Repository structure:** Modular separation of routes, business logic, and database operations.
- **Double-sell prevention:** Row-level locking on seat rows as the atomic decision point.
- **Observability:** Confirmed reservations, declines by reason, and available seats.

**Implemented with AI assistance**

- **Row locking:** I chose the approach; AI helped implement the locking logic.
- **Idempotency:** I chose request fingerprinting using a hash of the seat list. AI helped implement the database constraint, replay handling, and key-conflict logic.
- **Observability:** AI implemented the Prometheus metrics and `/metrics` endpoint based on my specification.

**What I verified**

- I tested the deployed service on Render and verified the expected behavior, including concurrent requests.

---

### 7. What I Would Do Next

**1. Time-bound seat holds**

Move from immediate confirmation to a two-step flow where seats are held for a fixed period, such as 10 minutes. Expired holds would be automatically released. The existing schema already supports a `held` state.

**2. Payment gateway integration**

Payment confirmation would move a seat from `held` to `confirmed`. Failed or expired payments would release the seat. Payment callbacks would use guarded state transitions and idempotency to handle expiry races and repeated notifications safely.

**3. Confirmed booking cancellation**

Confirmed bookings would continue to be cancellable only by their owner. In the payment-enabled flow, cancellation would also trigger the appropriate refund through the payment gateway.