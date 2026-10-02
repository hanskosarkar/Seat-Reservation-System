"""Issues tokens for the test-only token endpoint."""
import logging

from app import config
from app.auth.jwt_handler import create_token

logger = logging.getLogger("app.auth")


def issue_token(user_id: str) -> dict:
    try:
        from app.main import REQUEST_ID
        request_id = REQUEST_ID.get() or "-"
    except Exception:
        request_id = "-"

    logger.info(f"issue_token.start request_id={request_id} user_id={user_id}")
    result = {
        "access_token": create_token(user_id),
        "token_type": "bearer",
        "user_id": user_id,
        "expires_in": config.JWT_EXPIRY_MINUTES * 60,
    }
    logger.info(f"issue_token.end request_id={request_id} user_id={user_id}")
    return result
