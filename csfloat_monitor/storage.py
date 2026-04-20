from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import uuid4

from csfloat_monitor.models import (
    CurrentListing,
    ItemChange,
    PinCallbackAction,
    PinRecentSale,
    PinWatchState,
    PollRun,
    Setting,
    initialize_database,
    run_unattended_migrations,
)
from csfloat_monitor.types import (
    CHANGE_DELISTED,
    CHANGE_NEW,
    CHANGE_PRICE_CHANGED,
    ChangeSet,
    ListingRecord,
    PinSaleRecord,
)


class Storage:
    TELEGRAM_CHAT_ID_KEY = "telegram_chat_id"
    TELEGRAM_CALLBACK_OFFSET_KEY = "telegram_callback_offset"

    def __init__(self, database_url_or_path: str):
        self._db = initialize_database(database_url_or_path)
        self._log = logging.getLogger("csfloat.storage")

    def run_migrations(self) -> None:
        self._log.info("migrations_start")
        run_unattended_migrations()
        self._log.info("migrations_complete")

    def get_snapshot(self) -> dict[str, ListingRecord]:
        result: dict[str, ListingRecord] = {}
        query = CurrentListing.select()
        for row in query:
            result[row.listing_id] = ListingRecord(
                listing_id=row.listing_id,
                listing_url=row.listing_url,
                price=row.price,
                state=row.state,
                market_hash_name=row.market_hash_name,
                item_name=row.item_name,
                wear_name=row.wear_name,
                float_value=row.float_value,
                created_at=row.created_at,
                screenshot_url=row.screenshot_url,
                image_url=row.image_url or row.screenshot_url or _infer_image_url_from_raw_json(row.raw_json),
                inspect_link=row.inspect_link or _infer_inspect_link_from_raw_json(row.raw_json),
                seller_description=row.seller_description or _infer_seller_description_from_raw_json(row.raw_json),
                raw_json=row.raw_json,
            )
        return result

    def start_poll(self, is_startup: bool) -> PollRun:
        return PollRun.create(started_at=datetime.now(UTC), status="running", is_startup=is_startup)

    def mark_poll_failed(self, poll: PollRun, error_message: str) -> None:
        poll.finished_at = datetime.now(UTC)
        poll.status = "failed"
        poll.error_message = error_message
        poll.save()

    def apply_poll_results(
        self,
        poll: PollRun,
        current: dict[str, ListingRecord],
        changes: Iterable[ChangeSet],
    ) -> None:
        changes_list = list(changes)
        now = datetime.now(UTC)

        with self._db.atomic():
            for change in changes_list:
                for delta in change.deltas:
                    ItemChange.create(
                        listing_id=change.listing_id,
                        change_type=change.change_type,
                        field_name=delta.field_name,
                        old_value=delta.old_value,
                        new_value=delta.new_value,
                        observed_at=now,
                        poll_id=poll.id,
                    )

            live_ids = set(current.keys())
            if live_ids:
                CurrentListing.delete().where(CurrentListing.listing_id.not_in(live_ids)).execute()
            else:
                CurrentListing.delete().execute()

            for listing in current.values():
                insert_data = {
                    "listing_id": listing.listing_id,
                    "listing_url": listing.listing_url,
                    "price": listing.price,
                    "state": listing.state,
                    "market_hash_name": listing.market_hash_name,
                    "item_name": listing.item_name,
                    "wear_name": listing.wear_name,
                    "float_value": listing.float_value,
                    "created_at": listing.created_at,
                    "screenshot_url": listing.screenshot_url,
                    "image_url": listing.image_url or listing.screenshot_url,
                    "inspect_link": listing.inspect_link,
                    "seller_description": listing.seller_description,
                    "raw_json": listing.raw_json,
                    "last_seen_at": now,
                }
                CurrentListing.insert(insert_data).on_conflict(
                    conflict_target=[CurrentListing.listing_id],
                    update=insert_data,
                ).execute()

            poll.finished_at = now
            poll.status = "completed"
            poll.total_fetched = len(current)
            poll.new_count = sum(1 for c in changes_list if c.change_type == CHANGE_NEW)
            poll.price_changed_count = sum(1 for c in changes_list if c.change_type == CHANGE_PRICE_CHANGED)
            poll.delisted_count = sum(1 for c in changes_list if c.change_type == CHANGE_DELISTED)
            poll.error_message = None
            poll.save()
            self._log.info(
                "poll_persisted poll_id=%s fetched=%d new=%d price_changed=%d delisted=%d",
                poll.id,
                len(current),
                poll.new_count,
                poll.price_changed_count,
                poll.delisted_count,
            )

    def get_setting(self, key: str) -> str | None:
        setting = Setting.get_or_none(Setting.key == key)
        return setting.value if setting else None

    def set_setting(self, key: str, value: str) -> None:
        now = datetime.now(UTC)
        Setting.insert(
            key=key,
            value=value,
            updated_at=now,
        ).on_conflict(
            conflict_target=[Setting.key],
            update={"value": value, "updated_at": now},
        ).execute()

    def get_telegram_chat_id(self) -> str | None:
        return self.get_setting(self.TELEGRAM_CHAT_ID_KEY)

    def set_telegram_chat_id(self, chat_id: str) -> None:
        self.set_setting(self.TELEGRAM_CHAT_ID_KEY, chat_id)

    def get_telegram_callback_offset(self) -> int:
        raw = self.get_setting(self.TELEGRAM_CALLBACK_OFFSET_KEY)
        if raw in {None, ""}:
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

    def set_telegram_callback_offset(self, offset: int) -> None:
        self.set_setting(self.TELEGRAM_CALLBACK_OFFSET_KEY, str(max(0, offset)))

    def ensure_pin_watch_state(self, def_index: int) -> PinWatchState:
        now = datetime.now(UTC)
        PinWatchState.insert(
            def_index=def_index,
            created_at=now,
            updated_at=now,
        ).on_conflict_ignore().execute()
        state = PinWatchState.get_or_none(PinWatchState.def_index == def_index)
        if state is None:
            raise RuntimeError(f"Failed to initialize pin watch state for def_index={def_index}")
        return state

    def list_active_pin_states(self) -> list[PinWatchState]:
        return list(PinWatchState.select().where(PinWatchState.status == "active").order_by(PinWatchState.def_index.asc()))

    def get_pin_watch_state(self, def_index: int) -> PinWatchState | None:
        return PinWatchState.get_or_none(PinWatchState.def_index == def_index)

    def update_pin_watch_state(
        self,
        def_index: int,
        *,
        market_hash_name: str | None = None,
        best_listing_price: int | None = None,
        best_sale_price: int | None = None,
        best_known_price: int | None = None,
        last_alert_listing_id: str | None = None,
        last_alert_price: int | None = None,
    ) -> None:
        state = self.ensure_pin_watch_state(def_index)
        now = datetime.now(UTC)

        if market_hash_name:
            state.market_hash_name = market_hash_name
        if best_listing_price is not None:
            state.best_listing_price = _min_or_value(state.best_listing_price, best_listing_price)
        if best_sale_price is not None:
            state.best_sale_price = _min_or_value(state.best_sale_price, best_sale_price)
        if best_known_price is not None:
            state.best_known_price = _min_or_value(state.best_known_price, best_known_price)
        if last_alert_listing_id is not None:
            state.last_alert_listing_id = last_alert_listing_id
        if last_alert_price is not None:
            state.last_alert_price = last_alert_price
        state.updated_at = now
        state.save()

    def mark_pin_completed(self, def_index: int, purchased_listing_id: str) -> None:
        state = self.ensure_pin_watch_state(def_index)
        state.status = "completed"
        state.purchased_listing_id = purchased_listing_id
        state.updated_at = datetime.now(UTC)
        state.save()

    def replace_recent_sales(
        self,
        def_index: int,
        market_hash_name: str,
        sales: list[PinSaleRecord],
    ) -> None:
        now = datetime.now(UTC)
        with self._db.atomic():
            PinRecentSale.delete().where(PinRecentSale.def_index == def_index).execute()
            for sale in sales:
                PinRecentSale.create(
                    def_index=def_index,
                    market_hash_name=market_hash_name,
                    sale_price=sale.sale_price,
                    sold_at=sale.sold_at,
                    listing_id=sale.listing_id,
                    recorded_at=now,
                )

    def get_recent_sales(self, def_index: int, limit: int = 10) -> list[PinSaleRecord]:
        rows = (
            PinRecentSale.select()
            .where(PinRecentSale.def_index == def_index)
            .order_by(PinRecentSale.id.asc())
            .limit(max(1, limit))
        )
        return [
            PinSaleRecord(
                sale_price=row.sale_price,
                sold_at=row.sold_at,
                listing_id=row.listing_id,
            )
            for row in rows
        ]

    def create_pin_callback_action(
        self,
        *,
        def_index: int,
        listing_id: str,
        listing_price: int,
        listing_url: str | None,
    ) -> PinCallbackAction:
        now = datetime.now(UTC)
        action_id = uuid4().hex
        return PinCallbackAction.create(
            action_id=action_id,
            def_index=def_index,
            listing_id=listing_id,
            listing_price=listing_price,
            listing_url=listing_url,
            status="pending",
            created_at=now,
            updated_at=now,
        )

    def get_pin_callback_action(self, action_id: str) -> PinCallbackAction | None:
        return PinCallbackAction.get_or_none(PinCallbackAction.action_id == action_id)

    def update_pin_callback_action_status(self, action_id: str, status: str) -> None:
        action = self.get_pin_callback_action(action_id)
        if action is None:
            return
        action.status = status
        action.updated_at = datetime.now(UTC)
        action.save()


