from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv

from csfloat_monitor.proxy import normalize_proxy_url, redact_proxy_url

DEFAULT_LISTINGS_URL = "https://csfloat.com/api/v1/listings?limit=40&max_price=46364&paint_index=1437"
DEFAULT_ITEM_URL_TEMPLATE = "https://csfloat.com/item/{listing_id}"
DEFAULT_SCREENSHOT_URL_TEMPLATE = "https://csfloat.pics/m/{screenshot_id}/playside.png?v=3"
# Alyx + Valeria (pin watch targets)
DEFAULT_PIN_DEF_INDEXES = [6134, 6121]


@dataclass(slots=True)
class AppConfig:
    csfloat_api_key: str
    csfloat_listings_url: str
    item_url_template: str
    screenshot_url_template: str
    csfloat_proxy: str | None
    telegram_bot_token: str
    telegram_chat_id: str | None
    database_url: str
    poll_interval_seconds: int
    http_timeout_seconds: float
    http_max_retries: int
    http_429_retries: int
    http_backoff_seconds: float
    http_max_backoff_seconds: float
    http_page_delay_seconds: float
    display_currency: str
    exchange_rate_cache_ttl_seconds: int
    market_avg_cache_ttl_seconds: int
    market_avg_min_samples: int
    log_level: str
    pin_target_def_indexes: list[int]

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()

        csfloat_api_key = os.getenv("CSFLOAT_API_KEY", "").strip()
        telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

        if not csfloat_api_key:
            raise ValueError("CSFLOAT_API_KEY is required")
        if not telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")

        poll_interval_raw = os.getenv("POLL_INTERVAL_SECONDS", "30").strip()
        poll_interval_seconds = int(poll_interval_raw)
        if poll_interval_seconds < 1:
            raise ValueError("POLL_INTERVAL_SECONDS must be >= 1")

        http_max_retries = int(os.getenv("HTTP_MAX_RETRIES", "8"))
        if http_max_retries < 1:
            raise ValueError("HTTP_MAX_RETRIES must be >= 1")

        http_429_retries = int(os.getenv("HTTP_429_RETRIES", "1"))
        if http_429_retries < 0:
            raise ValueError("HTTP_429_RETRIES must be >= 0")

        http_backoff_seconds = float(os.getenv("HTTP_BACKOFF_SECONDS", "1.5"))
        if http_backoff_seconds < 0:
            raise ValueError("HTTP_BACKOFF_SECONDS must be >= 0")

        http_max_backoff_seconds = float(os.getenv("HTTP_MAX_BACKOFF_SECONDS", "90"))
        if http_max_backoff_seconds <= 0:
            raise ValueError("HTTP_MAX_BACKOFF_SECONDS must be > 0")

        http_page_delay_seconds = float(os.getenv("HTTP_PAGE_DELAY_SECONDS", "0.35"))
        if http_page_delay_seconds < 0:
            raise ValueError("HTTP_PAGE_DELAY_SECONDS must be >= 0")

        exchange_rate_cache_ttl_seconds = int(os.getenv("EXCHANGE_RATE_CACHE_TTL_SECONDS", "300"))
        if exchange_rate_cache_ttl_seconds < 10:
            raise ValueError("EXCHANGE_RATE_CACHE_TTL_SECONDS must be >= 10")

        market_avg_cache_ttl_seconds = int(os.getenv("MARKET_AVG_CACHE_TTL_SECONDS", "300"))
        if market_avg_cache_ttl_seconds < 10:
            raise ValueError("MARKET_AVG_CACHE_TTL_SECONDS must be >= 10")

        market_avg_min_samples = int(os.getenv("MARKET_AVG_MIN_SAMPLES", "3"))
        if market_avg_min_samples < 1:
            raise ValueError("MARKET_AVG_MIN_SAMPLES must be >= 1")

        display_currency = os.getenv("DISPLAY_CURRENCY", "EUR").strip().upper()
        if not display_currency:
            raise ValueError("DISPLAY_CURRENCY must not be empty")

        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")

        pin_indexes_raw = os.getenv(
            "CSFLOAT_TARGET_DEF_INDEXES",
            ",".join(str(v) for v in DEFAULT_PIN_DEF_INDEXES),
        ).strip()
        pin_target_def_indexes: list[int] = []
        for part in pin_indexes_raw.split(","):
            token = part.strip()
            if not token:
                continue
            pin_target_def_indexes.append(int(token))
        if not pin_target_def_indexes:
            raise ValueError("CSFLOAT_TARGET_DEF_INDEXES must include at least one def_index")

        csfloat_proxy = normalize_proxy_url(os.getenv("CSFLOAT_PROXY", "").strip())

        return cls(
            csfloat_api_key=csfloat_api_key,
            csfloat_listings_url=os.getenv("CSFLOAT_LISTINGS_URL", DEFAULT_LISTINGS_URL).strip(),
            item_url_template=os.getenv("ITEM_URL_TEMPLATE", DEFAULT_ITEM_URL_TEMPLATE).strip(),
            screenshot_url_template=os.getenv(
                "SCREENSHOT_URL_TEMPLATE",
                DEFAULT_SCREENSHOT_URL_TEMPLATE,
            ).strip(),
            csfloat_proxy=csfloat_proxy,
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip() or None,
            database_url=(
                os.getenv("DATABASE_URL", "").strip()
                or os.getenv("SQLITE_PATH", "./data/monitor.db").strip()
            ),
            poll_interval_seconds=poll_interval_seconds,
            http_timeout_seconds=float(os.getenv("HTTP_TIMEOUT_SECONDS", "15")),
            http_max_retries=http_max_retries,
            http_429_retries=http_429_retries,
            http_backoff_seconds=http_backoff_seconds,
            http_max_backoff_seconds=http_max_backoff_seconds,
            http_page_delay_seconds=http_page_delay_seconds,
            display_currency=display_currency,
            exchange_rate_cache_ttl_seconds=exchange_rate_cache_ttl_seconds,
            market_avg_cache_ttl_seconds=market_avg_cache_ttl_seconds,
            market_avg_min_samples=market_avg_min_samples,
            log_level=log_level,
            pin_target_def_indexes=pin_target_def_indexes,
        )

    def redacted_database_target(self) -> str:
        target = self.database_url.strip()
        if target.lower().startswith("postgresql://") or target.lower().startswith("postgres://"):
            parsed = urlparse(target)
            host = parsed.hostname or "unknown-host"
            port = parsed.port or 5432
            db_name = parsed.path.lstrip("/") or "unknown-db"
            return f"postgres://{host}:{port}/{db_name}"
        return target

    def redacted_proxy_target(self) -> str | None:
        return redact_proxy_url(self.csfloat_proxy)
