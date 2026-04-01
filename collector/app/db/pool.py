from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import asyncpg

from ..config import settings

_pool: asyncpg.Pool | None = None
logger = logging.getLogger("reqly.collector")

_MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


async def _run_migrations(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename  TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            already = await conn.fetchval(
                "SELECT 1 FROM schema_migrations WHERE filename = $1", sql_file.name
            )
            if already:
                logger.debug("Migration %s already applied, skipping", sql_file.name)
                continue
            async with conn.transaction():
                await conn.execute(sql_file.read_text())
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", sql_file.name
                )
            logger.info("Reqly collector: applied migration %s", sql_file.name)


async def create_pool() -> asyncpg.Pool:
    global _pool
    # asyncpg requires postgresql:// scheme; Timescale Cloud gives postgres://
    dsn = settings.database_url.replace("postgres://", "postgresql://", 1)
    for attempt in range(1, 31):
        try:
            _pool = await asyncpg.create_pool(
                dsn=dsn,
                min_size=settings.db_pool_min_size,
                max_size=settings.db_pool_max_size,
            )
            logger.info("DB pool ready (attempt %d)", attempt)
            break
        except Exception as exc:
            logger.warning("DB not ready (attempt %d/30): %s — retrying in 2s", attempt, exc)
            await asyncio.sleep(2)
    else:
        raise RuntimeError("Could not connect to database after 30 attempts")

    # Migrations run once after a successful connection — errors here are not
    # retried so they surface immediately instead of being masked as "DB not ready".
    await _run_migrations(_pool)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("db pool not initialized -- app lifespan didn't run")
    return _pool
