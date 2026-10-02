"""FastAPI dependency that turns a bearer token into a user id."""
import logging

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt_handler import decode_token
from app.errors import Unauthorized

logger = logging.getLogger(__name__)

# auto_error=False so a missing header goes through our unified error format
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """The user id comes ONLY from the verified token's `sub` claim."""
    from app.main import REQUEST_ID

    request_id = REQUEST_ID.get() or "-"
    logger.info(f"get_current_user.start request_id={request_id} creds_present={creds is not None}")
    if creds is None:
        raise Unauthorized("missing bearer token")
    user_id = decode_token(creds.credentials)["sub"]
    logger.info(f"get_current_user.end request_id={request_id} user_id={user_id}")
    return user_id