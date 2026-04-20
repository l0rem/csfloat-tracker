from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from csfloat_monitor.csfloat_client import CSFloatClient
from csfloat_monitor.storage import Storage
from csfloat_monitor.telegram_notifier import TelegramNotifier
from csfloat_monitor.types import ListingRecord, PinAlert, PinSaleAlert, PinSaleRecord


LOGGER = logging.getLogger("csfloat.pin_watcher")


@dataclass(slots=True)
class PinWatcherStats:
    polled: int = 0
    alerts_sent: int = 0
    callbacks_processed: int = 0
    purchases_succeeded: int = 0
    no_listing: int = 0
    no_baseline: int = 0
    duplicate_skipped: int = 0
    above_threshold: int = 0
    tied_low_alerts: int = 0
    new_low_alerts: int = 0
    sale_alerts_sent: int = 0


@dataclass(slots=True)
class BootstrapStats:
    requested: int = 0
    initialized: int = 0
    no_listing: int = 0
    sales_loaded: int = 0
    sales_missing: int = 0


def bootstrap_pin_states(
    *,
    storage: Storage,
    client: CSFloatClient,
    def_indexes: list[int],
    sales_rows: int,
) -> BootstrapStats:
    started = time.monotonic()
    stats = BootstrapStats(requested=len(def_indexes))
    LOGGER.info(
        "bootstrap_start pins_requested=%d sales_rows=%d",
        len(def_indexes),
        sales_rows,
    )
    for def_index in def_indexes:
        storage.ensure_pin_watch_state(def_index)
        LOGGER.info("bootstrap_pin_fetch_start def_index=%s", def_index)
        listing = client.fetch_lowest_listing(def_index)
        if listing is None or listing.price is None:
            stats.no_listing += 1
            LOGGER.warning("bootstrap_no_listing def_index=%s", def_index)
            continue

        market_hash_name = listing.market_hash_name or f"def_index:{def_index}"
        sales = client.fetch_sales_history(market_hash_name)
        recent_sales = sales[: max(1, sales_rows)]
        if recent_sales:
            storage.replace_recent_sales(def_index, market_hash_name, recent_sales)
            cheapest_sale = min(s.sale_price for s in recent_sales)
            latest_sale = recent_sales[0]
            stats.sales_loaded += 1
        else:
            cheapest_sale = None
            latest_sale = None
            stats.sales_missing += 1

        best_known = _min_compact([listing.price, cheapest_sale])
        last_sale_listing_id = str(latest_sale.listing_id) if latest_sale and latest_sale.listing_id else None
        last_sale_sold_at = latest_sale.sold_at if latest_sale else None
        last_sale_price = latest_sale.sale_price if latest_sale else None
        storage.update_pin_watch_state(
            def_index,
            market_hash_name=market_hash_name,
            best_listing_price=listing.price,
            best_sale_price=cheapest_sale,
            best_known_price=best_known,
            # seed dedupe at startup to avoid immediate tie alerts after restart
            last_alert_listing_id=listing.listing_id,
            last_alert_price=listing.price,
            # seed latest sale marker to avoid immediate sale alerts after restart
            last_sale_listing_id=last_sale_listing_id,
            last_sale_price=last_sale_price,
            last_sale_sold_at=last_sale_sold_at,
        )
        stats.initialized += 1
        LOGGER.info(
            "bootstrap_pin_ready def_index=%s market_hash_name=%s listing_id=%s listing_price=%s cheapest_sale=%s best_known=%s sales_rows=%d",
            def_index,
            market_hash_name,
            listing.listing_id,
            listing.price,
            cheapest_sale,
            best_known,
            len(recent_sales),
        )
    duration = time.monotonic() - started
    LOGGER.info(
        "bootstrap_complete duration_s=%.2f requested=%d initialized=%d no_listing=%d sales_loaded=%d sales_missing=%d",
        duration,
        stats.requested,
        stats.initialized,
        stats.no_listing,
        stats.sales_loaded,
        stats.sales_missing,
    )
    return stats


