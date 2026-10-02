"""FastAPI dependency that turns a bearer token into a user id."""
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt_handler import decode_token
from app.errors import Unauthorized

# auto_error=False so a missing header goes through our unified error format
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """The user id comes ONLY from the verified token's `sub` claim."""
    if creds is None:
        raise Unauthorized("missing bearer token")
    return decode_token(creds.credentials)["sub"]