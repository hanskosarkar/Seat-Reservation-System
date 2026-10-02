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