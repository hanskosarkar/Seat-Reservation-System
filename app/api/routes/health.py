"""Liveness and readiness probes. Neither requires authentication."""
import asyncio
import logging

from fastapi import APIRouter, Request

from app import config
from app.errors import NotReady

logger = logging.getLogger("app.health")
router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz():
    """Liveness: the process is up. Never touches the database."""
    return {"status": "ok"}


async def _ping_db(pool) -> None:
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")


@router.get("/readyz")
async def readyz(request: Request):
    """Readiness: the database answers. Fails closed (503) otherwise."""
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise NotReady("database pool not initialised")
    try:
        await asyncio.wait_for(
            _ping_db(pool), timeout=config.READY_CHECK_TIMEOUT_SECONDS
        )
    except Exception as exc:  # any failure means "not ready"
        logger.warning(f"readiness_failed err={exc!r}")
        raise NotReady("database unavailable") from exc
    return {"status": "ready", "database": "ok"}