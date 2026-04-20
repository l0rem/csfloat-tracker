from __future__ import annotations

import json
import logging
import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx

from csfloat_monitor.types import ListingRecord, PinSaleRecord


STEAM_ICON_URL_PREFIX = "https://steamcommunity-a.akamaihd.net/economy/image/"


class CSFloatClient:
    def __init__(
        self,
        api_key: str,
        listings_url: str,
        item_url_template: str,
        screenshot_url_template: str,
        timeout_seconds: float = 15,
        max_retries: int = 3,
        max_429_retries: int = 1,
        backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 90.0,
        page_delay_seconds: float = 0.35,
        proxy: str | None = None,
        client: httpx.Client | None = None,
    ):
        self._api_key = api_key
        self._listings_url = listings_url
        self._item_url_template = item_url_template
        self._screenshot_url_template = screenshot_url_template
        self._max_retries = max(1, max_retries)
        self._max_429_retries = max(0, max_429_retries)
        self._backoff_seconds = max(0.1, backoff_seconds)
        self._max_backoff_seconds = max(self._backoff_seconds, max_backoff_seconds)
        self._page_delay_seconds = max(0.0, page_delay_seconds)
        self._client = client or httpx.Client(timeout=timeout_seconds, proxy=proxy)
        self._owns_client = client is None
        self._log = logging.getLogger("csfloat.client")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_all_listings(self) -> dict[str, ListingRecord]:
        records: dict[str, ListingRecord] = {}
        cursor: str | None = None

        is_first_page = True
        page_number = 1
        while True:
            if not is_first_page and self._page_delay_seconds > 0:
                time.sleep(self._page_delay_seconds)
            payload = self._request_page(cursor)
            page_data = payload.get("data", [])
            self._log.info(
                "fetch_page_success page=%d cursor=%s items=%d has_next=%s",
                page_number,
                bool(cursor),
                len(page_data),
                bool(payload.get("cursor")),
            )
            for listing_payload in payload.get("data", []):
                record = self._normalize_listing(listing_payload)
                records[record.listing_id] = record

            cursor = payload.get("cursor")
            if not cursor:
                break
            is_first_page = False
            page_number += 1

        return records

    def fetch_lowest_listing(self, def_index: int) -> ListingRecord | None:
        url = self._build_listings_url_for_def_index(def_index)
        self._log.info("fetch_lowest_listing_start def_index=%s", def_index)
        payload = self._request_json(url)
        rows = payload.get("data", [])
        if not rows:
            self._log.warning("fetch_lowest_listing_empty def_index=%s", def_index)
            return None
        record = self._normalize_listing(rows[0])
        self._log.info(
            "fetch_lowest_listing_success def_index=%s listing_id=%s price=%s market_hash_name=%s",
            def_index,
            record.listing_id,
            record.price,
            record.market_hash_name,
        )
        return record

    def fetch_sales_history(self, market_hash_name: str) -> list[PinSaleRecord]:
        encoded_name = quote(market_hash_name, safe="")
        url = f"https://csfloat.com/api/v1/history/{encoded_name}/sales"
        self._log.info("fetch_sales_history_start market_hash_name=%s", market_hash_name)
        payload = self._request_json(url)
        if not isinstance(payload, list):
            self._log.warning("fetch_sales_history_unexpected_payload market_hash_name=%s", market_hash_name)
            return []
        sales: list[PinSaleRecord] = []
        for row in payload:
            raw_price = row.get("price")
            if raw_price is None:
                continue
            try:
                sale_price = int(raw_price)
            except (TypeError, ValueError):
                continue
            sales.append(
                PinSaleRecord(
                    sale_price=sale_price,
                    sold_at=row.get("sold_at"),
                    listing_id=str(row.get("id")) if row.get("id") is not None else None,
                )
            )
        self._log.info("fetch_sales_history_success market_hash_name=%s rows=%d", market_hash_name, len(sales))
        return sales

    def buy_now(self, *, listing_id: str, total_price: int) -> dict[str, Any]:
        payload = {
            "total_price": int(total_price),
            "contract_ids": [str(listing_id)],
        }
        self._log.info("buy_now_start listing_id=%s total_price=%s", listing_id, total_price)
        result = self._request_json(
            "https://csfloat.com/api/v1/listings/buy",
            method="POST",
            json_payload=payload,
        )
        self._log.info("buy_now_success listing_id=%s total_price=%s", listing_id, total_price)
        return result

    def _request_page(self, cursor: str | None) -> dict[str, Any]:
        url = self._with_cursor(self._listings_url, cursor)
        return self._request_json(url)

    def _request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        json_payload: Any | None = None,
    ) -> Any:

        last_exc: Exception | None = None
        retry_429_used = 0
        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._client.request(
                    method,
                    url,
                    headers={"Authorization": self._api_key},
                    json=json_payload,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    status_exc = httpx.HTTPStatusError(
                        f"Transient HTTP status: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    last_exc = status_exc
                    if attempt == self._max_retries:
                        break
                    if response.status_code == 429 and retry_429_used >= self._max_429_retries:
                        self._log.warning(
                            "fetch_429_budget_exhausted attempt=%d/%d retry_429_used=%d",
                            attempt,
                            self._max_retries,
                            retry_429_used,
                        )
                        break
                    if response.status_code == 429:
                        retry_429_used += 1
                    delay = self._compute_retry_delay(attempt, response)
                    self._log.warning(
                        "fetch_transient_error status=%d attempt=%d/%d retry_in=%.2fs",
                        response.status_code,
                        attempt,
                        self._max_retries,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt == self._max_retries:
                    break
                delay = self._compute_retry_delay(attempt, None)
                self._log.warning(
                    "fetch_retryable_exception attempt=%d/%d retry_in=%.2fs error=%s",
                    attempt,
                    self._max_retries,
                    delay,
                    exc,
                )
                time.sleep(delay)

        raise RuntimeError(
            f"Failed to fetch CSFloat listings after {self._max_retries} attempts "
            f"(429 retries used: {retry_429_used}/{self._max_429_retries}): {last_exc}"
        )

    @staticmethod
    def _build_listings_url_for_def_index(def_index: int) -> str:
        return f"https://csfloat.com/api/v1/listings?limit=1&sort_by=lowest_price&def_index={int(def_index)}"

    def _compute_retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        # Honor Retry-After for explicit server-side throttling.
        if response is not None and response.status_code == 429:
            retry_after = self._parse_retry_after_seconds(response.headers.get("Retry-After"))
            if retry_after is not None:
                return min(max(retry_after, self._backoff_seconds), self._max_backoff_seconds)
            # Without Retry-After, be conservative to avoid burst retries.
            base_429 = max(10.0, self._backoff_seconds * (2 ** (attempt - 1)))
            jitter_429 = random.uniform(0, 2.0)
            return min(base_429 + jitter_429, self._max_backoff_seconds)

        base = self._backoff_seconds * (2 ** (attempt - 1))
        jitter = random.uniform(0, self._backoff_seconds * 0.25)
        return min(base + jitter, self._max_backoff_seconds)

    @staticmethod
    def _parse_retry_after_seconds(raw: str | None) -> float | None:
        if not raw:
            return None
        value = raw.strip()
        try:
            return float(value)
        except ValueError:
            pass

        try:
            retry_dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None

        if retry_dt.tzinfo is None:
            retry_dt = retry_dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        delta = (retry_dt - now).total_seconds()
        return max(0.0, delta)

    def _normalize_listing(self, payload: dict[str, Any]) -> ListingRecord:
        listing_id = str(payload.get("id", "")).strip()
        if not listing_id:
            raise ValueError(f"Listing is missing id: {payload}")

        item = payload.get("item") or {}
        listing_url = self._item_url_template.format(listing_id=listing_id)
        raw_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        screenshot_url = self._screenshot_url(item)
        icon_image_url = self._icon_image_url(item)
        inspect_link = self._inspect_link(item)

        return ListingRecord(
            listing_id=listing_id,
            listing_url=listing_url,
            price=payload.get("price"),
            state=payload.get("state"),
            market_hash_name=item.get("market_hash_name"),
            item_name=item.get("item_name"),
            wear_name=item.get("wear_name"),
            float_value=item.get("float_value"),
            created_at=payload.get("created_at"),
            raw_json=raw_json,
            screenshot_url=screenshot_url,
            image_url=screenshot_url or icon_image_url,
            inspect_link=inspect_link,
            seller_description=self._seller_description(payload),
        )

    def _screenshot_url(self, item: dict[str, Any]) -> str | None:
        screenshot_id = item.get("cs2_screenshot_id")
        if screenshot_id in {None, ""}:
            return None
        return self._screenshot_url_template.format(screenshot_id=screenshot_id)

    @staticmethod
    def _icon_image_url(item: dict[str, Any]) -> str | None:
        icon_url = item.get("icon_url")
        if icon_url in {None, ""}:
            return None
        icon_value = str(icon_url)
        if icon_value.startswith("http://") or icon_value.startswith("https://"):
            return icon_value
        return f"{STEAM_ICON_URL_PREFIX}{icon_value}"

    @staticmethod
    def _inspect_link(item: dict[str, Any]) -> str | None:
        inspect_link = item.get("inspect_link") or item.get("serialized_inspect")
        if inspect_link in {None, ""}:
            return None
        return str(inspect_link)

    @staticmethod
    def _seller_description(payload: dict[str, Any]) -> str | None:
        description = payload.get("description")
        if description in {None, ""}:
            return None
        return str(description)

    @staticmethod
    def _with_cursor(url: str, cursor: str | None) -> str:
        split = urlsplit(url)
        params = dict(parse_qsl(split.query, keep_blank_values=True))
        if cursor:
            params["cursor"] = cursor
        else:
            params.pop("cursor", None)

        return urlunsplit((split.scheme, split.netloc, split.path, urlencode(params), split.fragment))
