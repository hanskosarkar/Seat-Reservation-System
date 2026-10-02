"""SQL for the reservations table. Functions take a connection; no transactions."""
from uuid import UUID

import asyncpg

from app.db.models.reservation import Reservation

_COLUMNS = (
    "id, show_id, user_id, idempotency_key, request_hash, seats, "
    "amount_paise, status, created_at"
)


async def lock_user_scope(conn: asyncpg.Connection, show_id: UUID, user_id: str) -> None:
    """Transaction-scoped advisory lock on (show, user).

    Serialises every booking/cancel by the same user for the same show, which
    is what makes the per-user limit and same-key retries race-free. It is
    released automatically at COMMIT/ROLLBACK. Other users are unaffected.
    """
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
        f"res:{show_id}:{user_id}",
    )


async def get_by_idempotency_key(
    conn: asyncpg.Connection, show_id: UUID, user_id: str, idempotency_key: str
) -> Reservation | None:
    row = await conn.fetchrow(
        f"""
        SELECT {_COLUMNS} FROM reservations
        WHERE show_id = $1 AND user_id = $2 AND idempotency_key = $3
        """,
        show_id, user_id, idempotency_key,
    )
    return Reservation.from_record(row) if row else None


async def get_by_id(conn: asyncpg.Connection, reservation_id: UUID) -> Reservation | None:
    row = await conn.fetchrow(
        f"SELECT {_COLUMNS} FROM reservations WHERE id = $1", reservation_id
    )
    return Reservation.from_record(row) if row else None


async def get_by_id_for_update(
    conn: asyncpg.Connection, reservation_id: UUID
) -> Reservation | None:
    row = await conn.fetchrow(
        f"SELECT {_COLUMNS} FROM reservations WHERE id = $1 FOR UPDATE",
        reservation_id,
    )
    return Reservation.from_record(row) if row else None


async def insert_reservation(
    conn: asyncpg.Connection,
    reservation_id: UUID,
    show_id: UUID,
    user_id: str,
    idempotency_key: str,
    request_hash: str,
    seats: list[str],
    amount_paise: int,
) -> Reservation:
    """Raises asyncpg.UniqueViolationError if (show, user, key) already exists."""
    row = await conn.fetchrow(
        f"""
        INSERT INTO reservations
            (id, show_id, user_id, idempotency_key, request_hash, seats,
             amount_paise, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, 'confirmed')
        RETURNING {_COLUMNS}
        """,
        reservation_id, show_id, user_id, idempotency_key, request_hash,
        seats, amount_paise,
    )
    return Reservation.from_record(row)


async def mark_cancelled(conn: asyncpg.Connection, reservation_id: UUID) -> None:
    await conn.execute(
        "UPDATE reservations SET status = 'cancelled' WHERE id = $1", reservation_id
    )
