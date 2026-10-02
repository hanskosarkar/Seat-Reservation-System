"""asyncpg pool lifecycle + migration runner.

Services own transactions; DB operations receive a `conn`. This module only
creates/destroys the pool and applies the schema.
"""
import asyncio
import logging
from pathlib import Path

import asyncpg

from app import config

logger = logging.getLogger("app.db")

SCHEMA_DIR = Path(__file__).parent / "schema"
# Arbitrary constant: serialises migrations if several instances start at once.
MIGRATION_LOCK_ID = 727_001


async def create_pool() -> asyncpg.Pool:
    """Create the pool, retrying while the database is still coming up."""
    logger.info(f"initializing db_pool_connection...")

    last_exc: Exception | None = None
    for attempt in range(1, config.DB_CONNECT_RETRIES + 1):
        try:
            pool = await asyncpg.create_pool(
                dsn=config.DATABASE_URL,
                min_size=config.DB_POOL_MIN,
                max_size=config.DB_POOL_MAX,
                command_timeout=config.DB_COMMAND_TIMEOUT_SECONDS,
                timeout=config.DB_ACQUIRE_TIMEOUT_SECONDS,
            )
            
            logger.info(f"db_pool_ready attempt={attempt}")
            return pool
        except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
            last_exc = exc
            wait = min(2 * attempt, 10)
            logger.warning(
                f"db_connect_failed attempt={attempt}/{config.DB_CONNECT_RETRIES} "
                f"retry_in={wait}s err={exc}"
            )
            await asyncio.sleep(wait)
    raise RuntimeError(f"could not connect to database: {last_exc}")


async def run_migrations(pool: asyncpg.Pool) -> None:
    """Apply every .sql file in schema/ in name order. Idempotent."""
    files = sorted(SCHEMA_DIR.glob("*.sql"))
    async with pool.acquire() as conn:
        # session-level advisory lock so concurrent starts don't race on DDL
        await conn.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)
        try:
            for f in files:
                await conn.execute(f.read_text())
                logger.info(f"migration_applied file={f.name}")
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_ID)


async def close_pool(pool: asyncpg.Pool) -> None:
    await pool.close()