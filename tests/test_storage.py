from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csfloat_monitor.diff_engine import diff_listings
from csfloat_monitor.models import CurrentListing, ItemChange
from csfloat_monitor.storage import Storage
from csfloat_monitor.types import ListingRecord


def make_listing(listing_id: str, price: int) -> ListingRecord:
    return ListingRecord(
        listing_id=listing_id,
        listing_url=f"https://csfloat.com/item/{listing_id}",
        price=price,
        state="listed",
        market_hash_name="Test Item",
        item_name="Test Item",
        wear_name="Battle-Scarred",
        float_value=0.42,
        created_at="2026-04-01T00:00:00Z",
        raw_json="{}",
    )


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmp_dir.name) / "monitor.db")
        self.storage = Storage(db_path)
        self.storage.run_migrations()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_append_only_change_log_and_snapshot_consistency(self) -> None:
        initial = {
            "1": make_listing("1", 100),
            "2": make_listing("2", 220),
        }
        poll_1 = self.storage.start_poll(is_startup=False)
        self.storage.apply_poll_results(poll_1, initial, diff_listings({}, initial))
        count_after_first_poll = ItemChange.select().count()
        self.assertGreater(count_after_first_poll, 0)

        current_snapshot = self.storage.get_snapshot()
        updated = {
            "1": make_listing("1", 135),
        }
        poll_2 = self.storage.start_poll(is_startup=False)
        second_changes = diff_listings(current_snapshot, updated)
        self.storage.apply_poll_results(poll_2, updated, second_changes)

        total_changes = ItemChange.select().count()
        self.assertGreater(total_changes, count_after_first_poll)

        second_poll_rows = list(ItemChange.select().where(ItemChange.poll_id == poll_2.id))
        self.assertEqual(4, len(second_poll_rows))

        listing_rows = list(CurrentListing.select())
        self.assertEqual(1, len(listing_rows))
        self.assertEqual("1", listing_rows[0].listing_id)
        self.assertEqual(135, listing_rows[0].price)
