"""FastAPI application entrypoint."""
import logging
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar

from fastapi import FastAPI, Request

from app import config
from app.api.routes import auth, health, shows, reservations, metrics
from app.db.pool import close_pool, create_pool, run_migrations
from app.errors import register_error_handlers
from app.observability.metrics import instrument_reservations

REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = getattr(record, "request_id", REQUEST_ID.get() or "-")
        return True


class RequestIdFormatter(logging.Formatter):
    def format(self, record):
        record.request_id = getattr(record, "request_id", REQUEST_ID.get() or "-")
        return super().format(record)


logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s",
)
root_logger = logging.getLogger()
root_logger.addFilter(RequestIdFilter())
for handler in root_logger.handlers:
    handler.setFormatter(RequestIdFormatter(
        "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"
    ))
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    await run_migrations(app.state.pool)
    logger.info("startup_complete")
    try:
        yield
    finally:
        await close_pool(app.state.pool)
        logger.info("shutdown_complete")


def create_app() -> FastAPI:
    app = FastAPI(title="Seat Reservation Service", lifespan=lifespan)

    @app.middleware("http")
    async def add_request_id_and_log(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        token = REQUEST_ID.set(request_id)
        start = time.perf_counter()
        logger.info(
            f"request_start method={request.method} path={request.url.path} request_id={request_id}"
        )
        try:
            response = await call_next(request)
        finally:
            REQUEST_ID.reset(token)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            f"request_end method={request.method} path={request.url.path} status={response.status_code} duration_ms={duration_ms} request_id={request_id}"
        )
        return response

    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(shows.router)
    app.include_router(reservations.router)
    app.include_router(metrics.router)
    instrument_reservations()

    return app


app = create_app()