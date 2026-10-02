"""Issues tokens for the test-only token endpoint."""
from app import config
from app.auth.jwt_handler import create_token


def issue_token(user_id: str) -> dict:
    return {
        "access_token": create_token(user_id),
        "token_type": "bearer",
        "user_id": user_id,
        "expires_in": config.JWT_EXPIRY_MINUTES * 60,
    }