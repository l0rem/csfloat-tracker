from __future__ import annotations

import html
import logging
from typing import Any

import httpx

from csfloat_monitor.currency import PriceFormatter, UsdPriceFormatter
from csfloat_monitor.market_insights import DelistedMarketAnalyzer
from csfloat_monitor.types import CHANGE_NEW, CHANGE_PRICE_CHANGED, ChangeSet, PinAlert


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
    market_line: str | None = None,
) -> dict[str, Any]:
    text = format_change_message(change, price_formatter=price_formatter)
    if market_line:
        text = f"{text}\n{market_line}"

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
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
    market_line: str | None = None,
) -> dict[str, Any]:
    image_url = change.image_url or change.screenshot_url
    if not image_url:
        raise ValueError("image_url is required for photo payload")

    caption = format_change_message(change, price_formatter=price_formatter)
    if market_line:
        caption = f"{caption}\n{market_line}"

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "photo": image_url,
        "caption": caption,
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
        market_analyzer: DelistedMarketAnalyzer | None = None,
    ):
        self._chat_id = chat_id
        self._send_message_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._send_photo_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        self._edit_message_text_url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
        self._edit_message_caption_url = f"https://api.telegram.org/bot{bot_token}/editMessageCaption"
        self._edit_message_reply_markup_url = f"https://api.telegram.org/bot{bot_token}/editMessageReplyMarkup"
        self._answer_callback_url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
        self._updates_url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
        self._client = httpx.Client(timeout=timeout_seconds)
        self._price_formatter = price_formatter
        self._market_analyzer = market_analyzer
        self._log = logging.getLogger("csfloat.notifier")

    def close(self) -> None:
        self._client.close()
        self._price_formatter.close()

    def send_change(self, change: ChangeSet) -> None:
        market_line = None
        if self._market_analyzer:
            try:
                market_line = self._market_analyzer.build_market_line(change, self._price_formatter)
            except Exception as exc:  # noqa: BLE001
                self._log.warning("market_analyzer_failed listing_id=%s error=%s", change.listing_id, exc)

        if change.image_url or change.screenshot_url:
            photo_payload = build_send_photo_payload(
                self._chat_id,
                change,
                price_formatter=self._price_formatter,
                market_line=market_line,
            )
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

        payload = build_send_payload(
            self._chat_id,
            change,
            price_formatter=self._price_formatter,
            market_line=market_line,
        )
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

    def send_pin_alert(self, alert: PinAlert, action_id: str) -> dict[str, Any]:
        caption = self._format_pin_alert_message(alert)
        reply_markup = self._build_pin_buy_markup(action_id)

        if alert.image_url:
            payload = {
                "chat_id": self._chat_id,
                "photo": alert.image_url,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": reply_markup,
            }
            response = self._client.post(self._send_photo_url, json=payload)
            response.raise_for_status()
            data = response.json()
            self._assert_telegram_ok(data)
            return data

        payload = {
            "chat_id": self._chat_id,
            "text": caption,
            "parse_mode": "HTML",
            "reply_markup": reply_markup,
            "disable_web_page_preview": True,
        }
        response = self._client.post(self._send_message_url, json=payload)
        response.raise_for_status()
        data = response.json()
        self._assert_telegram_ok(data)
        return data

    def fetch_updates(self, *, offset: int) -> list[dict[str, Any]]:
        params = {
            "offset": offset,
            "timeout": 0,
            "allowed_updates": '["callback_query"]',
        }
        response = self._client.get(self._updates_url, params=params)
        response.raise_for_status()
        payload = response.json()
        self._assert_telegram_ok(payload)
        return payload.get("result", [])

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        response = self._client.post(self._answer_callback_url, json=payload)
        response.raise_for_status()
        self._assert_telegram_ok(response.json())

    def set_confirm_markup(self, chat_id: int | str, message_id: int, action_id: str) -> None:
        payload = {
            "chat_id": str(chat_id),
            "message_id": message_id,
            "reply_markup": self._build_confirm_markup(action_id),
        }
        response = self._client.post(self._edit_message_reply_markup_url, json=payload)
        response.raise_for_status()
        self._assert_telegram_ok(response.json())

    def set_buy_markup(self, chat_id: int | str, message_id: int, action_id: str) -> None:
        payload = {
            "chat_id": str(chat_id),
            "message_id": message_id,
            "reply_markup": self._build_pin_buy_markup(action_id),
        }
        response = self._client.post(self._edit_message_reply_markup_url, json=payload)
        response.raise_for_status()
        self._assert_telegram_ok(response.json())

    def append_status_to_message(
        self,
        *,
        chat_id: int | str,
        message_id: int,
        is_photo: bool,
        original_text: str,
        status_line: str,
    ) -> None:
        text = f"{original_text}\n\n{status_line}"
        payload: dict[str, Any] = {
            "chat_id": str(chat_id),
            "message_id": message_id,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": []},
        }
        if is_photo:
            payload["caption"] = text
            response = self._client.post(self._edit_message_caption_url, json=payload)
        else:
            payload["text"] = text
            response = self._client.post(self._edit_message_text_url, json=payload)
        response.raise_for_status()
        self._assert_telegram_ok(response.json())

    def _format_pin_alert_message(self, alert: PinAlert) -> str:
        trigger_label = "🔥 <b>NEW LOW</b>" if alert.trigger_type == "new_low" else "🟰 <b>TIED LOW</b>"
        lines = [
            f"{trigger_label}",
            f"🎯 <b>Item:</b> {html.escape(alert.market_hash_name)}",
            f"🧩 <b>Def Index:</b> <code>{alert.def_index}</code>",
            f"🆔 <b>Listing:</b> <code>{html.escape(alert.listing_id)}</code>",
            f"💶 <b>Price:</b> <code>{html.escape(self._price_formatter.format_price(str(alert.listing_price)))}</code>",
            f"🏁 <b>Best Known:</b> <code>{html.escape(self._price_formatter.format_price(str(alert.best_known_price)))}</code>",
        ]
        if alert.cheapest_sale_price is None:
            lines.append("📉 <b>Vs Cheapest Sale:</b> <code>n/a</code>")
        else:
            sign = "-" if (alert.percent_below_cheapest_sale or 0) >= 0 else "+"
            pct = abs(alert.percent_below_cheapest_sale or 0.0)
            lines.append(
                f"📉 <b>Vs Cheapest Sale:</b> <code>{sign}{pct:.2f}%</code> "
                f"(sale <code>{html.escape(self._price_formatter.format_price(str(alert.cheapest_sale_price)))}</code>)"
            )

        lines.append("")
        lines.append("📚 <b>Last 10 Sales</b>")
        if not alert.recent_sales:
            lines.append("• <code>n/a</code>")
        else:
            for sale in alert.recent_sales[:10]:
                sold_at = (sale.sold_at or "unknown time").replace("T", " ").replace("Z", " UTC")
                price_text = self._price_formatter.format_price(str(sale.sale_price))
                lines.append(f"• <code>{html.escape(price_text)}</code> — {html.escape(sold_at)}")

        lines.append("")
        lines.append(f"🔗 <a href=\"{html.escape(alert.listing_url)}\">Open on CSFloat</a>")
        return "\n".join(lines)

    @staticmethod
    def _build_pin_buy_markup(action_id: str) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "Buy",
                        "callback_data": f"buy:{action_id}",
                    }
                ]
            ]
        }

    @staticmethod
    def _build_confirm_markup(action_id: str) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "Yes",
                        "callback_data": f"confirm_yes:{action_id}",
                    },
                    {
                        "text": "No",
                        "callback_data": f"confirm_no:{action_id}",
                    },
                ]
            ]
        }
