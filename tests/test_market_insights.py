from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csfloat_monitor.market_insights import DelistedMarketAnalyzer
from csfloat_monitor.models import ItemChange
from csfloat_monitor.storage import Storage
from csfloat_monitor.types import CHANGE_NEW, ChangeSet, FieldDelta


class _RawPriceFormatter:
    def format_price(self, raw: str | None) -> str:
        return str(raw)

    def close(self) -> None:
        return None


class MarketInsightsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmp_dir.name) / "monitor.db")
        self.storage = Storage(db_path)
        self.storage.run_migrations()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_builds_comparison_from_delisted_only(self) -> None:
        # listing-1 sold at 100
        ItemChange.create(listing_id="listing-1", change_type="new", field_name="market_hash_name", old_value="n/a", new_value="Item A")
        ItemChange.create(listing_id="listing-1", change_type="delisted", field_name="price", old_value="100", new_value="n/a")
        # listing-2 sold at 200
        ItemChange.create(listing_id="listing-2", change_type="new", field_name="market_hash_name", old_value="n/a", new_value="Item A")
        ItemChange.create(listing_id="listing-2", change_type="delisted", field_name="price", old_value="200", new_value="n/a")
        # this should be ignored (not delisted)
        ItemChange.create(listing_id="listing-3", change_type="price_changed", field_name="price", old_value="150", new_value="160")

        analyzer = DelistedMarketAnalyzer(cache_ttl_seconds=60, min_samples=2)
        change = ChangeSet(
            listing_id="x",
            change_type=CHANGE_NEW,
            listing_url="https://csfloat.com/item/x",
            market_hash_name="Item A",
            deltas=[FieldDelta(field_name="price", old_value="n/a", new_value="180")],
        )
        line = analyzer.build_market_line(change, _RawPriceFormatter())

        self.assertIn("Vs sold avg (2)", line)
        self.assertIn("+20.0%", line)
        self.assertIn("avg <code>150</code>", line)

    def test_reports_na_when_samples_too_low(self) -> None:
        ItemChange.create(listing_id="listing-1", change_type="new", field_name="market_hash_name", old_value="n/a", new_value="Item B")
        ItemChange.create(listing_id="listing-1", change_type="delisted", field_name="price", old_value="210", new_value="n/a")

        analyzer = DelistedMarketAnalyzer(cache_ttl_seconds=60, min_samples=3)
        change = ChangeSet(
            listing_id="x",
            change_type=CHANGE_NEW,
            listing_url="https://csfloat.com/item/x",
            market_hash_name="Item B",
            deltas=[FieldDelta(field_name="price", old_value="n/a", new_value="220")],
        )
        line = analyzer.build_market_line(change, _RawPriceFormatter())
        self.assertIn("n/a", line)
