from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


DEFAULT_LISTINGS_URL = "https://csfloat.com/api/v1/listings?limit=40&max_price=46364&paint_index=1437"
DEFAULT_ITEM_URL_TEMPLATE = "https://csfloat.com/item/{listing_id}"
DEFAULT_SCREENSHOT_URL_TEMPLATE = "https://csfloat.pics/m/{screenshot_id}/playside.png?v=3"


@dataclass(slots=True)
class AppConfig:
    csfloat_api_key: str
    csfloat_listings_url: str
    item_url_template: str
    screenshot_url_template: str
    telegram_bot_token: str
    telegram_chat_id: str | None
    database_url: str
    poll_interval_seconds: int
    http_timeout_seconds: float
    http_max_retries: int
    http_backoff_seconds: float
    display_currency: str
    exchange_rate_cache_ttl_seconds: int

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

        exchange_rate_cache_ttl_seconds = int(os.getenv("EXCHANGE_RATE_CACHE_TTL_SECONDS", "300"))
        if exchange_rate_cache_ttl_seconds < 10:
            raise ValueError("EXCHANGE_RATE_CACHE_TTL_SECONDS must be >= 10")

        display_currency = os.getenv("DISPLAY_CURRENCY", "EUR").strip().upper()
        if not display_currency:
            raise ValueError("DISPLAY_CURRENCY must not be empty")

        return cls(
            csfloat_api_key=csfloat_api_key,
            csfloat_listings_url=os.getenv("CSFLOAT_LISTINGS_URL", DEFAULT_LISTINGS_URL).strip(),
            item_url_template=os.getenv("ITEM_URL_TEMPLATE", DEFAULT_ITEM_URL_TEMPLATE).strip(),
            screenshot_url_template=os.getenv(
                "SCREENSHOT_URL_TEMPLATE",
                DEFAULT_SCREENSHOT_URL_TEMPLATE,
            ).strip(),
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip() or None,
            database_url=(
                os.getenv("DATABASE_URL", "").strip()
                or os.getenv("SQLITE_PATH", "./data/monitor.db").strip()
            ),
            poll_interval_seconds=poll_interval_seconds,
            http_timeout_seconds=float(os.getenv("HTTP_TIMEOUT_SECONDS", "15")),
            http_max_retries=int(os.getenv("HTTP_MAX_RETRIES", "3")),
            http_backoff_seconds=float(os.getenv("HTTP_BACKOFF_SECONDS", "1.0")),
            display_currency=display_currency,
            exchange_rate_cache_ttl_seconds=exchange_rate_cache_ttl_seconds,
        )
