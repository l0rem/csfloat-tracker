from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from monitor import run_single_poll

from csfloat_monitor.models import CurrentListing
from csfloat_monitor.storage import Storage
from csfloat_monitor.types import ChangeSet, ListingRecord


def make_listing(
    listing_id: str,
    price: int,
    *,
    market_hash_name: str = "Test Item",
    def_index: int | None = None,
) -> ListingRecord:
    raw_json = "{}" if def_index is None else f'{{"item":{{"def_index":{def_index}}}}}'
    return ListingRecord(
        listing_id=listing_id,
        listing_url=f"https://csfloat.com/item/{listing_id}",
        price=price,
        state="listed",
        market_hash_name=market_hash_name,
        item_name=market_hash_name,
        wear_name="Battle-Scarred",
        float_value=0.31,
        created_at="2026-04-01T00:00:00Z",
        raw_json=raw_json,
    )


class FakeClient:
    def __init__(self, snapshots: list[dict[str, ListingRecord]]):
        self._snapshots = snapshots
        self._index = 0
        self.fetch_all_calls = 0
        self.fetch_target_calls = 0
        self.last_target_def_indexes: list[int] | None = None

    def fetch_all_listings(self) -> dict[str, ListingRecord]:
        self.fetch_all_calls += 1
        result = self._snapshots[min(self._index, len(self._snapshots) - 1)]
        self._index += 1
        return result

    def fetch_all_listings_for_def_indexes(self, def_indexes: list[int]) -> dict[str, ListingRecord]:
        self.fetch_target_calls += 1
        self.last_target_def_indexes = list(def_indexes)
        return self.fetch_all_listings()

    def close(self) -> None:
        return None


class FakeNotifier:
    def __init__(self):
        self.sent: list[ChangeSet] = []

    def send_change(self, change: ChangeSet) -> None:
        self.sent.append(change)

    def close(self) -> None:
        return None


class IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmp_dir.name) / "monitor.db")
        self.storage = Storage(db_path)
        self.storage.run_migrations()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_startup_indexing_then_change_notifications(self) -> None:
        first = {"1": make_listing("1", 100), "2": make_listing("2", 200)}
        second = {"1": make_listing("1", 150), "3": make_listing("3", 300)}
        client = FakeClient([first, second])
        notifier = FakeNotifier()

        startup_change_count = run_single_poll(self.storage, client, notifier, is_startup=True)
        self.assertEqual(0, startup_change_count)
        self.assertEqual(0, len(notifier.sent))

        second_change_count = run_single_poll(self.storage, client, notifier, is_startup=False)
        self.assertEqual(3, second_change_count)
        self.assertEqual(3, len(notifier.sent))

        listing_ids = sorted(row.listing_id for row in CurrentListing.select())
        self.assertEqual(["1", "3"], listing_ids)

    def test_startup_diff_is_emitted_when_snapshot_exists(self) -> None:
        seed_data = {"1": make_listing("1", 100)}
        seed_poll = self.storage.start_poll(is_startup=False)
        self.storage.apply_poll_results(seed_poll, seed_data, [])

        changed = {"1": make_listing("1", 145)}
        client = FakeClient([changed])
        notifier = FakeNotifier()

        change_count = run_single_poll(self.storage, client, notifier, is_startup=True)
        self.assertEqual(1, change_count)
        self.assertEqual(1, len(notifier.sent))

    def test_targeted_poll_uses_def_index_fetcher(self) -> None:
        first = {"1": make_listing("1", 100)}
        client = FakeClient([first])
        notifier = FakeNotifier()

        change_count = run_single_poll(
            self.storage,
            client,
            notifier,
            is_startup=True,
            target_def_indexes=[6134, 6121],
        )

        self.assertEqual(0, change_count)
        self.assertEqual(1, client.fetch_target_calls)
        self.assertEqual([6134, 6121], client.last_target_def_indexes)

    def test_startup_targeted_poll_ignores_non_target_seeded_snapshot(self) -> None:
        seed_data = {
            "howl-1": make_listing("howl-1", 25000, market_hash_name="Howl Pin", def_index=6101),
        }
        seed_poll = self.storage.start_poll(is_startup=False)
        self.storage.apply_poll_results(seed_poll, seed_data, [])

        current = {
            "val-1": make_listing("val-1", 5000, market_hash_name="Valeria Pin", def_index=6121),
        }
        client = FakeClient([current])
        notifier = FakeNotifier()

        change_count = run_single_poll(
            self.storage,
            client,
            notifier,
            is_startup=True,
            target_def_indexes=[6134, 6121],
        )

        self.assertEqual(0, change_count)
        self.assertEqual(0, len(notifier.sent))
