"""JWT creation and verification."""
import time

import jwt

from app import config
from app.errors import Unauthorized


def create_token(user_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + config.JWT_EXPIRY_MINUTES * 60,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Return the verified claims, or raise Unauthorized."""
    try:
        claims = jwt.decode(
            token,
            config.JWT_SECRET,
            algorithms=[config.JWT_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
    except jwt.ExpiredSignatureError:
        raise Unauthorized("token expired")
    except jwt.InvalidTokenError:
        raise Unauthorized("invalid token")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise Unauthorized("invalid token subject")
    return claims