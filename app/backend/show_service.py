"""Business logic for shows. Owns the transactions."""
import logging
import re
from uuid import UUID, uuid4

import asyncpg

from app.db.models.seat import Seat
from app.db.models.show import Show
from app.db.operations import seat_ops, show_ops
from app.errors import NotFound

logger = logging.getLogger("app.shows")

_DIGITS = re.compile(r"(\d+)")


def _natural_key(label: str) -> list:
    """A2 < A10 (natural order) instead of A10 < A2."""
    return [int(p) if p.isdigit() else p for p in _DIGITS.split(label)]


def _build_state(show: Show, seats: list[Seat]) -> dict:
    """Counts are derived from the very same rows that are listed, so
    available + held + confirmed always equals the number of seats shown."""
    counts = {"available": 0, "held": 0, "confirmed": 0}
    for s in seats:
        counts[s.status] += 1
    ordered = sorted(seats, key=lambda s: _natural_key(s.seat_label))
    return {
        "id": str(show.id),
        "name": show.name,
        "price_paise": show.price_paise,
        "per_user_limit": show.per_user_limit,
        "total_seats": show.total_seats,
        **counts,
        "seats": [{"seat": s.seat_label, "status": s.status} for s in ordered],
    }


async def create_show(
    pool: asyncpg.Pool,
    name: str,
    seats: list[str],
    price_paise: int,
    per_user_limit: int,
) -> dict:
    show_id = uuid4()
    async with pool.acquire() as conn:
        # show + all seats in one transaction: no half-created shows
        async with conn.transaction():
            await show_ops.insert_show(
                conn, show_id, name, price_paise, per_user_limit, len(seats)
            )
            await seat_ops.bulk_insert_seats(conn, show_id, seats)
    logger.info(f"show_created show_id={show_id} seats={len(seats)}")
    return await get_show_state(pool, show_id)


async def get_show_state(pool: asyncpg.Pool, show_id: UUID) -> dict:
    async with pool.acquire() as conn:
        # one repeatable-read snapshot: show row and seat rows can't disagree
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            show = await show_ops.get_show(conn, show_id)
            if show is None:
                raise NotFound("show not found", {"show_id": str(show_id)})
            seats = await seat_ops.list_seats(conn, show_id)
    return _build_state(show, seats)