from __future__ import annotations

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
                    },
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
