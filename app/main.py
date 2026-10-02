"""FastAPI application entrypoint."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import config
from app.api.routes import auth, health, shows, reservations 
from app.db.pool import close_pool, create_pool, run_migrations
from app.errors import register_error_handlers

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
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
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(shows.router)
    app.include_router(reservations.router)
    
    return app


app = create_app()