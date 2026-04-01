from __future__ import annotations

import html
import logging
from typing import Any

import httpx

from csfloat_monitor.currency import PriceFormatter, UsdPriceFormatter
from csfloat_monitor.types import CHANGE_NEW, CHANGE_PRICE_CHANGED, ChangeSet


DEFAULT_PRICE_FORMATTER = UsdPriceFormatter()

EVENT_META: dict[str, tuple[str, str]] = {
    CHANGE_NEW: ("🆕", "New Listing"),
    CHANGE_PRICE_CHANGED: ("💸", "Price Updated"),
    "delisted": ("🛑", "Listing Delisted"),
}

def _format_value(field_name: str, value: str | None, price_formatter: PriceFormatter) -> str:
    if value in {None, ""}:
        return "n/a"
    if field_name == "price":
        return price_formatter.format_price(value)
    if field_name == "float_value":
        try:
            return f"{float(value):.8f}"
        except ValueError:
            return value
    return value


def format_change_message(change: ChangeSet, price_formatter: PriceFormatter = DEFAULT_PRICE_FORMATTER) -> str:
    emoji, event = EVENT_META.get(change.change_type, ("🔔", f"Update ({change.change_type})"))
    lines = [f"{emoji} <b>{html.escape(event)}</b>"]
    lines.append(f"🆔 <b>Listing:</b> <code>{html.escape(change.listing_id)}</code>")
    if change.market_hash_name:
        lines.append(f"🎯 <b>Item:</b> {html.escape(change.market_hash_name)}")
    if change.float_value is not None:
        lines.append(
            f"🧪 <b>Float:</b> "
            f"<code>{html.escape(_format_value('float_value', str(change.float_value), price_formatter))}</code>"
        )

    if change.change_type == CHANGE_NEW:
        price = _find_delta_value(change, "price", use_new=True)
        if price:
            lines.append(f"💶 <b>Price:</b> <code>{html.escape(_format_value('price', price, price_formatter))}</code>")
        if change.seller_description:
            lines.append(f"📝 <b>Seller Note:</b> {html.escape(change.seller_description)}")
        _append_inspect_link(lines, change)
        return "\n".join(lines)

    if change.change_type == CHANGE_PRICE_CHANGED:
        old_price = _find_delta_value(change, "price", use_new=False)
        new_price = _find_delta_value(change, "price", use_new=True)
        if old_price and new_price:
            lines.append(
                f"💶 <b>Price:</b> <code>{html.escape(_format_value('price', old_price, price_formatter))}</code> "
                f"→ <code>{html.escape(_format_value('price', new_price, price_formatter))}</code>"
            )
        return "\n".join(lines)

    if change.change_type == "delisted":
        old_price = _find_delta_value(change, "price", use_new=False)
        if old_price:
            lines.append(f"💶 <b>Last Price:</b> <code>{html.escape(_format_value('price', old_price, price_formatter))}</code>")
        lines.append("📦 <b>Status:</b> <code>Delisted</code>")
        return "\n".join(lines)

    # Fallback generic format for any future change types.
    for delta in change.deltas:
        old_value = _format_value(delta.field_name, delta.old_value, price_formatter)
        new_value = _format_value(delta.field_name, delta.new_value, price_formatter)
        label = delta.field_name.replace("_", " ").title()
        lines.append(f"• <b>{html.escape(label)}:</b> <code>{html.escape(old_value)}</code> → <code>{html.escape(new_value)}</code>")
    return "\n".join(lines)


def _find_delta_value(change: ChangeSet, field_name: str, use_new: bool) -> str | None:
    for delta in change.deltas:
        if delta.field_name != field_name:
            continue
        value = delta.new_value if use_new else delta.old_value
        if value in {None, "", "n/a"}:
            return None
        return value
    return None


def _append_inspect_link(lines: list[str], change: ChangeSet) -> None:
    if not change.inspect_link:
        return
    lines.append("")
    lines.append("🔎 <b>Inspect Link</b>")
    lines.append(f"<pre><code>{html.escape(change.inspect_link)}</code></pre>")


