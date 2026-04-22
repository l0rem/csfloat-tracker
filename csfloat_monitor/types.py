from __future__ import annotations

from dataclasses import dataclass, field

CHANGE_NEW = "new"
CHANGE_PRICE_CHANGED = "price_changed"
CHANGE_DELISTED = "delisted"
CHANGE_TRACKED_REMOVED = "tracked_removed"


@dataclass(slots=True)
class ListingRecord:
    listing_id: str
    listing_url: str
    price: int | None
    state: str | None
    market_hash_name: str | None
    item_name: str | None
    wear_name: str | None
    float_value: float | None
    created_at: str | None
    raw_json: str
    screenshot_url: str | None = None
    image_url: str | None = None
    inspect_link: str | None = None
    seller_description: str | None = None


@dataclass(slots=True)
class FieldDelta:
    field_name: str
    old_value: str | None
    new_value: str | None


@dataclass(slots=True)
class ChangeSet:
    listing_id: str
    change_type: str
    listing_url: str | None
    market_hash_name: str | None
    float_value: float | None = None
    seller_description: str | None = None
    deltas: list[FieldDelta] = field(default_factory=list)
    screenshot_url: str | None = None
    image_url: str | None = None
    inspect_link: str | None = None


@dataclass(slots=True)
class PinSaleRecord:
    sale_price: int
    sold_at: str | None = None
    listing_id: str | None = None


@dataclass(slots=True)
class PinAlert:
    def_index: int
    market_hash_name: str
    listing_id: str
    listing_price: int
    listing_url: str
    image_url: str | None
    trigger_type: str  # "new_cheapest_current"
    previous_lowest_price: int
    absolute_lowest_price: int
    absolute_drop_price: int
    absolute_drop_percent: float
    cheapest_sale_price: int | None
    percent_below_cheapest_sale: float | None
    recent_sales: list[PinSaleRecord] = field(default_factory=list)


@dataclass(slots=True)
class PinSaleAlert:
    def_index: int
    market_hash_name: str
    sale_price: int
    lowest_known_price: int
    percent_above_lowest_known: float
    sold_at: str | None = None
    sale_listing_id: str | None = None
    image_url: str | None = None
    listing_url: str | None = None
