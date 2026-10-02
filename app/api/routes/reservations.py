from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from app import config
from app.api.schemas import ReservationResponse, ReserveRequest
from app.auth.dependencies import get_current_user
from app.backend import cancellation_service, reservation_service
from app.errors import InvalidRequest

router = APIRouter(tags=["reservations"])


def _resolve_idempotency_key(header_value: str | None, body_value: str | None) -> str:
    """The key may come from the Idempotency-Key header or the body field."""
    candidates = [v.strip() for v in (header_value, body_value) if v is not None]
    if not candidates or not candidates[0]:
        raise InvalidRequest(
            "idempotency key required (Idempotency-Key header or "
            "idempotency_key body field)"
        )
    if len(set(candidates)) > 1:
        raise InvalidRequest("header and body idempotency keys differ")
    key = candidates[0]
    if len(key) > config.MAX_IDEMPOTENCY_KEY_LENGTH:
        raise InvalidRequest(
            f"idempotency key longer than {config.MAX_IDEMPOTENCY_KEY_LENGTH} "
            "characters"
        )
    return key


@router.post(
    "/shows/{show_id}/reserve",
    response_model=ReservationResponse,
    status_code=201,
)
async def reserve(
    show_id: UUID,
    body: ReserveRequest,
    request: Request,
    response: Response,
    user_id: str = Depends(get_current_user),  # identity from the token only
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
):
    key = _resolve_idempotency_key(idempotency_key_header, body.idempotency_key)
    result, replayed = await reservation_service.reserve(
        request.app.state.pool,
        show_id=show_id,
        user_id=user_id,
        seats=body.seats,
        idempotency_key=key,
    )
    if replayed:
        response.headers["Idempotent-Replay"] = "true"
    return result


@router.post(
    "/reservations/{reservation_id}/cancel",
    response_model=ReservationResponse,
    status_code=200,
)
async def cancel_reservation(
    reservation_id: UUID,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    return await cancellation_service.cancel(
        request.app.state.pool, reservation_id, user_id
    )
