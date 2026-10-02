"""Unified error wrapper.

Every error response has the same JSON shape:
    {"error": {"code": "SEAT_TAKEN", "message": "...", "details": {...}}}

Domain outcomes (seat taken, over limit...) are raised as AppError subclasses
with 4xx status codes. Anything unexpected is caught by the catch-all handler
so the client never sees a raw stack trace.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")


class AppError(Exception):
    status_code: int = 400
    code: str = "BAD_REQUEST"
    headers: dict[str, str] = {}

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidRequest(AppError):
    """Well-formed JSON that breaks a business input rule."""

    status_code = 422
    code = "INVALID_REQUEST"


class Unauthorized(AppError):
    """Missing, malformed, expired or tampered token."""

    status_code = 401
    code = "UNAUTHORIZED"
    headers = {"WWW-Authenticate": "Bearer"}


class Forbidden(AppError):
    """Authenticated, but not allowed (e.g. cancelling someone else's booking)."""

    status_code = 403
    code = "FORBIDDEN"


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"


class SeatNotFound(AppError):
    """A requested seat label does not exist in this show."""

    status_code = 404
    code = "SEAT_NOT_FOUND"


class SeatTaken(AppError):
    """Domain decline: a seat is already held or confirmed."""

    status_code = 409
    code = "SEAT_TAKEN"


class PerUserLimitExceeded(AppError):
    """Domain decline: the user would exceed the show's per-user seat limit."""

    status_code = 409
    code = "PER_USER_LIMIT_EXCEEDED"


class IdempotencyKeyReused(AppError):
    """The same idempotency key was sent with a different set of seats."""

    status_code = 409
    code = "IDEMPOTENCY_KEY_REUSED"


class Overloaded(AppError):
    """Load shedding as a clean, retryable 4xx instead of a server error:
    the database pool stayed saturated longer than we are willing to queue."""

    status_code = 429
    code = "SERVER_BUSY"
    headers = {"Retry-After": "1"}


class NotReady(AppError):
    """Dependency (database) unavailable: readiness fails closed."""

    status_code = 503
    code = "NOT_READY"


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
            headers=exc.headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR",
                "Request validation failed",
                {"errors": jsonable(exc.errors())},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body("HTTP_ERROR", str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception):
        logger.exception("unhandled_exception")
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Internal server error"),
        )


def jsonable(errors: list) -> list:
    """Make pydantic error dicts JSON-safe (drop non-serialisable ctx)."""
    return [
        {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
        for e in errors
    ]