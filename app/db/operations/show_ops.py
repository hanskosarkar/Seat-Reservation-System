"""SQL for the shows table. Functions take a connection; no transactions here."""
from uuid import UUID

import asyncpg

from app.db.models.show import Show


async def insert_show(
    conn: asyncpg.Connection,
    show_id: UUID,
    name: str,
    price_paise: int,
    per_user_limit: int,
    total_seats: int,
) -> Show:
    row = await conn.fetchrow(
        """
        INSERT INTO shows (id, name, price_paise, per_user_limit, total_seats)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, name, price_paise, per_user_limit, total_seats, created_at
        """,
        show_id, name, price_paise, per_user_limit, total_seats,
    )
    return Show.from_record(row)


async def get_show(conn: asyncpg.Connection, show_id: UUID) -> Show | None:
    row = await conn.fetchrow(
        """
        SELECT id, name, price_paise, per_user_limit, total_seats, created_at
        FROM shows WHERE id = $1
        """,
        show_id,
    )
    return Show.from_record(row) if row else None