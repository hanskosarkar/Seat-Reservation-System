import hashlib
import uuid

from fastapi import HTTPException

from app.db.operations import reservation_ops
from app.db.operations import seat_ops


def build_request_hash(seats: list[str]) -> str:
    normalized = ",".join(sorted(seats))

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


async def reserve_seat(
    pool,
    show_id,
    user_id: str,
    seats: list[str],
    idempotency_key: str,
):
    seat_label = seats[0]

    request_hash = build_request_hash(seats)
    reservation_id = uuid.uuid4()

    async with pool.acquire() as conn:
        async with conn.transaction():

            # 1. Check whether this request was already processed
            existing = await reservation_ops.get_by_idempotency_key(
                conn,
                user_id,
                idempotency_key,
            )

            if existing:
                if existing["request_hash"] != request_hash:
                    raise HTTPException(
                        status_code=409,
                        detail="IDEMPOTENCY_KEY_REUSED",
                    )

                return existing

            # 2. Get show price
            show = await conn.fetchrow(
                """
                SELECT price_paise
                FROM shows
                WHERE id = $1
                """,
                show_id,
            )

            if not show:
                raise HTTPException(
                    status_code=404,
                    detail="SHOW_NOT_FOUND",
                )

            # 3. Create reservation record
            await reservation_ops.create_reservation(
                conn=conn,
                reservation_id=reservation_id,
                show_id=show_id,
                user_id=user_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                seats=seats,
                amount_paise=show["price_paise"],
            )

            # 4. Atomically claim the seat
            claimed = await seat_ops.claim_available_seat(
                conn=conn,
                show_id=show_id,
                seat_label=seat_label,
                reservation_id=reservation_id,
                user_id=user_id,
            )

            if not claimed:
                raise HTTPException(
                    status_code=409,
                    detail="SEAT_TAKEN",
                )

            # 5. Transaction commits
            return {
                "id": reservation_id,
                "show_id": show_id,
                "user_id": user_id,
                "seats": seats,
                "amount_paise": show["price_paise"],
                "status": "confirmed",
            }