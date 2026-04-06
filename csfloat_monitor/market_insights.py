from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass

from csfloat_monitor.currency import PriceFormatter
from csfloat_monitor.models import ItemChange
from csfloat_monitor.types import ChangeSet


@dataclass(slots=True)
class SoldStats:
    average_price_cents: float
    sample_size: int


class DelistedMarketAnalyzer:
    def __init__(self, cache_ttl_seconds: int = 300, min_samples: int = 3):
        self._cache_ttl_seconds = max(10, cache_ttl_seconds)
        self._min_samples = max(1, min_samples)
        self._stats_by_market_hash_name: dict[str, SoldStats] = {}
        self._last_refresh_monotonic: float | None = None
        self._log = logging.getLogger("csfloat.market")

    def build_market_line(self, change: ChangeSet, price_formatter: PriceFormatter) -> str:
        listing_name = (change.market_hash_name or "").strip()
        if not listing_name:
            return "📊 <b>Vs sold avg:</b> <code>n/a</code>"

        price_cents = self._extract_price_cents_for_comparison(change)
        if price_cents is None:
            return "📊 <b>Vs sold avg:</b> <code>n/a</code>"

        stats = self._get_stats_for_listing(listing_name)
        if stats is None or stats.sample_size < self._min_samples:
            return f"📊 <b>Vs sold avg:</b> <code>n/a</code> (need {self._min_samples}+ sold samples)"

        average = stats.average_price_cents
        if average <= 0:
            return "📊 <b>Vs sold avg:</b> <code>n/a</code>"

        diff_pct = ((price_cents - average) / average) * 100
        sign = "+" if diff_pct >= 0 else ""
        avg_display = price_formatter.format_price(str(int(round(average))))
        return (
            f"📊 <b>Vs sold avg ({stats.sample_size}):</b> "
            f"<code>{sign}{diff_pct:.1f}%</code> (avg <code>{avg_display}</code>)"
        )

    def _get_stats_for_listing(self, market_hash_name: str) -> SoldStats | None:
        self._refresh_cache_if_needed()
        return self._stats_by_market_hash_name.get(market_hash_name)

    def _refresh_cache_if_needed(self) -> None:
        if self._last_refresh_monotonic is not None:
            age = time.monotonic() - self._last_refresh_monotonic
            if age < self._cache_ttl_seconds:
                return

        listing_to_market_name: dict[str, str] = {}
        for row in (
            ItemChange.select(
                ItemChange.listing_id,
                ItemChange.field_name,
                ItemChange.old_value,
                ItemChange.new_value,
            )
            .where(ItemChange.field_name == "market_hash_name")
            .order_by(ItemChange.id.asc())
        ):
            value = _pick_market_hash_name(row.old_value, row.new_value)
            if value:
                listing_to_market_name[row.listing_id] = value

        totals: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for row in (
            ItemChange.select(ItemChange.listing_id, ItemChange.old_value, ItemChange.change_type, ItemChange.field_name)
            .where(
                ItemChange.change_type == "delisted",
                ItemChange.field_name == "price",
            )
            .order_by(ItemChange.id.asc())
        ):
            market_hash_name = listing_to_market_name.get(row.listing_id)
            if not market_hash_name:
                continue
            price = _to_int(row.old_value)
            if price is None:
                continue
            totals[market_hash_name] += price
            counts[market_hash_name] += 1

        stats: dict[str, SoldStats] = {}
        for market_hash_name, total in totals.items():
            sample_size = counts[market_hash_name]
            if sample_size <= 0:
                continue
            stats[market_hash_name] = SoldStats(average_price_cents=total / sample_size, sample_size=sample_size)

        self._stats_by_market_hash_name = stats
        self._last_refresh_monotonic = time.monotonic()
        self._log.debug("market_stats_refreshed groups=%d", len(stats))

    @staticmethod
    def _extract_price_cents_for_comparison(change: ChangeSet) -> int | None:
        for delta in change.deltas:
            if delta.field_name != "price":
                continue
            raw = delta.old_value if change.change_type == "delisted" else delta.new_value
            return _to_int(raw)
        return None


def _to_int(raw: str | None) -> int | None:
    if raw in {None, "", "n/a"}:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _pick_market_hash_name(old_value: str | None, new_value: str | None) -> str | None:
    for value in (new_value, old_value):
        if value in {None, "", "n/a"}:
            continue
        return value
    return None
