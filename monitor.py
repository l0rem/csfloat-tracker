from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from csfloat_monitor.config import AppConfig
from csfloat_monitor.currency import CSFloatCurrencyPriceFormatter
from csfloat_monitor.csfloat_client import CSFloatClient
from csfloat_monitor.diff_engine import diff_listings
from csfloat_monitor.market_insights import DelistedMarketAnalyzer
from csfloat_monitor.storage import Storage
from csfloat_monitor.telegram_notifier import TelegramNotifier


LOGGER = logging.getLogger("csfloat.monitor")


def configure_logging() -> None:
    Path("./logs").mkdir(parents=True, exist_ok=True)
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("./logs/monitor.log"),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def run_single_poll(
    storage: Storage,
    csfloat_client: CSFloatClient,
    notifier: TelegramNotifier,
    is_startup: bool,
    target_def_indexes: list[int] | None = None,
) -> int:
    poll = storage.start_poll(is_startup=is_startup)
    LOGGER.info("poll_start poll_id=%s startup=%s", poll.id, is_startup)
    try:
        previous_all = storage.get_snapshot()
        LOGGER.info("poll_snapshot_loaded poll_id=%s previous_count=%d", poll.id, len(previous_all))
        if target_def_indexes:
            previous = _filter_snapshot_by_def_indexes(previous_all, target_def_indexes)
            if len(previous) != len(previous_all):
                LOGGER.info(
                    "poll_snapshot_filtered poll_id=%s previous_filtered_count=%d previous_skipped_count=%d target_def_indexes=%s",
                    poll.id,
                    len(previous),
                    len(previous_all) - len(previous),
                    target_def_indexes,
                )
        else:
            previous = previous_all
        if target_def_indexes:
            current = csfloat_client.fetch_all_listings_for_def_indexes(target_def_indexes)
            LOGGER.info(
                "poll_fetch_complete poll_id=%s fetched_count=%d target_def_indexes=%s",
                poll.id,
                len(current),
                target_def_indexes,
            )
        else:
            current = csfloat_client.fetch_all_listings()
            LOGGER.info("poll_fetch_complete poll_id=%s fetched_count=%d", poll.id, len(current))
        if is_startup and not previous:
            changes = []
        else:
            changes = diff_listings(previous, current)
        LOGGER.info("poll_diff_complete poll_id=%s changes=%d", poll.id, len(changes))
        storage.apply_poll_results(poll, current, changes)
    except Exception as exc:  # noqa: BLE001
        storage.mark_poll_failed(poll, str(exc))
        LOGGER.exception("poll_failed poll_id=%s startup=%s error=%s", poll.id, is_startup, exc)
        raise

    sent_count = 0
    for change in changes:
        try:
            notifier.send_change(change)
            sent_count += 1
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception(
                "notify_failed poll_id=%s listing_id=%s change_type=%s error=%s",
                poll.id,
                change.listing_id,
                change.change_type,
                exc,
            )

    LOGGER.info(
        "poll_complete poll_id=%s startup=%s fetched=%d changes=%d notifications_sent=%d",
        poll.id,
        is_startup,
        len(current),
        len(changes),
        sent_count,
    )
    return len(changes)


def cmd_resolve_chat_id(args: argparse.Namespace) -> int:
    config = AppConfig.from_env()
    LOGGER.info("resolve_chat_id_start username=%s db=%s", args.username, config.redacted_database_target())
    storage = Storage(config.database_url)
    storage.run_migrations()

    notifier = TelegramNotifier(bot_token=config.telegram_bot_token, chat_id="0")
    try:
        chat_id = notifier.resolve_chat_id(args.username)
    finally:
        notifier.close()

    storage.set_telegram_chat_id(chat_id)
    print(f"Resolved chat id for @{args.username.lstrip('@')}: {chat_id}")
    print("Saved chat id to settings table.")
    return 0


