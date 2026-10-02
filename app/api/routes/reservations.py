from fastapi import APIRouter, Depends, Request, status

from app.api.schemas import (
    ReserveRequest,
    ReservationResponse,
)
from app.auth.dependencies import get_current_user
from app.backend.reservation_service import reserve_seat

router = APIRouter(
    prefix="/shows",
    tags=["reservations"],
)


@router.post(
    "/{show_id}/reserve",
    response_model=ReservationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reserve(
    show_id,
    request: ReserveRequest,
    current_user=Depends(get_current_user),
):
    result = await reserve_seat(
        pool=request.app.state.db_pool,
        show_id=show_id,
        user_id=current_user,
        seats=request.seats,
        idempotency_key=request.idempotency_key,
    )

    return {
        "reservation_id": str(result["id"]),
        "show_id": str(result["show_id"]),
        "user_id": result["user_id"],
        "seats": result["seats"],
        "amount_paise": result["amount_paise"],
        "status": result["status"],
    }