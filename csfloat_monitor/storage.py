from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime

from csfloat_monitor.models import (
    CurrentListing,
    ItemChange,
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
)


class Storage:
    TELEGRAM_CHAT_ID_KEY = "telegram_chat_id"

    def __init__(self, database_url_or_path: str):
        self._db = initialize_database(database_url_or_path)

    def run_migrations(self) -> None:
        run_unattended_migrations()

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
