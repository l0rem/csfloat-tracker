from __future__ import annotations

from csfloat_monitor.types import (
    CHANGE_DELISTED,
    CHANGE_NEW,
    CHANGE_PRICE_CHANGED,
    ChangeSet,
    FieldDelta,
    ListingRecord,
)


def _to_text(value: object | None) -> str | None:
    if value is None:
        return None
    return str(value)


def diff_listings(
    previous: dict[str, ListingRecord],
    current: dict[str, ListingRecord],
) -> list[ChangeSet]:
    changes: list[ChangeSet] = []

    previous_ids = set(previous)
    current_ids = set(current)

    new_ids = sorted(current_ids - previous_ids)
    for listing_id in new_ids:
        listing = current[listing_id]
        deltas = [
            FieldDelta("price", "n/a", _to_text(listing.price)),
            FieldDelta("state", "n/a", _to_text(listing.state)),
        ]
        if listing.market_hash_name:
            deltas.append(FieldDelta("market_hash_name", "n/a", listing.market_hash_name))
        if listing.float_value is not None:
            deltas.append(FieldDelta("float_value", "n/a", _to_text(listing.float_value)))

        changes.append(
            ChangeSet(
                listing_id=listing_id,
                change_type=CHANGE_NEW,
                listing_url=listing.listing_url,
                market_hash_name=listing.market_hash_name,
                float_value=listing.float_value,
                seller_description=listing.seller_description,
                deltas=deltas,
                screenshot_url=listing.screenshot_url,
                image_url=listing.image_url or listing.screenshot_url,
                inspect_link=listing.inspect_link,
            )
        )

    shared_ids = sorted(current_ids & previous_ids)
    for listing_id in shared_ids:
        old_listing = previous[listing_id]
        new_listing = current[listing_id]

        deltas: list[FieldDelta] = []
        if old_listing.price != new_listing.price:
            deltas.append(FieldDelta("price", _to_text(old_listing.price), _to_text(new_listing.price)))

        if deltas:
            changes.append(
                ChangeSet(
                    listing_id=listing_id,
                    change_type=CHANGE_PRICE_CHANGED,
                    listing_url=new_listing.listing_url,
                    market_hash_name=new_listing.market_hash_name,
                    float_value=new_listing.float_value,
                    seller_description=new_listing.seller_description,
                    deltas=deltas,
                    screenshot_url=new_listing.screenshot_url,
                    image_url=new_listing.image_url or new_listing.screenshot_url,
                    inspect_link=new_listing.inspect_link,
                )
            )

    delisted_ids = sorted(previous_ids - current_ids)
    for listing_id in delisted_ids:
        old_listing = previous[listing_id]
        deltas = [
            FieldDelta("state", _to_text(old_listing.state), "delisted"),
            FieldDelta("price", _to_text(old_listing.price), "n/a"),
        ]
        changes.append(
            ChangeSet(
                listing_id=listing_id,
                change_type=CHANGE_DELISTED,
                listing_url=old_listing.listing_url,
                market_hash_name=old_listing.market_hash_name,
                float_value=old_listing.float_value,
                seller_description=old_listing.seller_description,
                deltas=deltas,
                screenshot_url=old_listing.screenshot_url,
                image_url=old_listing.image_url or old_listing.screenshot_url,
                inspect_link=old_listing.inspect_link,
            )
        )

    return changes
