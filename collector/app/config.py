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


GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_LLM_MODEL = "openai/gpt-oss-120b"


def _llm_from_env() -> tuple[str | None, str | None, str]:
    """(base_url, api_key, model) of the OpenAI-compatible LLM; base_url is
    None when none is configured. LLM_BASE_URL picks any provider (Ollama
    needs no key); LLM_API_KEY alone means OpenAI; GROQ_API_KEY alone means
    Groq, as before 0.13. The Groq key only ever goes to Groq."""
    base_url = os.environ.get("LLM_BASE_URL") or None
    api_key = os.environ.get("LLM_API_KEY") or None
    groq_key = os.environ.get("GROQ_API_KEY") or None
    if base_url is None and api_key:
        base_url = OPENAI_BASE_URL
    elif base_url is None and groq_key:
        base_url, api_key = GROQ_BASE_URL, groq_key
    model = os.environ.get("LLM_MODEL") or os.environ.get("GROQ_MODEL") or None
    if model is None:
        if base_url and base_url.rstrip("/") != GROQ_BASE_URL:
            _logger.warning("LLM_MODEL is not set; using %r, which %s may not serve", _DEFAULT_LLM_MODEL, base_url)
        model = _DEFAULT_LLM_MODEL
    return (base_url.rstrip("/") if base_url else None), api_key, model


