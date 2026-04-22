from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx

from csfloat_monitor.csfloat_client import CSFloatClient


class CSFloatClientTests(unittest.TestCase):
    def test_fetch_all_listings_paginates_with_cursor(self) -> None:
        client = CSFloatClient(
            api_key="test",
            listings_url="https://csfloat.com/api/v1/listings?limit=1&paint_index=1437",
            item_url_template="https://csfloat.com/item/{listing_id}",
            screenshot_url_template="https://csfloat.pics/m/{screenshot_id}/playside.png?v=3",
        )

        page_1 = {
            "data": [
                {"id": "1", "price": 100, "state": "listed", "item": {"market_hash_name": "A"}},
            ],
            "cursor": "next_cursor",
        }
        page_2 = {
            "data": [
                {
                    "id": "2",
                    "price": 200,
                    "state": "listed",
                    "item": {
                        "market_hash_name": "B",
                        "cs2_screenshot_id": "8437643956702555280",
                        "inspect_link": "steam://inspect/B",
                    },
                    "description": "double wave thumb",
                },
            ],
        }

        with patch.object(client, "_request_page", side_effect=[page_1, page_2]) as mocked_page:
            records = client.fetch_all_listings()

        self.assertEqual(2, len(records))
        self.assertIn("1", records)
        self.assertIn("2", records)
        self.assertEqual([None, "next_cursor"], [call.args[0] for call in mocked_page.call_args_list])
        self.assertIsNone(records["1"].screenshot_url)
        self.assertIsNone(records["1"].image_url)
        self.assertEqual(
            "https://csfloat.pics/m/8437643956702555280/playside.png?v=3",
            records["2"].screenshot_url,
        )
        self.assertEqual(records["2"].screenshot_url, records["2"].image_url)
        self.assertEqual("steam://inspect/B", records["2"].inspect_link)
        self.assertEqual("double wave thumb", records["2"].seller_description)
        client.close()

    def test_retries_429_and_recovers(self) -> None:
        calls = {"count": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            if calls["count"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate_limited"})
            return httpx.Response(200, json={"data": []})

        client = CSFloatClient(
            api_key="test",
            listings_url="https://csfloat.com/api/v1/listings?limit=1&paint_index=1437",
            item_url_template="https://csfloat.com/item/{listing_id}",
            screenshot_url_template="https://csfloat.pics/m/{screenshot_id}/playside.png?v=3",
            max_retries=3,
            max_429_retries=1,
            backoff_seconds=0.1,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        with patch("csfloat_monitor.csfloat_client.time.sleep") as sleep_mock:
            records = client.fetch_all_listings()

        self.assertEqual({}, records)
        self.assertEqual(2, calls["count"])
        self.assertTrue(sleep_mock.called)
        client.close()

    def test_stops_429_retries_after_budget(self) -> None:
        calls = {"count": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(429, json={"error": "rate_limited"})

        client = CSFloatClient(
            api_key="test",
            listings_url="https://csfloat.com/api/v1/listings?limit=1&paint_index=1437",
            item_url_template="https://csfloat.com/item/{listing_id}",
            screenshot_url_template="https://csfloat.pics/m/{screenshot_id}/playside.png?v=3",
            max_retries=8,
            max_429_retries=1,
            backoff_seconds=0.1,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        with patch("csfloat_monitor.csfloat_client.time.sleep"):
            with self.assertRaises(RuntimeError):
                client.fetch_all_listings()

        self.assertEqual(2, calls["count"])
        client.close()

    def test_fetch_lowest_listing_uses_def_index_endpoint(self) -> None:
        captured_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_urls.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "10",
                            "price": 1234,
                            "state": "listed",
                            "item": {"market_hash_name": "Test Pin"},
                        }
                    ]
                },
            )

        client = CSFloatClient(
            api_key="test",
            listings_url="https://csfloat.com/api/v1/listings",
            item_url_template="https://csfloat.com/item/{listing_id}",
            screenshot_url_template="https://csfloat.pics/m/{screenshot_id}/playside.png?v=3",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        record = client.fetch_lowest_listing(6121)
        self.assertIsNotNone(record)
        self.assertIn("def_index=6121", captured_urls[0])
        self.assertIn("limit=1", captured_urls[0])
        self.assertEqual("10", record.listing_id if record else "")
        client.close()

    def test_fetch_cheapest_listings_uses_requested_limit(self) -> None:
        captured_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_urls.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "10",
                            "price": 1234,
                            "state": "listed",
                            "item": {"market_hash_name": "Test Pin"},
                        },
                        {
                            "id": "11",
                            "price": 1260,
                            "state": "listed",
                            "item": {"market_hash_name": "Test Pin"},
                        },
                    ]
                },
            )

        client = CSFloatClient(
            api_key="test",
            listings_url="https://csfloat.com/api/v1/listings",
            item_url_template="https://csfloat.com/item/{listing_id}",
            screenshot_url_template="https://csfloat.pics/m/{screenshot_id}/playside.png?v=3",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        rows = client.fetch_cheapest_listings(6121, limit=2)
        self.assertEqual(2, len(rows))
        self.assertIn("def_index=6121", captured_urls[0])
        self.assertIn("limit=2", captured_urls[0])
        client.close()

    def test_fetch_sales_history_parses_rows(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {"id": "x1", "price": 5000, "sold_at": "2026-04-20T00:00:00Z"},
                    {"id": "x2", "price": 4900, "sold_at": "2026-04-19T00:00:00Z"},
                ],
            )

        client = CSFloatClient(
            api_key="test",
            listings_url="https://csfloat.com/api/v1/listings",
            item_url_template="https://csfloat.com/item/{listing_id}",
            screenshot_url_template="https://csfloat.pics/m/{screenshot_id}/playside.png?v=3",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        rows = client.fetch_sales_history("Valeria Phoenix Pin")
        self.assertEqual(2, len(rows))
        self.assertEqual(5000, rows[0].sale_price)
        self.assertEqual("x1", rows[0].listing_id)
        client.close()

    def test_fetch_sales_history_sorts_newest_first(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {"id": "x-old", "price": 4900, "sold_at": "2026-04-19T00:00:00Z"},
                    {"id": "x-new", "price": 5000, "sold_at": "2026-04-20T00:00:00Z"},
                ],
            )

        client = CSFloatClient(
            api_key="test",
            listings_url="https://csfloat.com/api/v1/listings",
            item_url_template="https://csfloat.com/item/{listing_id}",
            screenshot_url_template="https://csfloat.pics/m/{screenshot_id}/playside.png?v=3",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        rows = client.fetch_sales_history("Valeria Phoenix Pin")
        self.assertEqual(2, len(rows))
        self.assertEqual("x-new", rows[0].listing_id)
        self.assertEqual("x-old", rows[1].listing_id)
        client.close()

    def test_buy_now_posts_bulk_buy_payload(self) -> None:
        captured_json: list[dict] = []
        captured_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_paths.append(request.url.path)
            captured_json.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"ok": True})

        client = CSFloatClient(
            api_key="test",
            listings_url="https://csfloat.com/api/v1/listings",
            item_url_template="https://csfloat.com/item/{listing_id}",
            screenshot_url_template="https://csfloat.pics/m/{screenshot_id}/playside.png?v=3",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        result = client.buy_now(listing_id="123", total_price=4500)
        self.assertTrue(result.get("ok"))
        self.assertEqual("/api/v1/listings/buy", captured_paths[0])
        self.assertEqual({"total_price": 4500, "contract_ids": ["123"]}, captured_json[0])
        client.close()
