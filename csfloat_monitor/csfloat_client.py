from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from csfloat_monitor.types import ListingRecord


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
        backoff_seconds: float = 1.0,
    ):
        self._api_key = api_key
        self._listings_url = listings_url
        self._item_url_template = item_url_template
        self._screenshot_url_template = screenshot_url_template
        self._max_retries = max(1, max_retries)
        self._backoff_seconds = max(0.1, backoff_seconds)
        self._client = httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self._client.close()

    def fetch_all_listings(self) -> dict[str, ListingRecord]:
        records: dict[str, ListingRecord] = {}
        cursor: str | None = None

        while True:
            payload = self._request_page(cursor)
            for listing_payload in payload.get("data", []):
                record = self._normalize_listing(listing_payload)
                records[record.listing_id] = record

            cursor = payload.get("cursor")
            if not cursor:
                break

        return records

    def _request_page(self, cursor: str | None) -> dict[str, Any]:
        url = self._with_cursor(self._listings_url, cursor)

        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._client.get(
                    url,
                    headers={"Authorization": self._api_key},
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        f"Transient HTTP status: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt == self._max_retries:
                    break
                time.sleep(self._backoff_seconds * (2 ** (attempt - 1)))

        raise RuntimeError(f"Failed to fetch CSFloat listings: {last_exc}")

    def _normalize_listing(self, payload: dict[str, Any]) -> ListingRecord:
        listing_id = str(payload.get("id", "")).strip()
        if not listing_id:
            raise ValueError(f"Listing is missing id: {payload}")

        item = payload.get("item") or {}
        listing_url = self._item_url_template.format(listing_id=listing_id)
        raw_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        screenshot_url = self._screenshot_url(item)
        icon_image_url = self._icon_image_url(item)

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
    def _with_cursor(url: str, cursor: str | None) -> str:
        split = urlsplit(url)
        params = dict(parse_qsl(split.query, keep_blank_values=True))
        if cursor:
            params["cursor"] = cursor
        else:
            params.pop("cursor", None)

        return urlunsplit((split.scheme, split.netloc, split.path, urlencode(params), split.fragment))
