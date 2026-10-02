"""Token endpoint. Test-only issuer: no password, any user id gets a token."""
import logging

from fastapi import APIRouter

from app.api.schemas import TokenRequest, TokenResponse
from app.backend import token_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


@router.post("/auth/token", response_model=TokenResponse)
async def create_token(body: TokenRequest):
    from app.main import REQUEST_ID

    request_id = REQUEST_ID.get() or "-"
    logger.info(f"create_token.start request_id={request_id} user_id={body.user_id}")
    result = token_service.issue_token(body.user_id)
    logger.info(f"create_token.end request_id={request_id} user_id={body.user_id}")
    return result
