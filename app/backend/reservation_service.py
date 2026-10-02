"""Business logic for reservations. Owns the transaction.

Behaviour for a request for several seats is ALL-OR-NOTHING: either every
requested seat is confirmed to the user, or none is and the request is
declined with a 4xx. Never a partial booking.

Inside ONE transaction, in this fixed order:
  1. read the show (price, per-user limit)
  2. take the (show, user) advisory lock   -> serialises this user's requests
  3. idempotency lookup                     -> replay / key-reuse conflict
  4. per-user limit check                   -> safe because of step 2
  5. row-lock the seats in sorted order     -> deterministic: no deadlock
  6. verify every seat exists and is available
  7. insert the reservation, conditionally update the seats
Any decline raises, which rolls everything back.
"""
import hashlib
import json
import logging
from uuid import UUID, uuid4

import asyncpg

from app.db.models.reservation import Reservation
from app.db.operations import reservation_ops, seat_ops, show_ops
from app.db.pool import connection
from app.errors import (
    IdempotencyKeyReused,
    NotFound,
    PerUserLimitExceeded,
    SeatNotFound,
    SeatTaken,
)

logger = logging.getLogger("app.reservations")


def request_hash(seats: list[str]) -> str:
    """Stable fingerprint of the requested seat SET (order-independent)."""
    return hashlib.sha256(json.dumps(sorted(seats)).encode()).hexdigest()


def reservation_to_dict(r: Reservation) -> dict:
    return {
        "reservation_id": str(r.id),
        "show_id": str(r.show_id),
        "user_id": r.user_id,
        "seats": r.seats,
        "amount_paise": r.amount_paise,
        "status": r.status,
    }


async def reserve(
    pool: asyncpg.Pool,
    show_id: UUID,
    user_id: str,
    seats: list[str],
    idempotency_key: str,
) -> tuple[dict, bool]:
    """Returns (reservation, replayed). `replayed` is True when this was a
    retry of an earlier request and the ORIGINAL reservation is returned."""
    try:
        from app.main import REQUEST_ID
        request_id = REQUEST_ID.get() or "-"
    except Exception:
        request_id = "-"

    logger.info(
        f"reserve.start request_id={request_id} show_id={show_id} user_id={user_id} seats={seats} idempotency_key={idempotency_key}"
    )
    async with connection(pool) as conn:
        async with conn.transaction():
            reservation, replayed = await _reserve_in_transaction(
                conn, show_id, user_id, seats, idempotency_key
            )
    if not replayed:
        logger.info(
            f"reservation_confirmed request_id={request_id} show_id={show_id} user_id={user_id} "
            f"seats={seats} reservation_id={reservation.id}"
        )
    logger.info(
        f"reserve.end request_id={request_id} show_id={show_id} user_id={user_id} replayed={replayed}"
    )
    return reservation_to_dict(reservation), replayed


async def _reserve_in_transaction(
    conn: asyncpg.Connection,
    show_id: UUID,
    user_id: str,
    seats: list[str],
    idempotency_key: str,
) -> tuple[Reservation, bool]:
    try:
        from app.main import REQUEST_ID
        request_id = REQUEST_ID.get() or "-"
    except Exception:
        request_id = "-"

    logger.info(
        f"_reserve_in_transaction.start request_id={request_id} show_id={show_id} user_id={user_id} seats={seats} idempotency_key={idempotency_key}"
    )
    # 1. show
    show = await show_ops.get_show(conn, show_id)
    if show is None:
        raise NotFound("show not found", {"show_id": str(show_id)})

    # 2. serialise this user's requests for this show
    await reservation_ops.lock_user_scope(conn, show_id, user_id)

    # 3. idempotency: same key -> replay, or conflict if the seats differ
    fingerprint = request_hash(seats)
    existing = await reservation_ops.get_by_idempotency_key(
        conn, show_id, user_id, idempotency_key
    )
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise IdempotencyKeyReused(
                "idempotency key was already used with different seats",
                {"idempotency_key": idempotency_key},
            )
        logger.info(
            f"_reserve_in_transaction.end request_id={request_id} show_id={show_id} user_id={user_id} replayed=True reservation_id={existing.id}"
        )
        return existing, True

    # 4. per-user limit (the advisory lock makes count-then-insert safe)
    held = await seat_ops.count_user_seats(conn, show_id, user_id)
    if held + len(seats) > show.per_user_limit:
        raise PerUserLimitExceeded(
            "per-user seat limit exceeded",
            {
                "limit": show.per_user_limit,
                "currently_held": held,
                "requested": len(seats),
            },
        )

    # 5 + 6. lock in deterministic order, then verify
    locked = await seat_ops.lock_seats_ordered(conn, show_id, seats)
    by_label = {s.seat_label: s for s in locked}

    missing = sorted(s for s in seats if s not in by_label)
    if missing:
        raise SeatNotFound("seat does not exist in this show", {"seats": missing})

    taken = sorted(s for s in seats if by_label[s].status != "available")
    if taken:
        raise SeatTaken("seat already taken", {"seats": taken})

    # 7. record + conditional update; rowcount must match or abort everything
    reservation_id = uuid4()
    amount_paise = show.price_paise * len(seats)  # integer paise
    try:
        reservation = await reservation_ops.insert_reservation(
            conn, reservation_id, show_id, user_id, idempotency_key,
            fingerprint, seats, amount_paise,
        )
    except asyncpg.UniqueViolationError as exc:  # unreachable behind the lock
        raise IdempotencyKeyReused(
            "idempotency key already used", {"idempotency_key": idempotency_key}
        ) from exc

    updated = await seat_ops.mark_confirmed(
        conn, show_id, seats, reservation_id, user_id
    )
    if updated != len(seats):
        raise SeatTaken("seat already taken", {"seats": sorted(seats)})

    logger.info(
        f"_reserve_in_transaction.end request_id={request_id} show_id={show_id} user_id={user_id} replayed=False reservation_id={reservation.id}"
    )
    return reservation, False
