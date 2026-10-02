"""Business logic for cancelling a reservation. Owns the transaction.

Model: explicit release (no time-boxed expiry). Cancelling returns the seats
to 'available'; they are immediately re-bookable.

Safety properties:
  * only the owner may cancel (403 otherwise)
  * cancelling is idempotent: a second cancel is a no-op that touches no seat,
    so it can never free a seat that has since been re-booked by someone else
  * the seat release is additionally guarded on reservation_id, so even a
    logic slip could not free another user's seat
  * lock order matches reservations: (show,user) advisory lock, then the
    reservation row, then seats in sorted order -> no deadlock
"""
import logging
from uuid import UUID

import asyncpg

from app.backend.reservation_service import reservation_to_dict
from app.db.operations import reservation_ops, seat_ops
from app.db.pool import connection
from app.errors import Forbidden, NotFound

logger = logging.getLogger("app.reservations")


async def cancel(pool: asyncpg.Pool, reservation_id: UUID, user_id: str) -> dict:
    try:
        from app.main import REQUEST_ID
        request_id = REQUEST_ID.get() or "-"
    except Exception:
        request_id = "-"

    logger.info(
        f"cancel.start request_id={request_id} reservation_id={reservation_id} user_id={user_id}"
    )
    async with connection(pool) as conn:
        async with conn.transaction():
            # plain read first: learn the owner before taking any lock
            res = await reservation_ops.get_by_id(conn, reservation_id)
            if res is None:
                raise NotFound(
                    "reservation not found", {"reservation_id": str(reservation_id)}
                )
            if res.user_id != user_id:
                raise Forbidden("only the owner can cancel this reservation")

            await reservation_ops.lock_user_scope(conn, res.show_id, user_id)
            res = await reservation_ops.get_by_id_for_update(conn, reservation_id)

            if res.status == "cancelled":  # idempotent: nothing to release
                logger.info(
                    f"cancel.end request_id={request_id} reservation_id={reservation_id} user_id={user_id} status=already_cancelled"
                )
                return reservation_to_dict(res)

            await seat_ops.lock_seats_ordered(conn, res.show_id, res.seats)
            released = await seat_ops.release_seats(
                conn, res.show_id, res.seats, res.id
            )
            if released != len(res.seats):
                logger.warning(
                    f"cancel_released_fewer_seats request_id={request_id} reservation_id={res.id} "
                    f"expected={len(res.seats)} released={released}"
                )
            await reservation_ops.mark_cancelled(conn, res.id)
            logger.info(
                f"reservation_cancelled request_id={request_id} reservation_id={res.id} user_id={user_id}"
            )
            result = {**reservation_to_dict(res), "status": "cancelled"}
            logger.info(
                f"cancel.end request_id={request_id} reservation_id={reservation_id} user_id={user_id}"
            )
            return result