@dataclass(frozen=True)
class Settings:
    database_url: str
    ingest_key: str
    read_key: str
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str
    ask_model: str
    ask_daily_limit: int
    cors_origins: list[str]
    db_pool_min_size: int
    db_pool_max_size: int
    rate_limit_per_minute: int
    consumer_salt: str | None
    rate_limit_env_key_per_minute: int
    rate_limit_ingest_per_ip_per_minute: int
    insights_scheduler_enabled: bool
    late_data_refresh_seconds: float
    alerts_enabled: bool
    alert_slack_webhook_url: str | None
    alert_discord_webhook_url: str | None
    alert_webhook_url: str | None
    alert_teams_webhook_url: str | None
    alert_pagerduty_routing_key: str | None
    alert_opsgenie_api_key: str | None
    alert_opsgenie_api_url: str
    alert_email_to: list[str]
    alert_email_from: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_starttls: bool
    resend_api_key: str | None
    alert_renotify_hours: float
    llm_cost_alert_min_usd: float | None
    consumer_limit_per_day: int
    dashboard_url: str | None
    public_dashboard: bool
    admin_username: str
    admin_password: str | None
    session_ttl_hours: float
    log_format: str
    metrics_enabled: bool
    metrics_token: str | None
    demo_mode: bool
    dashboard_dir: str | None

    @property
    def llm_enabled(self) -> bool:
        return self.llm_base_url is not None

    @classmethod
    def from_env(cls) -> "Settings":
        # Safe unless told otherwise: without REQLY_DEMO_MODE the keys must be
        # set, the dashboard needs a sign-in and no other origin may call the
        # API. Demo mode (docker-compose, the load generator) brings back the
        # public demo-key / demo-read-key, PUBLIC_DASHBOARD=true and CORS "*".
        demo_mode = _env_flag("REQLY_DEMO_MODE", False)
        # Empty when unset outside demo mode: the collector refuses to start
        # (main.py) and no key can match an empty one.
        ingest_key = os.environ.get("REQLY_INGEST_KEY") or ("demo-key" if demo_mode else "")
        # The read key is shipped inside the dashboard's JS bundle, so it is
        # effectively public. It deliberately does NOT fall back to the ingest
        # key -- that would hand write access to anyone who opens the dashboard.
        read_key = os.environ.get("REQLY_READ_KEY") or ("demo-read-key" if demo_mode else "")

        if ingest_key == "demo-key":
            _logger.warning(
                "REQLY_INGEST_KEY is not set — using the public default 'demo-key'. "
                "Set REQLY_INGEST_KEY and REQLY_READ_KEY before exposing this collector."
            )
        if read_key and read_key == ingest_key:
            _logger.warning(
                "REQLY_READ_KEY equals REQLY_INGEST_KEY. The read key is embedded in "
                "the dashboard bundle, so anyone with dashboard access can also write "
                "telemetry. Use two different keys."
            )

        cors_raw = os.environ.get("CORS_ORIGINS", "*" if demo_mode else "")
        cors_origins = [o.strip() for o in cors_raw.split(",") if o.strip()]
        llm_base_url, llm_api_key, llm_model = _llm_from_env()

        return cls(
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql://reqly:localdev@localhost:5432/reqly",
            ),
            ingest_key=ingest_key,
            read_key=read_key,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            # Ask Reqly needs reliable tool calling; defaults to the report model.
            ask_model=os.environ.get("ASK_MODEL") or llm_model,
            # The read key ships in the dashboard bundle, so anyone who can open
            # the dashboard can ask. This caps the LLM spend per day; 0 turns Ask off.
            ask_daily_limit=int(os.environ.get("ASK_DAILY_LIMIT", "200")),
            cors_origins=cors_origins,
            db_pool_min_size=int(os.environ.get("DB_POOL_MIN_SIZE", "2")),
            db_pool_max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "10")),
            rate_limit_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "600")),
            # Ingest is limited per key; the env key is shared by every service
            # that uses it, so it gets more. The per-IP cap stops key rotation.
            rate_limit_env_key_per_minute=int(os.environ.get("RATE_LIMIT_ENV_KEY_PER_MINUTE", "6000")),
            rate_limit_ingest_per_ip_per_minute=int(os.environ.get("RATE_LIMIT_INGEST_PER_IP_PER_MINUTE", "6000")),
            # false turns off the weekly insights report.
            insights_scheduler_enabled=_env_flag("INSIGHTS_SCHEDULER_ENABLED", True),
            late_data_refresh_seconds=float(os.environ.get("LATE_DATA_REFRESH_SECONDS", "60")),
            # Hourly anomaly alerts. The check always runs (open alerts show on
            # the dashboard); notifications only go to channels that are set.
            alerts_enabled=_env_flag("ALERTS_ENABLED", True),
            alert_slack_webhook_url=os.environ.get("ALERT_SLACK_WEBHOOK_URL") or None,
            alert_discord_webhook_url=os.environ.get("ALERT_DISCORD_WEBHOOK_URL") or None,
            alert_webhook_url=os.environ.get("ALERT_WEBHOOK_URL") or None,
            # Teams: a Workflows "When a Teams webhook request is received" URL.
            alert_teams_webhook_url=os.environ.get("ALERT_TEAMS_WEBHOOK_URL") or None,
            # PagerDuty Events API v2 integration (routing) key.
            alert_pagerduty_routing_key=os.environ.get("ALERT_PAGERDUTY_ROUTING_KEY") or None,
            alert_opsgenie_api_key=os.environ.get("ALERT_OPSGENIE_API_KEY") or None,
            # EU accounts: https://api.eu.opsgenie.com
            alert_opsgenie_api_url=os.environ.get("ALERT_OPSGENIE_API_URL") or "https://api.opsgenie.com",
            # Email: through SMTP_HOST, or Resend's API with RESEND_API_KEY.
            alert_email_to=[a.strip() for a in os.environ.get("ALERT_EMAIL_TO", "").split(",") if a.strip()],
            alert_email_from=os.environ.get("ALERT_EMAIL_FROM") or None,
            smtp_host=os.environ.get("SMTP_HOST") or None,
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_username=os.environ.get("SMTP_USERNAME") or None,
            smtp_password=os.environ.get("SMTP_PASSWORD") or None,
            smtp_starttls=_env_flag("SMTP_STARTTLS", True),
            resend_api_key=os.environ.get("RESEND_API_KEY") or None,
            alert_renotify_hours=float(os.environ.get("ALERT_RENOTIFY_HOURS", "6")),
            # LLM cost alerts: the smallest extra spend per route-hour, in USD,
            # worth an alert. 0 or negative turns them off.
            llm_cost_alert_min_usd=_positive_or_none(os.environ.get("LLM_COST_ALERT_MIN_USD", "1.0")),
            # Distinct consumers kept per service per UTC day; later new ones
            # are stored as "__other__". 0 turns the cap off.
            consumer_limit_per_day=int(os.environ.get("CONSUMER_LIMIT_PER_DAY", "1000")),
            # Hashes consumer ids that arrive raw over OTLP (enduser.id), like the
            # SDKs do before sending. Unset: OTLP consumer ids aren't stored.
            consumer_salt=os.environ.get("REQLY_CONSUMER_SALT") or None,
            dashboard_url=os.environ.get("DASHBOARD_URL") or None,
            # true: the read key (public in the dashboard bundle) can read
            # everything -- a public demo. false: reading needs a signed-in
            # user (the read key is refused).
            public_dashboard=_env_flag("PUBLIC_DASHBOARD", demo_mode),
            # First admin, created at start-up when there are no users yet.
            admin_username=os.environ.get("REQLY_ADMIN_USERNAME") or "admin",
            admin_password=os.environ.get("REQLY_ADMIN_PASSWORD") or None,
            session_ttl_hours=float(os.environ.get("SESSION_TTL_HOURS", "168")),
            # "json": one JSON object per log line; anything else: plain text.
            log_format=(os.environ.get("LOG_FORMAT") or "text").strip().lower(),
            # Prometheus metrics at /metrics; with METRICS_TOKEN set, scrapes
            # need "Authorization: Bearer <token>".
            metrics_enabled=_env_flag("METRICS_ENABLED", True),
            metrics_token=os.environ.get("METRICS_TOKEN") or None,
            demo_mode=demo_mode,
            # All-in-one: serve the built dashboard from this directory.
            dashboard_dir=os.environ.get("DASHBOARD_DIR") or None,
        )

    def missing_keys(self) -> list[str]:
        """Settings the collector can't run without (outside demo mode)."""
        return [name for name, value in (("REQLY_INGEST_KEY", self.ingest_key), ("REQLY_READ_KEY", self.read_key))
                if not value]


settings = Settings.from_env()
