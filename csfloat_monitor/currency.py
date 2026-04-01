from __future__ import annotations

import logging
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

import httpx


DEFAULT_EXCHANGE_RATES_URL = "https://csfloat.com/api/v1/meta/exchange-rates"


class PriceFormatter(Protocol):
    def format_price(self, raw: str | None) -> str: ...

    def close(self) -> None: ...


class UsdPriceFormatter:
    def format_price(self, raw: str | None) -> str:
        if raw in {None, "n/a"}:
            return "n/a"
        try:
            cents = int(raw)
        except ValueError:
            return str(raw)

        usd_amount = Decimal(cents) / Decimal(100)
        return f"${usd_amount:.2f}"

    def close(self) -> None:
        return None


class CSFloatCurrencyPriceFormatter:
    def __init__(
        self,
        api_key: str,
        target_currency: str = "EUR",
        rates_url: str = DEFAULT_EXCHANGE_RATES_URL,
        timeout_seconds: float = 10,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        cache_ttl_seconds: int = 300,
        proxy: str | None = None,
        client: httpx.Client | None = None,
    ):
        self._api_key = api_key
        self._target_currency = (target_currency or "EUR").strip().upper()
        self._rates_url = rates_url
        self._max_retries = max(1, int(max_retries))
        self._backoff_seconds = max(0.1, float(backoff_seconds))
        self._cache_ttl_seconds = max(10, int(cache_ttl_seconds))
        self._client = client or httpx.Client(timeout=timeout_seconds, proxy=proxy)
        self._owns_client = client is None
        self._cached_rate: float | None = None
        self._cached_at_monotonic: float | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def format_price(self, raw: str | None) -> str:
        if raw in {None, "n/a"}:
            return "n/a"
        try:
            cents = int(raw)
        except ValueError:
            return str(raw)

        usd_amount = Decimal(cents) / Decimal(100)
        rate = self._get_rate()
        symbol = _currency_symbol(self._target_currency)
        if rate is None:
            return f"{symbol}n/a" if symbol else f"{self._target_currency} n/a"

        converted = (usd_amount * Decimal(str(rate))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if symbol:
            converted_text = f"{symbol}{converted:.2f}"
        else:
            converted_text = f"{self._target_currency} {converted:.2f}"

        return converted_text

    def _get_rate(self) -> float | None:
        if self._cached_rate is not None and self._cached_at_monotonic is not None:
            age = time.monotonic() - self._cached_at_monotonic
            if age < self._cache_ttl_seconds:
                return self._cached_rate

        try:
            rate = self._fetch_rate()
            self._cached_rate = rate
            self._cached_at_monotonic = time.monotonic()
            return rate
        except Exception as exc:  # noqa: BLE001
            logging.warning("Falling back to cached %s rate because lookup failed: %s", self._target_currency, exc)
            return self._cached_rate

    def _fetch_rate(self) -> float:
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._client.get(
                    self._rates_url,
                    headers={"Authorization": self._api_key},
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        f"Transient HTTP status: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()

                payload = response.json()
                data = payload.get("data") or {}
                rate = data.get(self._target_currency.lower())
                if rate is None:
                    raise RuntimeError(f"Currency {self._target_currency} is missing from exchange-rates payload")
                return float(rate)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError, ValueError) as exc:
                last_exc = exc
                if attempt == self._max_retries:
                    break
                time.sleep(self._backoff_seconds * (2 ** (attempt - 1)))

        raise RuntimeError(f"Failed to fetch exchange rates from CSFloat: {last_exc}")


def _currency_symbol(code: str) -> str | None:
    symbols = {
        "EUR": "€",
        "USD": "$",
        "GBP": "£",
    }
    return symbols.get(code.upper())