def build_send_payload(
    chat_id: str,
    change: ChangeSet,
    price_formatter: PriceFormatter = DEFAULT_PRICE_FORMATTER,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": format_change_message(change, price_formatter=price_formatter),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    if change.change_type in {CHANGE_NEW, CHANGE_PRICE_CHANGED} and change.listing_url:
        payload["reply_markup"] = _build_reply_markup(change.listing_url)

    return payload


def build_send_photo_payload(
    chat_id: str,
    change: ChangeSet,
    price_formatter: PriceFormatter = DEFAULT_PRICE_FORMATTER,
) -> dict[str, Any]:
    image_url = change.image_url or change.screenshot_url
    if not image_url:
        raise ValueError("image_url is required for photo payload")

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "photo": image_url,
        "caption": format_change_message(change, price_formatter=price_formatter),
        "parse_mode": "HTML",
    }

    if change.change_type in {CHANGE_NEW, CHANGE_PRICE_CHANGED} and change.listing_url:
        payload["reply_markup"] = _build_reply_markup(change.listing_url)

    return payload


def _build_reply_markup(listing_url: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Open on CSFloat ↗",
                    "url": listing_url,
                }
            ]
        ]
    }


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout_seconds: float = 10,
        price_formatter: PriceFormatter = DEFAULT_PRICE_FORMATTER,
    ):
        self._chat_id = chat_id
        self._send_message_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._send_photo_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        self._updates_url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
        self._client = httpx.Client(timeout=timeout_seconds)
        self._price_formatter = price_formatter
        self._log = logging.getLogger("csfloat.notifier")

    def close(self) -> None:
        self._client.close()
        self._price_formatter.close()

    def send_change(self, change: ChangeSet) -> None:
        if change.image_url or change.screenshot_url:
            photo_payload = build_send_photo_payload(self._chat_id, change, price_formatter=self._price_formatter)
            try:
                response = self._client.post(self._send_photo_url, json=photo_payload)
                response.raise_for_status()
                self._assert_telegram_ok(response.json())
                self._log.info(
                    "notify_photo_sent listing_id=%s change_type=%s",
                    change.listing_id,
                    change.change_type,
                )
                return
            except Exception:  # noqa: BLE001
                self._log.warning(
                    "notify_photo_failed_fallback_text listing_id=%s change_type=%s",
                    change.listing_id,
                    change.change_type,
                )

        payload = build_send_payload(self._chat_id, change, price_formatter=self._price_formatter)
        response = self._client.post(self._send_message_url, json=payload)
        response.raise_for_status()
        self._assert_telegram_ok(response.json())
        self._log.info(
            "notify_text_sent listing_id=%s change_type=%s",
            change.listing_id,
            change.change_type,
        )

    @staticmethod
    def _assert_telegram_ok(data: dict[str, Any]) -> None:
        if not data.get("ok", False):
            raise RuntimeError(f"Telegram API rejected message: {data}")

    def resolve_chat_id(self, username: str) -> str:
        normalized_username = username.lstrip("@").strip().lower()
        if not normalized_username:
            raise ValueError("username is required")

        response = self._client.get(self._updates_url)
        response.raise_for_status()
        payload = response.json()

        best_update_id = -1
        best_chat_id: str | None = None

        for update in payload.get("result", []):
            message = update.get("message") or update.get("edited_message") or {}
            from_user = message.get("from") or {}
            from_username = str(from_user.get("username", "")).lower()
            if from_username != normalized_username:
                continue

            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is None:
                continue

            update_id = int(update.get("update_id", -1))
            if update_id >= best_update_id:
                best_update_id = update_id
                best_chat_id = str(chat_id)

        if best_chat_id is None:
            raise RuntimeError(
                "Could not resolve chat id from getUpdates. Send a message to the bot first and rerun."
            )

        return best_chat_id
