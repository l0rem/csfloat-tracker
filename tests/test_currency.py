from __future__ import annotations

import unittest

import httpx

from csfloat_monitor.currency import CSFloatCurrencyPriceFormatter


class CurrencyFormatterTests(unittest.TestCase):
    def test_uses_csfloat_exchange_rate_and_caches_it(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            self.assertEqual("/api/v1/meta/exchange-rates", request.url.path)
            return httpx.Response(200, json={"data": {"eur": 0.8}})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        formatter = CSFloatCurrencyPriceFormatter(
            api_key="test-key",
            target_currency="EUR",
            max_retries=1,
            cache_ttl_seconds=300,
            client=client,
        )

        self.assertEqual("€0.80", formatter.format_price("100"))
        self.assertEqual("€1.60", formatter.format_price("200"))
        self.assertEqual(1, call_count)

    def test_returns_target_currency_unavailable_when_missing(self) -> None:
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"data": {"usd": 1}})
            )
        )
        formatter = CSFloatCurrencyPriceFormatter(
            api_key="test-key",
            target_currency="EUR",
            max_retries=1,
            client=client,
        )

        self.assertEqual("€n/a", formatter.format_price("150"))
