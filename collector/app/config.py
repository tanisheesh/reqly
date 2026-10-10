from __future__ import annotations

import logging
import os
from dataclasses import dataclass

_logger = logging.getLogger("reqly.collector")


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _positive_or_none(raw: str) -> float | None:
    try:
        value = float(raw)
    except ValueError:
        _logger.warning("ignoring non-numeric value %r, using the default", raw)
        return 1.0
    return value if value > 0 else None


@dataclass(frozen=True)
class Settings:
    database_url: str
    ingest_key: str
    read_key: str
    groq_api_key: str | None
    groq_model: str
    ask_model: str
    ask_daily_limit: int
    cors_origins: list[str]
    db_pool_min_size: int
    db_pool_max_size: int
    rate_limit_per_minute: int
    insights_scheduler_enabled: bool
    late_data_refresh_seconds: float
    alerts_enabled: bool
    alert_slack_webhook_url: str | None
    alert_discord_webhook_url: str | None
    alert_webhook_url: str | None
    alert_renotify_hours: float
    llm_cost_alert_min_usd: float | None
    dashboard_url: str | None
    public_dashboard: bool
    admin_username: str
    admin_password: str | None
    session_ttl_hours: float

    @classmethod
    def from_env(cls) -> "Settings":
        ingest_key = os.environ.get("REQLY_INGEST_KEY", "demo-key")
        # The read key is shipped inside the dashboard's JS bundle, so it is
        # effectively public. It deliberately does NOT fall back to the ingest
        # key -- that would hand write access to anyone who opens the dashboard.
        read_key = os.environ.get("REQLY_READ_KEY", "demo-read-key")

        if ingest_key == "demo-key":
            _logger.warning(
                "REQLY_INGEST_KEY is not set — using the public default 'demo-key'. "
                "Set REQLY_INGEST_KEY and REQLY_READ_KEY before exposing this collector."
            )
        if read_key == ingest_key:
            _logger.warning(
                "REQLY_READ_KEY equals REQLY_INGEST_KEY. The read key is embedded in "
                "the dashboard bundle, so anyone with dashboard access can also write "
                "telemetry. Use two different keys."
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
            groq_model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
            # Ask Reqly needs reliable tool calling; defaults to the report model.
            ask_model=os.environ.get("ASK_MODEL") or os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
            # The read key ships in the dashboard bundle, so anyone who can open
            # the dashboard can ask. This caps the Groq spend per day; 0 turns Ask off.
            ask_daily_limit=int(os.environ.get("ASK_DAILY_LIMIT", "200")),
            cors_origins=cors_origins,
            db_pool_min_size=int(os.environ.get("DB_POOL_MIN_SIZE", "2")),
            db_pool_max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "10")),
            rate_limit_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "600")),
            # Set to false when the SAM Lambda owns the weekly run, otherwise
            # both fire every Sunday and the report is generated twice.
            insights_scheduler_enabled=_env_flag("INSIGHTS_SCHEDULER_ENABLED", True),
            late_data_refresh_seconds=float(os.environ.get("LATE_DATA_REFRESH_SECONDS", "60")),
            # Hourly anomaly alerts. The check always runs (open alerts show on
            # the dashboard); notifications only go to channels that are set.
            alerts_enabled=_env_flag("ALERTS_ENABLED", True),
            alert_slack_webhook_url=os.environ.get("ALERT_SLACK_WEBHOOK_URL") or None,
            alert_discord_webhook_url=os.environ.get("ALERT_DISCORD_WEBHOOK_URL") or None,
            alert_webhook_url=os.environ.get("ALERT_WEBHOOK_URL") or None,
            alert_renotify_hours=float(os.environ.get("ALERT_RENOTIFY_HOURS", "6")),
            # LLM cost alerts: the smallest extra spend per route-hour, in USD,
            # worth an alert. 0 or negative turns them off.
            llm_cost_alert_min_usd=_positive_or_none(os.environ.get("LLM_COST_ALERT_MIN_USD", "1.0")),
            dashboard_url=os.environ.get("DASHBOARD_URL") or None,
            # true: the read key (public in the dashboard bundle) can read
            # everything -- a public demo. false: reading needs a signed-in
            # user (the read key is refused).
            public_dashboard=_env_flag("PUBLIC_DASHBOARD", True),
            # First admin, created at start-up when there are no users yet.
            admin_username=os.environ.get("REQLY_ADMIN_USERNAME") or "admin",
            admin_password=os.environ.get("REQLY_ADMIN_PASSWORD") or None,
            session_ttl_hours=float(os.environ.get("SESSION_TTL_HOURS", "168")),
        )


settings = Settings.from_env()