def run_pin_watch_poll(
    *,
    storage: Storage,
    client: CSFloatClient,
    notifier: TelegramNotifier,
    sales_rows: int,
) -> PinWatcherStats:
    started = time.monotonic()
    stats = PinWatcherStats()
    for state in storage.list_active_pin_states():
        stats.polled += 1
        def_index = int(state.def_index)
        listing = client.fetch_lowest_listing(def_index)
        if listing is None or listing.price is None:
            stats.no_listing += 1
            LOGGER.warning("poll_no_listing def_index=%s", def_index)
            continue

        market_hash_name = listing.market_hash_name or state.market_hash_name or f"def_index:{def_index}"
        sales = client.fetch_sales_history(market_hash_name)
        recent_sales = sales[: max(1, sales_rows)]
        cheapest_sale = min((sale.sale_price for sale in recent_sales), default=None)
        latest_sale = recent_sales[0] if recent_sales else None
        has_new_latest_sale = _is_new_latest_sale(
            last_sale_listing_id=state.last_sale_listing_id,
            last_sale_price=state.last_sale_price,
            last_sale_sold_at=state.last_sale_sold_at,
            latest_sale=latest_sale,
        )

        if recent_sales:
            storage.replace_recent_sales(def_index, market_hash_name, recent_sales)

        prior_best_known = state.best_known_price
        storage.update_pin_watch_state(
            def_index,
            market_hash_name=market_hash_name,
            best_listing_price=listing.price,
            best_sale_price=cheapest_sale,
            best_known_price=_min_compact([listing.price, cheapest_sale]),
        )
        refreshed_state = storage.get_pin_watch_state(def_index)
        if refreshed_state is None:
            LOGGER.warning("poll_state_missing_after_update def_index=%s", def_index)
            continue
        if has_new_latest_sale and latest_sale and refreshed_state.best_known_price:
            sale_alert = PinSaleAlert(
                def_index=def_index,
                market_hash_name=market_hash_name,
                sale_price=latest_sale.sale_price,
                lowest_known_price=refreshed_state.best_known_price,
                percent_above_lowest_known=_percent_above(refreshed_state.best_known_price, latest_sale.sale_price),
                sold_at=latest_sale.sold_at,
                sale_listing_id=latest_sale.listing_id,
                image_url=listing.image_url or listing.screenshot_url,
                listing_url=listing.listing_url,
            )
            notifier.send_pin_sale_alert(sale_alert)
            stats.sale_alerts_sent += 1
            storage.update_pin_watch_state(
                def_index,
                last_sale_listing_id=str(latest_sale.listing_id) if latest_sale.listing_id else "",
                last_sale_price=latest_sale.sale_price,
                last_sale_sold_at=latest_sale.sold_at or "",
            )
            LOGGER.info(
                "pin_sale_alert_sent def_index=%s sale_listing_id=%s sale_price=%s lowest_known=%s pct_above=%.2f",
                def_index,
                latest_sale.listing_id,
                latest_sale.sale_price,
                refreshed_state.best_known_price,
                sale_alert.percent_above_lowest_known,
            )
        best_known_price = prior_best_known if prior_best_known is not None else refreshed_state.best_known_price
        if best_known_price is None:
            stats.no_baseline += 1
            LOGGER.warning("poll_no_baseline def_index=%s listing_id=%s price=%s", def_index, listing.listing_id, listing.price)
            continue
        trigger_type = _trigger_type(listing.price, best_known_price)
        if not trigger_type:
            stats.above_threshold += 1
            LOGGER.debug(
                "poll_no_alert_above_threshold def_index=%s listing_id=%s price=%s best_known=%s",
                def_index,
                listing.listing_id,
                listing.price,
                best_known_price,
            )
            continue

        if _is_duplicate_alert(refreshed_state.last_alert_listing_id, refreshed_state.last_alert_price, listing):
            stats.duplicate_skipped += 1
            LOGGER.debug(
                "poll_alert_duplicate_skipped def_index=%s listing_id=%s price=%s",
                def_index,
                listing.listing_id,
                listing.price,
            )
            continue

        recent_sales = storage.get_recent_sales(def_index, limit=max(1, sales_rows))
        cheapest_sale = refreshed_state.best_sale_price
        pct_below = _percent_below(cheapest_sale, listing.price)
        action = storage.create_pin_callback_action(
            def_index=def_index,
            listing_id=listing.listing_id,
            listing_price=listing.price,
            listing_url=listing.listing_url,
        )
        alert = PinAlert(
            def_index=def_index,
            market_hash_name=listing.market_hash_name or refreshed_state.market_hash_name or f"def_index:{def_index}",
            listing_id=listing.listing_id,
            listing_price=listing.price,
            listing_url=listing.listing_url,
            image_url=listing.image_url or listing.screenshot_url,
            trigger_type=trigger_type,
            best_known_price=best_known_price,
            cheapest_sale_price=cheapest_sale,
            percent_below_cheapest_sale=pct_below,
            recent_sales=recent_sales[:10],
        )
        notifier.send_pin_alert(alert, action.action_id)
        stats.alerts_sent += 1
        if trigger_type == "new_low":
            stats.new_low_alerts += 1
        else:
            stats.tied_low_alerts += 1
        storage.update_pin_watch_state(
            def_index,
            last_alert_listing_id=listing.listing_id,
            last_alert_price=listing.price,
        )
        LOGGER.info(
            "pin_alert_sent def_index=%s listing_id=%s trigger=%s price=%s best_known=%s",
            def_index,
            listing.listing_id,
            trigger_type,
            listing.price,
            best_known_price,
        )
    duration = time.monotonic() - started
    LOGGER.info(
        "pin_watch_poll_stats duration_s=%.2f polled=%d alerts=%d sale_alerts=%d new_low=%d tied_low=%d above_threshold=%d duplicate_skipped=%d no_listing=%d no_baseline=%d",
        duration,
        stats.polled,
        stats.alerts_sent,
        stats.sale_alerts_sent,
        stats.new_low_alerts,
        stats.tied_low_alerts,
        stats.above_threshold,
        stats.duplicate_skipped,
        stats.no_listing,
        stats.no_baseline,
    )
    return stats