def cmd_run(_: argparse.Namespace) -> int:
    config = AppConfig.from_env()
    LOGGER.info(
        "startup_config db=%s proxy=%s listings_url=%s poll_interval=%ss http_max_retries=%d http_429_retries=%d "
        "http_backoff=%.2fs market_avg_cache_ttl=%ss market_avg_min_samples=%d "
        "http_max_backoff=%.2fs http_page_delay=%.2fs display_currency=%s target_def_indexes=%s",
        config.redacted_database_target(),
        config.redacted_proxy_target(),
        config.csfloat_listings_url,
        config.poll_interval_seconds,
        config.http_max_retries,
        config.http_429_retries,
        config.http_backoff_seconds,
        config.market_avg_cache_ttl_seconds,
        config.market_avg_min_samples,
        config.http_max_backoff_seconds,
        config.http_page_delay_seconds,
        config.display_currency,
        config.pin_target_def_indexes,
    )
    storage = Storage(config.database_url)
    storage.run_migrations()

    chat_id = config.telegram_chat_id or storage.get_telegram_chat_id()
    if not chat_id:
        raise RuntimeError(
            "Telegram chat id is missing. Run `python monitor.py resolve-chat-id --username loremsh` first "
            "or set TELEGRAM_CHAT_ID in .env"
        )

    csfloat_client = CSFloatClient(
        api_key=config.csfloat_api_key,
        listings_url=config.csfloat_listings_url,
        item_url_template=config.item_url_template,
        screenshot_url_template=config.screenshot_url_template,
        timeout_seconds=config.http_timeout_seconds,
        max_retries=config.http_max_retries,
        max_429_retries=config.http_429_retries,
        backoff_seconds=config.http_backoff_seconds,
        max_backoff_seconds=config.http_max_backoff_seconds,
        page_delay_seconds=config.http_page_delay_seconds,
        proxy=config.csfloat_proxy,
    )
    price_formatter = CSFloatCurrencyPriceFormatter(
        api_key=config.csfloat_api_key,
        target_currency=config.display_currency,
        timeout_seconds=config.http_timeout_seconds,
        max_retries=config.http_max_retries,
        backoff_seconds=config.http_backoff_seconds,
        cache_ttl_seconds=config.exchange_rate_cache_ttl_seconds,
        proxy=config.csfloat_proxy,
    )
    notifier = TelegramNotifier(
        bot_token=config.telegram_bot_token,
        chat_id=chat_id,
        timeout_seconds=config.http_timeout_seconds,
        price_formatter=price_formatter,
        market_analyzer=DelistedMarketAnalyzer(
            cache_ttl_seconds=config.market_avg_cache_ttl_seconds,
            min_samples=config.market_avg_min_samples,
        ),
    )

    try:
        try:
            run_single_poll(
                storage,
                csfloat_client,
                notifier,
                is_startup=True,
                target_def_indexes=config.pin_target_def_indexes,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_rate_limited_error(exc):
                LOGGER.warning("startup_poll_rate_limited error=%s", exc)
            else:
                LOGGER.exception("startup_poll_failed error=%s", exc)

        while True:
            time.sleep(config.poll_interval_seconds)
            try:
                run_single_poll(
                    storage,
                    csfloat_client,
                    notifier,
                    is_startup=False,
                    target_def_indexes=config.pin_target_def_indexes,
                )
            except Exception as exc:  # noqa: BLE001
                if _is_rate_limited_error(exc):
                    LOGGER.warning("poll_loop_rate_limited error=%s", exc)
                else:
                    LOGGER.exception("poll_loop_failed error=%s", exc)
    finally:
        csfloat_client.close()
        notifier.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor CSFloat listings and notify on Telegram when listings change."
    )

    sub = parser.add_subparsers(dest="command", required=True)

    resolve_chat_id = sub.add_parser("resolve-chat-id", help="Resolve and persist Telegram chat id")
    resolve_chat_id.add_argument("--username", required=True, help="Telegram username (without @)")
    resolve_chat_id.set_defaults(func=cmd_resolve_chat_id)

    run_parser = sub.add_parser("run", help="Run the polling loop")
    run_parser.set_defaults(func=cmd_run)

    return parser


def main() -> int:
    parser = build_parser()
    try:
        args = parser.parse_args()
        configure_logging()
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        LOGGER.info("stopped_by_user")
        return 0
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("fatal_error error=%s", exc)
        return 1


def _is_rate_limited_error(exc: Exception) -> bool:
    return "429" in str(exc) or "rate limit" in str(exc).lower()


def _filter_snapshot_by_def_indexes(
    snapshot: dict[str, ListingRecord],
    target_def_indexes: list[int],
) -> dict[str, ListingRecord]:
    targets = {int(v) for v in target_def_indexes}
    return {
        listing_id: listing
        for listing_id, listing in snapshot.items()
        if _extract_def_index(listing) in targets
    }


def _extract_def_index(listing: ListingRecord) -> int | None:
    if not listing.raw_json:
        return None
    try:
        payload = json.loads(listing.raw_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    raw_value = item.get("def_index")
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
