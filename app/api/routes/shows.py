from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.schemas import CreateShowRequest, ShowResponse
from app.auth.dependencies import get_current_user
from app.backend import show_service

router = APIRouter(tags=["shows"])


@router.post(
    "/shows",
    response_model=ShowResponse,
    status_code=201,
    dependencies=[Depends(get_current_user)],  # any valid token may create a show
)
async def create_show(body: CreateShowRequest, request: Request):
    return await show_service.create_show(
        request.app.state.pool,
        name=body.name,
        seats=body.seats,
        price_paise=body.price_paise,
        per_user_limit=body.per_user_limit,
    )


@router.get("/shows/{show_id}", response_model=ShowResponse)
async def get_show(show_id: UUID, request: Request):
    """Public: lets anyone watch the reconciliation invariant."""
    return await show_service.get_show_state(request.app.state.pool, show_id)