def process_telegram_callbacks(
    *,
    storage: Storage,
    client: CSFloatClient,
    notifier: TelegramNotifier,
) -> PinWatcherStats:
    started = time.monotonic()
    stats = PinWatcherStats()
    offset = storage.get_telegram_callback_offset()
    LOGGER.debug("callback_fetch_start offset=%d", offset)
    updates = notifier.fetch_updates(offset=offset)
    if not updates:
        LOGGER.debug("callback_fetch_empty offset=%d", offset)
        return stats

    max_update_id = offset
    for update in updates:
        update_id = int(update.get("update_id", 0))
        max_update_id = max(max_update_id, update_id + 1)
        callback = update.get("callback_query") or {}
        if not callback:
            continue
        stats.callbacks_processed += 1
        _process_callback(callback=callback, storage=storage, client=client, notifier=notifier, stats=stats)

    storage.set_telegram_callback_offset(max_update_id)
    duration = time.monotonic() - started
    LOGGER.info(
        "callback_batch_processed duration_s=%.2f callbacks=%d purchases_succeeded=%d next_offset=%d",
        duration,
        stats.callbacks_processed,
        stats.purchases_succeeded,
        max_update_id,
    )
    return stats


def _process_callback(
    *,
    callback: dict,
    storage: Storage,
    client: CSFloatClient,
    notifier: TelegramNotifier,
    stats: PinWatcherStats,
) -> None:
    callback_id = str(callback.get("id", ""))
    data = str(callback.get("data", ""))
    message = callback.get("message") or {}
    message_id = message.get("message_id")
    chat_id = (message.get("chat") or {}).get("id")
    if not callback_id or not data or message_id is None or chat_id is None:
        LOGGER.warning("callback_invalid_payload callback_id=%s data=%s", callback_id, data)
        return

    parts = data.split(":", 1)
    if len(parts) != 2:
        notifier.answer_callback_query(callback_id, "Invalid action")
        LOGGER.warning("callback_invalid_action_format data=%s", data)
        return
    action_type, action_id = parts
    LOGGER.info(
        "callback_received action_type=%s action_id=%s chat_id=%s message_id=%s",
        action_type,
        action_id,
        chat_id,
        message_id,
    )
    action = storage.get_pin_callback_action(action_id)
    if action is None:
        notifier.answer_callback_query(callback_id, "Action expired")
        LOGGER.warning("callback_action_missing action_id=%s", action_id)
        return

    if action_type == "buy":
        notifier.set_confirm_markup(chat_id, int(message_id), action_id)
        notifier.answer_callback_query(callback_id, "Confirm purchase?")
        LOGGER.info(
            "callback_buy_prompted action_id=%s def_index=%s listing_id=%s price=%s",
            action_id,
            action.def_index,
            action.listing_id,
            action.listing_price,
        )
        return

    if action_type == "confirm_no":
        notifier.set_buy_markup(chat_id, int(message_id), action_id)
        storage.update_pin_callback_action_status(action_id, "cancelled")
        notifier.answer_callback_query(callback_id, "Purchase cancelled")
        LOGGER.info("callback_purchase_cancelled action_id=%s", action_id)
        return

    if action_type != "confirm_yes":
        notifier.answer_callback_query(callback_id, "Unknown action")
        LOGGER.warning("callback_unknown_action action_type=%s action_id=%s", action_type, action_id)
        return

    pin_state = storage.get_pin_watch_state(action.def_index)
    if pin_state and pin_state.status == "completed":
        notifier.answer_callback_query(callback_id, "This pin is already purchased")
        LOGGER.info("callback_purchase_already_completed action_id=%s def_index=%s", action_id, action.def_index)
        return

    try:
        LOGGER.info(
            "callback_purchase_attempt action_id=%s def_index=%s listing_id=%s price=%s",
            action_id,
            action.def_index,
            action.listing_id,
            action.listing_price,
        )
        client.buy_now(listing_id=action.listing_id, total_price=action.listing_price)
        storage.update_pin_callback_action_status(action_id, "bought")
        storage.mark_pin_completed(action.def_index, action.listing_id)
        original_text = (message.get("caption") or message.get("text") or "").strip()
        status_line = "✅ <b>Purchase succeeded</b>"
        notifier.append_status_to_message(
            chat_id=chat_id,
            message_id=int(message_id),
            is_photo=bool(message.get("photo")),
            original_text=original_text,
            status_line=status_line,
        )
        notifier.answer_callback_query(callback_id, "Purchased")
        stats.purchases_succeeded += 1
        LOGGER.info(
            "callback_purchase_succeeded action_id=%s def_index=%s listing_id=%s",
            action_id,
            action.def_index,
            action.listing_id,
        )
        return
    except Exception as exc:  # noqa: BLE001
        storage.update_pin_callback_action_status(action_id, "failed")
        original_text = (message.get("caption") or message.get("text") or "").strip()
        status_line = f"❌ <b>Purchase failed:</b> <code>{_truncate(str(exc), 120)}</code>"
        notifier.append_status_to_message(
            chat_id=chat_id,
            message_id=int(message_id),
            is_photo=bool(message.get("photo")),
            original_text=original_text,
            status_line=status_line,
        )
        notifier.answer_callback_query(callback_id, "Purchase failed")
        LOGGER.exception(
            "callback_purchase_failed action_id=%s def_index=%s listing_id=%s error=%s",
            action_id,
            action.def_index,
            action.listing_id,
            exc,
        )