def _infer_image_url_from_raw_json(raw_json: str | None) -> str | None:
    if not raw_json:
        return None
    try:
        payload = json.loads(raw_json)
    except Exception:  # noqa: BLE001
        return None

    item = payload.get("item") or {}
    screenshot_id = item.get("cs2_screenshot_id")
    if screenshot_id not in {None, ""}:
        return f"https://csfloat.pics/m/{screenshot_id}/playside.png?v=3"

    icon_url = item.get("icon_url")
    if icon_url in {None, ""}:
        return None

    icon_value = str(icon_url)
    if icon_value.startswith("http://") or icon_value.startswith("https://"):
        return icon_value
    return f"https://steamcommunity-a.akamaihd.net/economy/image/{icon_value}"


def _infer_inspect_link_from_raw_json(raw_json: str | None) -> str | None:
    if not raw_json:
        return None
    try:
        payload = json.loads(raw_json)
    except Exception:  # noqa: BLE001
        return None

    item = payload.get("item") or {}
    inspect_link = item.get("inspect_link") or item.get("serialized_inspect")
    if inspect_link in {None, ""}:
        return None
    return str(inspect_link)


def _infer_seller_description_from_raw_json(raw_json: str | None) -> str | None:
    if not raw_json:
        return None
    try:
        payload = json.loads(raw_json)
    except Exception:  # noqa: BLE001
        return None
    description = payload.get("description")
    if description in {None, ""}:
        return None
    return str(description)


def _min_or_value(existing: int | None, candidate: int) -> int:
    if existing is None:
        return candidate
    return min(existing, candidate)
