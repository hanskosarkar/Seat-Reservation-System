"""SQL for the seats table. Functions take a connection; no transactions here."""
from uuid import UUID

import asyncpg

from app.db.models.seat import Seat


async def bulk_insert_seats(
    conn: asyncpg.Connection, show_id: UUID, labels: list[str]
) -> None:
    """Insert every seat as 'available' in one statement."""
    await conn.execute(
        """
        INSERT INTO seats (show_id, seat_label)
        SELECT $1, label FROM unnest($2::text[]) AS label
        """,
        show_id, labels,
    )


async def list_seats(conn: asyncpg.Connection, show_id: UUID) -> list[Seat]:
    rows = await conn.fetch(
        """
        SELECT show_id, seat_label, status, reservation_id, user_id
        FROM seats WHERE show_id = $1
        """,
        show_id,
    )
    return [Seat.from_record(r) for r in rows]


async def count_user_seats(
    conn: asyncpg.Connection, show_id: UUID, user_id: str
) -> int:
    """Seats this user currently holds or has confirmed in this show."""
    return await conn.fetchval(
        """
        SELECT count(*) FROM seats
        WHERE show_id = $1 AND user_id = $2 AND status IN ('held', 'confirmed')
        """,
        show_id, user_id,
    )


async def lock_seats_ordered(
    conn: asyncpg.Connection, show_id: UUID, labels: list[str]
) -> list[Seat]:
    """Row-lock the given seats (FOR UPDATE) in a fixed, sorted order.

    EVERY code path that locks several seats goes through here, so all
    transactions acquire seat locks in the same order (ORDER BY seat_label).
    Two overlapping multi-seat requests can therefore never wait on each other
    in a cycle: no deadlock. A concurrent request for the same seat blocks
    here until the first transaction ends, then sees the committed state.
    """
    rows = await conn.fetch(
        """
        SELECT show_id, seat_label, status, reservation_id, user_id
        FROM seats
        WHERE show_id = $1 AND seat_label = ANY($2::text[])
        ORDER BY seat_label
        FOR UPDATE
        """,
        show_id, labels,
    )
    return [Seat.from_record(r) for r in rows]


async def mark_confirmed(
    conn: asyncpg.Connection,
    show_id: UUID,
    labels: list[str],
    reservation_id: UUID,
    user_id: str,
) -> int:
    """Conditional update guarded on current state. Returns rows changed.

    The `status = 'available'` guard makes this race-free even on its own:
    a seat that is no longer available is simply not updated.
    """
    result = await conn.execute(
        """
        UPDATE seats
        SET status = 'confirmed', reservation_id = $3, user_id = $4
        WHERE show_id = $1
          AND seat_label = ANY($2::text[])
          AND status = 'available'
        """,
        show_id, labels, reservation_id, user_id,
    )
    return int(result.split()[-1])  # "UPDATE 3" -> 3


async def release_seats(
    conn: asyncpg.Connection,
    show_id: UUID,
    labels: list[str],
    reservation_id: UUID,
) -> int:
    """Return seats to 'available', but ONLY those still owned by this exact
    reservation. A seat that now belongs to someone else is never touched, so
    a release can never resurrect or steal a seat confirmed to another user.
    """
    result = await conn.execute(
        """
        UPDATE seats
        SET status = 'available', reservation_id = NULL, user_id = NULL
        WHERE show_id = $1
          AND seat_label = ANY($2::text[])
          AND reservation_id = $3
        """,
        show_id, labels, reservation_id,
    )
    return int(result.split()[-1])
