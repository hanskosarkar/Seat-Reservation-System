"""Prometheus metrics for the reservation service.

Exposed series
--------------
reservations_confirmed_total                 counter
    New reservations committed (HTTP 201, not a replay).
reservations_declined_total{reason}          counter
    seat_taken             409 SEAT_TAKEN
    per_user_limit         409 PER_USER_LIMIT_EXCEEDED
    idempotent_replay      201 + `Idempotent-Replay: true` (same key, same
                           seats: the ORIGINAL reservation is returned and
                           nothing new is booked)
    idempotency_key_reused 409 IDEMPOTENCY_KEY_REUSED (same key, other seats)
seats_available{show_id}                     gauge
    Seats currently 'available', read from the database at scrape time, so it
    reconciles with GET /shows/{id}.

Design: the existing services/routes are NOT modified. `instrument_reservations`
wraps `reservation_service.reserve` and counts after the service returns (the
transaction has already committed) or raises (it has rolled back), so a counter
only moves for an outcome that really happened.
"""
import asyncio
import functools
import logging
import os

from prometheus_client import Counter, Gauge

from app.backend import reservation_service
from app.errors import IdempotencyKeyReused, PerUserLimitExceeded, SeatTaken

logger = logging.getLogger("app.metrics")

# Gauge cardinality guard: only the N most recently created shows are exported.
METRICS_MAX_SHOWS: int = int(os.environ.get("METRICS_MAX_SHOWS", "50"))
# The scrape must never hang behind a saturated pool during a burst.
METRICS_DB_TIMEOUT_SECONDS: float = float(
    os.environ.get("METRICS_DB_TIMEOUT_SECONDS", "2")
)

RESERVATIONS_CONFIRMED = Counter(
    "reservations_confirmed",
    "Reservations confirmed (new bookings; idempotent replays excluded).",
)

RESERVATIONS_DECLINED = Counter(
    "reservations_declined",
    "Reservation requests that did not create a new booking, by reason.",
    ["reason"],
)

SEATS_AVAILABLE = Gauge(
    "seats_available",
    "Seats currently available, per show.",
    ["show_id"],
)

DECLINE_REASONS = (
    "seat_taken",
    "per_user_limit",
    "idempotent_replay",
    "idempotency_key_reused",
)

for _reason in DECLINE_REASONS:  # export every series at 0 from the start
    RESERVATIONS_DECLINED.labels(reason=_reason)


def instrument_reservations() -> None:
    """Wrap reservation_service.reserve with outcome counters (idempotent)."""
    original = reservation_service.reserve
    if getattr(original, "_metrics_wrapped", False):
        return

    @functools.wraps(original)
    async def reserve_with_metrics(*args, **kwargs):
        try:
            result, replayed = await original(*args, **kwargs)
        except SeatTaken:
            RESERVATIONS_DECLINED.labels(reason="seat_taken").inc()
            raise
        except PerUserLimitExceeded:
            RESERVATIONS_DECLINED.labels(reason="per_user_limit").inc()
            raise
        except IdempotencyKeyReused:
            RESERVATIONS_DECLINED.labels(reason="idempotency_key_reused").inc()
            raise
        if replayed:
            RESERVATIONS_DECLINED.labels(reason="idempotent_replay").inc()
        else:
            RESERVATIONS_CONFIRMED.inc()
        return result, replayed

    reserve_with_metrics._metrics_wrapped = True  # type: ignore[attr-defined]
    reservation_service.reserve = reserve_with_metrics


_AVAILABLE_SQL = """
SELECT s.show_id::text AS show_id,
       count(*) FILTER (WHERE s.status = 'available') AS available
FROM seats s
WHERE s.show_id IN (
    SELECT id FROM shows ORDER BY created_at DESC LIMIT $1
)
GROUP BY s.show_id
"""


async def _fetch_available(pool) -> list:
    async with pool.acquire() as conn:
        return await conn.fetch(_AVAILABLE_SQL, METRICS_MAX_SHOWS)


async def refresh_seats_available(pool) -> None:
    """Re-read the seats_available gauge from the database.

    One statement = one consistent snapshot. On failure the previous values
    are kept (and the other metrics are still served) rather than failing the
    scrape.
    """
    if pool is None:
        return
    try:
        rows = await asyncio.wait_for(
            _fetch_available(pool), timeout=METRICS_DB_TIMEOUT_SECONDS
        )
    except Exception as exc:
        logger.warning(f"metrics_seats_refresh_failed err={exc!r}")
        return
    # no await below: clear + set happen atomically w.r.t. other scrapes
    SEATS_AVAILABLE.clear()
    for row in rows:
        SEATS_AVAILABLE.labels(show_id=row["show_id"]).set(row["available"])
