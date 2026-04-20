from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csfloat_monitor.pin_watcher import (
    bootstrap_pin_states,
    process_telegram_callbacks,
    run_pin_watch_poll,
)
from csfloat_monitor.storage import Storage
from csfloat_monitor.types import ListingRecord, PinSaleRecord


def make_listing(def_index: int, listing_id: str, price: int, market_hash_name: str = "Pin Item") -> ListingRecord:
    return ListingRecord(
        listing_id=listing_id,
        listing_url=f"https://csfloat.com/item/{listing_id}",
        price=price,
        state="listed",
        market_hash_name=market_hash_name,
        item_name=market_hash_name,
        wear_name=None,
        float_value=None,
        created_at="2026-04-20T00:00:00Z",
        raw_json=f'{{"item":{{"def_index":{def_index}}}}}',
        image_url="https://example.com/pin.png",
    )


class FakeClient:
    def __init__(
        self,
        listings: dict[int, list[ListingRecord]],
        sales: dict[str, list[PinSaleRecord] | list[list[PinSaleRecord]]],
    ):
        self._listings = listings
        self._sales = sales
        self.buy_calls: list[tuple[str, int]] = []

    def fetch_lowest_listing(self, def_index: int) -> ListingRecord | None:
        values = self._listings.get(def_index) or []
        if not values:
            return None
        if len(values) == 1:
            return values[0]
        return values.pop(0)

    def fetch_sales_history(self, market_hash_name: str) -> list[PinSaleRecord]:
        values = self._sales.get(market_hash_name) or []
        if values and isinstance(values[0], list):
            return list(values.pop(0))
        return list(values)

    def buy_now(self, *, listing_id: str, total_price: int) -> dict:
        self.buy_calls.append((listing_id, total_price))
        return {"ok": True}


class FakeNotifier:
    def __init__(self):
        self.alerts: list[tuple[str, int, int, int, float, str]] = []
        self.sale_alerts: list[tuple[int, int, float]] = []
        self._updates: list[dict] = []
        self.actions: list[tuple[str, str]] = []

    def send_pin_alert(self, alert, action_id: str):  # noqa: ANN001
        self.alerts.append(
            (
                alert.trigger_type,
                alert.previous_lowest_price,
                alert.absolute_lowest_price,
                alert.absolute_drop_price,
                alert.absolute_drop_percent,
                action_id,
            )
        )
        return {"ok": True}

    def send_pin_sale_alert(self, alert):  # noqa: ANN001
        self.sale_alerts.append((alert.sale_price, alert.lowest_known_price, alert.percent_above_lowest_known))
        return {"ok": True}

    def fetch_updates(self, *, offset: int):  # noqa: ARG002
        return list(self._updates)

    def answer_callback_query(self, callback_query_id: str, text: str | None = None):  # noqa: ARG002
        self.actions.append(("answer", text or ""))

    def set_confirm_markup(self, chat_id: int | str, message_id: int, action_id: str):  # noqa: ARG002
        self.actions.append(("confirm_markup", action_id))

    def set_buy_markup(self, chat_id: int | str, message_id: int, action_id: str):  # noqa: ARG002
        self.actions.append(("buy_markup", action_id))

    def append_status_to_message(
        self,
        *,
        chat_id: int | str,  # noqa: ARG002
        message_id: int,  # noqa: ARG002
        is_photo: bool,  # noqa: ARG002
        original_text: str,  # noqa: ARG002
        status_line: str,
    ):
        self.actions.append(("status", status_line))


class PinWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmp_dir.name) / "monitor.db")
        self.storage = Storage(db_path)
        self.storage.run_migrations()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_alerts_when_new_cheapest_replaces_previous_cheapest(self) -> None:
        def_index = 6121
        market = "Valeria Phoenix Pin"
        first = make_listing(def_index, "L1", 5000, market_hash_name=market)
        second = make_listing(def_index, "L2", 4800, market_hash_name=market)
        third = make_listing(def_index, "L2", 4800, market_hash_name=market)
        fourth = make_listing(def_index, "L4", 4700, market_hash_name=market)

        client = FakeClient(
            listings={def_index: [first, second, third, fourth]},
            sales={market: [PinSaleRecord(sale_price=5100), PinSaleRecord(sale_price=5200)]},
        )
        notifier = FakeNotifier()

        bootstrap_pin_states(
            storage=self.storage,
            client=client,
            def_indexes=[def_index],
            sales_rows=10,
        )
        stats_1 = run_pin_watch_poll(storage=self.storage, client=client, notifier=notifier, sales_rows=10)
        stats_2 = run_pin_watch_poll(storage=self.storage, client=client, notifier=notifier, sales_rows=10)
        stats_3 = run_pin_watch_poll(storage=self.storage, client=client, notifier=notifier, sales_rows=10)

        self.assertEqual(1, stats_1.alerts_sent)
        self.assertEqual(0, stats_2.alerts_sent)  # unchanged cheapest
        self.assertEqual(1, stats_3.alerts_sent)  # cheaper listing replaced previous cheapest
        self.assertEqual(2, len(notifier.alerts))
        self.assertEqual(
            ["new_cheapest_current", "new_cheapest_current"],
            [kind for kind, *_ in notifier.alerts],
        )
        self.assertEqual(5000, notifier.alerts[0][1])  # previous lowest
        self.assertEqual(4800, notifier.alerts[0][2])  # absolute lowest (new)
        self.assertEqual(200, notifier.alerts[0][3])   # absolute EUR drop
        self.assertAlmostEqual(4.0, notifier.alerts[0][4], places=2)
        self.assertEqual(4800, notifier.alerts[1][1])
        self.assertEqual(4700, notifier.alerts[1][2])
        self.assertEqual(100, notifier.alerts[1][3])
        self.assertAlmostEqual(2.083333, notifier.alerts[1][4], places=4)

    def test_confirm_yes_purchases_and_completes_pin(self) -> None:
        def_index = 6102
        self.storage.ensure_pin_watch_state(def_index)
        action = self.storage.create_pin_callback_action(
            def_index=def_index,
            listing_id="A1",
            listing_price=3900,
            listing_url="https://csfloat.com/item/A1",
        )
        notifier = FakeNotifier()
        notifier._updates = [
            {
                "update_id": 10,
                "callback_query": {
                    "id": "cb-1",
                    "data": f"confirm_yes:{action.action_id}",
                    "message": {
                        "message_id": 99,
                        "chat": {"id": 111},
                        "caption": "alert text",
                        "photo": [{"file_id": "p"}],
                    },
                },
            }
        ]
        client = FakeClient(listings={}, sales={})

        stats = process_telegram_callbacks(storage=self.storage, client=client, notifier=notifier)

        self.assertEqual(1, stats.callbacks_processed)
        self.assertEqual(1, stats.purchases_succeeded)
        self.assertEqual([("A1", 3900)], client.buy_calls)
        state = self.storage.get_pin_watch_state(def_index)
        self.assertIsNotNone(state)
        self.assertEqual("completed", state.status if state else "")
        refreshed_action = self.storage.get_pin_callback_action(action.action_id)
        self.assertIsNotNone(refreshed_action)
        self.assertEqual("bought", refreshed_action.status if refreshed_action else "")

    def test_new_latest_sale_sends_single_sale_alert(self) -> None:
        def_index = 6104
        market = "Guardian Pin"
        listing = make_listing(def_index, "L10", 5000, market_hash_name=market)

        client = FakeClient(
            listings={def_index: [listing, listing, listing]},
            sales={
                market: [
                    [PinSaleRecord(sale_price=4800, sold_at="2026-04-20T09:00:00Z", listing_id="S1")],
                    [PinSaleRecord(sale_price=5400, sold_at="2026-04-20T10:00:00Z", listing_id="S2")],
                    [PinSaleRecord(sale_price=5400, sold_at="2026-04-20T10:00:00Z", listing_id="S2")],
                ]
            },
        )
        notifier = FakeNotifier()

        bootstrap_pin_states(
            storage=self.storage,
            client=client,
            def_indexes=[def_index],
            sales_rows=10,
        )
        stats_1 = run_pin_watch_poll(storage=self.storage, client=client, notifier=notifier, sales_rows=10)
        stats_2 = run_pin_watch_poll(storage=self.storage, client=client, notifier=notifier, sales_rows=10)

        self.assertEqual(1, stats_1.sale_alerts_sent)
        self.assertEqual(0, stats_2.sale_alerts_sent)
        self.assertEqual(1, len(notifier.sale_alerts))
        sale_price, lowest_known, premium_pct = notifier.sale_alerts[0]
        self.assertEqual(5400, sale_price)
        self.assertEqual(4800, lowest_known)
        self.assertAlmostEqual(12.5, premium_pct, places=2)


if __name__ == "__main__":
    unittest.main()