def _trigger_type(current_price: int, best_known_price: int) -> str | None:
    if current_price < best_known_price:
        return "new_low"
    if current_price == best_known_price:
        return "tied_low"
    return None


def _is_duplicate_alert(last_listing_id: str | None, last_alert_price: int | None, listing: ListingRecord) -> bool:
    if not last_listing_id:
        return False
    if listing.listing_id != last_listing_id:
        return False
    if last_alert_price is None or listing.price is None:
        return True
    return int(last_alert_price) == int(listing.price)


def _percent_below(cheapest_sale_price: int | None, current_price: int) -> float | None:
    if cheapest_sale_price is None or cheapest_sale_price <= 0:
        return None
    return ((cheapest_sale_price - current_price) / cheapest_sale_price) * 100.0


def _percent_above(lowest_price: int, current_price: int) -> float:
    if lowest_price <= 0:
        return 0.0
    return ((current_price - lowest_price) / lowest_price) * 100.0


def _is_new_latest_sale(
    *,
    last_sale_listing_id: str | None,
    last_sale_price: int | None,
    last_sale_sold_at: str | None,
    latest_sale: PinSaleRecord | None,
) -> bool:
    if latest_sale is None:
        return False
    return (
        (last_sale_listing_id or "") != (latest_sale.listing_id or "")
        or int(last_sale_price or -1) != int(latest_sale.sale_price)
        or (last_sale_sold_at or "") != (latest_sale.sold_at or "")
    )


def _min_compact(values: list[int | None]) -> int | None:
    filtered = [v for v in values if v is not None]
    if not filtered:
        return None
    return min(filtered)


def _truncate(raw: str, max_len: int) -> str:
    if len(raw) <= max_len:
        return raw
    return f"{raw[: max(1, max_len - 3)]}..."
