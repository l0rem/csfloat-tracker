from __future__ import annotations

import unittest

from csfloat_monitor.telegram_notifier import (
    build_send_payload,
    build_send_photo_payload,
    format_change_message,
)
from csfloat_monitor.types import (
    CHANGE_DELISTED,
    CHANGE_NEW,
    CHANGE_PRICE_CHANGED,
    ChangeSet,
    FieldDelta,
)


class DummyPriceFormatter:
    def format_price(self, raw: str | None) -> str:
        return f"EUR({raw})"

    def close(self) -> None:
        return None


class TelegramNotifierTests(unittest.TestCase):
    def test_message_contains_old_and_new_values(self) -> None:
        change = ChangeSet(
            listing_id="123",
            change_type=CHANGE_PRICE_CHANGED,
            listing_url="https://csfloat.com/item/123",
            market_hash_name="Item Name",
            float_value=0.31415926,
            deltas=[FieldDelta(field_name="price", old_value="100", new_value="150")],
        )
        message = format_change_message(change, price_formatter=DummyPriceFormatter())

        self.assertIn("💶 <b>Price:</b>", message)
        self.assertIn("EUR(100)", message)
        self.assertIn("EUR(150)", message)
        self.assertIn("🧪 <b>Float:</b>", message)
        self.assertIn("→", message)

    def test_message_can_use_custom_price_formatter(self) -> None:
        change = ChangeSet(
            listing_id="123",
            change_type=CHANGE_NEW,
            listing_url="https://csfloat.com/item/123",
            market_hash_name="Item Name",
            float_value=0.123456789,
            seller_description="double wave thumb",
            inspect_link="steam://rungame/730/76561202255233023/+csgo_econ_action_preview%200018AA...",
            deltas=[
                FieldDelta(field_name="price", old_value="n/a", new_value="150"),
                FieldDelta(field_name="float_value", old_value="n/a", new_value="0.123456789"),
            ],
        )
        message = format_change_message(change, price_formatter=DummyPriceFormatter())

        self.assertIn("EUR(150)", message)
        self.assertIn("🧪 <b>Float:</b>", message)
        self.assertIn("📝 <b>Seller Note:</b>", message)
        self.assertIn("double wave thumb", message)
        self.assertIn("🔎 <b>Inspect Link</b>", message)
        self.assertIn("<pre><code>", message)
        self.assertNotIn("n/a", message.lower())

    def test_delisted_message_includes_float(self) -> None:
        change = ChangeSet(
            listing_id="123",
            change_type=CHANGE_DELISTED,
            listing_url="https://csfloat.com/item/123",
            market_hash_name="Item Name",
            float_value=0.7654321,
            deltas=[
                FieldDelta(field_name="state", old_value="listed", new_value="delisted"),
                FieldDelta(field_name="price", old_value="100", new_value="n/a"),
            ],
        )
        message = format_change_message(change, price_formatter=DummyPriceFormatter())
        self.assertIn("🧪 <b>Float:</b>", message)

    def test_inline_button_only_for_new_and_price_change(self) -> None:
        base_kwargs = {
            "listing_id": "123",
            "listing_url": "https://csfloat.com/item/123",
            "market_hash_name": "Item Name",
            "deltas": [FieldDelta(field_name="price", old_value="100", new_value="150")],
        }
        payload_new = build_send_payload("111", ChangeSet(change_type=CHANGE_NEW, **base_kwargs))
        payload_price = build_send_payload("111", ChangeSet(change_type=CHANGE_PRICE_CHANGED, **base_kwargs))
        payload_delisted = build_send_payload("111", ChangeSet(change_type=CHANGE_DELISTED, **base_kwargs))

        self.assertIn("reply_markup", payload_new)
        self.assertIn("reply_markup", payload_price)
        self.assertNotIn("reply_markup", payload_delisted)
        self.assertEqual("HTML", payload_new.get("parse_mode"))

    def test_market_line_is_appended_to_payload(self) -> None:
        change = ChangeSet(
            listing_id="123",
            change_type=CHANGE_PRICE_CHANGED,
            listing_url="https://csfloat.com/item/123",
            market_hash_name="Item Name",
            deltas=[FieldDelta(field_name="price", old_value="100", new_value="150")],
        )
        payload = build_send_payload("111", change, price_formatter=DummyPriceFormatter(), market_line="📊 market line")
        self.assertIn("📊 market line", payload["text"])

    def test_photo_payload_includes_image_and_caption(self) -> None:
        change = ChangeSet(
            listing_id="123",
            change_type=CHANGE_NEW,
            listing_url="https://csfloat.com/item/123",
            market_hash_name="Item Name",
            deltas=[FieldDelta(field_name="price", old_value="n/a", new_value="150")],
            image_url="https://csfloat.pics/m/123/playside.png?v=3",
        )
        payload = build_send_photo_payload("111", change, price_formatter=DummyPriceFormatter())

        self.assertEqual("https://csfloat.pics/m/123/playside.png?v=3", payload.get("photo"))
        self.assertIn("caption", payload)
        self.assertEqual("HTML", payload.get("parse_mode"))
        self.assertIn("reply_markup", payload)
