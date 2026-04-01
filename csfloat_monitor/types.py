from __future__ import annotations

from dataclasses import dataclass, field

CHANGE_NEW = "new"
CHANGE_PRICE_CHANGED = "price_changed"
CHANGE_DELISTED = "delisted"


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
