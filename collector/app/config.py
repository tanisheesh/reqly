from __future__ import annotations

import logging
import os
from dataclasses import dataclass

_logger = logging.getLogger("reqly.collector")


@dataclass(frozen=True)
class Settings:
    database_url: str
    ingest_key: str
    read_key: str
    groq_api_key: str | None
    groq_model: str
    cors_origins: list[str]
    db_pool_min_size: int
    db_pool_max_size: int
    rate_limit_per_minute: int

    @classmethod
    def from_env(cls) -> "Settings":
        ingest_key = os.environ.get("REQLY_INGEST_KEY", "demo-key")
        read_key = os.environ.get("REQLY_READ_KEY", ingest_key)

        if ingest_key == "demo-key":
            _logger.warning(
                "REQLY_INGEST_KEY is not set — using the public default 'demo-key'. "
                "Set REQLY_INGEST_KEY and REQLY_READ_KEY before exposing this collector."
            )

        cors_raw = os.environ.get("CORS_ORIGINS", "")
        cors_origins = [o.strip() for o in cors_raw.split(",") if o.strip()]

        return cls(
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql://reqly:localdev@localhost:5432/reqly",
            ),
            ingest_key=ingest_key,
            read_key=read_key,
            groq_api_key=os.environ.get("GROQ_API_KEY") or None,
            groq_model=os.environ.get("GROQ_MODEL", "llama3-70b-8192"),
            cors_origins=cors_origins,
            db_pool_min_size=int(os.environ.get("DB_POOL_MIN_SIZE", "2")),
            db_pool_max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "10")),
            rate_limit_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "600")),
        )


settings = Settings.from_env()
