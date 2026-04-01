from __future__ import annotations

import unittest

from csfloat_monitor.diff_engine import diff_listings
from csfloat_monitor.types import (
    CHANGE_DELISTED,
    CHANGE_NEW,
    CHANGE_PRICE_CHANGED,
    ListingRecord,
)


def make_listing(listing_id: str, price: int, state: str = "listed") -> ListingRecord:
    return ListingRecord(
        listing_id=listing_id,
        listing_url=f"https://csfloat.com/item/{listing_id}",
        price=price,
        state=state,
        market_hash_name="Test Item",
        item_name="Test Item",
        wear_name="Battle-Scarred",
        float_value=0.42,
        created_at="2026-04-01T00:00:00Z",
        raw_json="{}",
    )


class DiffEngineTests(unittest.TestCase):
    def test_detects_new_listing(self) -> None:
        current = {"1": make_listing("1", 100)}
        changes = diff_listings({}, current)

        self.assertEqual(1, len(changes))
        change = changes[0]
        self.assertEqual(CHANGE_NEW, change.change_type)
        self.assertEqual("1", change.listing_id)
        self.assertEqual(0.42, change.float_value)
        self.assertTrue(any(d.field_name == "price" and d.old_value == "n/a" and d.new_value == "100" for d in change.deltas))

    def test_detects_price_change(self) -> None:
        previous = {"1": make_listing("1", 100)}
        current = {"1": make_listing("1", 130)}
        changes = diff_listings(previous, current)

        self.assertEqual(1, len(changes))
        change = changes[0]
        self.assertEqual(CHANGE_PRICE_CHANGED, change.change_type)
        self.assertEqual(0.42, change.float_value)
        self.assertEqual("100", change.deltas[0].old_value)
        self.assertEqual("130", change.deltas[0].new_value)

    def test_detects_delisted(self) -> None:
        previous = {"1": make_listing("1", 100)}
        changes = diff_listings(previous, {})

        self.assertEqual(1, len(changes))
        change = changes[0]
        self.assertEqual(CHANGE_DELISTED, change.change_type)
        self.assertEqual(0.42, change.float_value)
        self.assertTrue(any(d.field_name == "state" and d.new_value == "delisted" for d in change.deltas))

    def test_detects_mixed_changes(self) -> None:
        previous = {"1": make_listing("1", 100), "2": make_listing("2", 200)}
        current = {"1": make_listing("1", 120), "3": make_listing("3", 300)}
        changes = diff_listings(previous, current)

        types = sorted(change.change_type for change in changes)
        self.assertEqual([CHANGE_DELISTED, CHANGE_NEW, CHANGE_PRICE_CHANGED], types)
