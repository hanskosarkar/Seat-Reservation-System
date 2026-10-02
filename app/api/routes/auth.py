"""Token endpoint. Test-only issuer: no password, any user id gets a token."""
from fastapi import APIRouter

from app.api.schemas import TokenRequest, TokenResponse
from app.backend import token_service

router = APIRouter(tags=["auth"])


@router.post("/auth/token", response_model=TokenResponse)
async def create_token(body: TokenRequest):
    return token_service.issue_token(body.user_id)