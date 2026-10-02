from uuid import UUID

import asyncpg


async def get_by_idempotency_key(
    conn: asyncpg.Connection,
    user_id: str,
    idempotency_key: str,
):
    """
    Return an existing reservation for the given user and idempotency key.

    Used to make retries of the same reservation request idempotent.
    """
    return await conn.fetchrow(
        """
        SELECT
            id,
            show_id,
            user_id,
            idempotency_key,
            request_hash,
            seats,
            amount_paise,
            status
        FROM reservations
        WHERE user_id = $1
          AND idempotency_key = $2
        """,
        user_id,
        idempotency_key,
    )


async def create_reservation(
    conn: asyncpg.Connection,
    reservation_id: UUID,
    show_id: UUID,
    user_id: str,
    idempotency_key: str,
    request_hash: str,
    seats: list[str],
    amount_paise: int,
):
    """
    Create a new reservation record.

    This must be called inside the same transaction that claims
    the corresponding seat.
    """
    return await conn.fetchrow(
        """
        INSERT INTO reservations (
            id,
            show_id,
            user_id,
            idempotency_key,
            request_hash,
            seats,
            amount_paise,
            status
        )
        VALUES (
            $1,
            $2,
            $3,
            $4,
            $5,
            $6,
            $7,
            'confirmed'
        )
        RETURNING
            id,
            show_id,
            user_id,
            idempotency_key,
            request_hash,
            seats,
            amount_paise,
            status
        """,
        reservation_id,
        show_id,
        user_id,
        idempotency_key,
        request_hash,
        seats,
        amount_paise,
    )