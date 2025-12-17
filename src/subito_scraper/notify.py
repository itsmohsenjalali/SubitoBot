import logging
from typing import Iterable, List, Optional
import time
import requests

from .config import Config
from .models import Listing


def send_telegram_listings(listings: Iterable[Listing], config: Config, chat_id: Optional[str] = None, bot_token: Optional[str] = None) -> int:
    target_token = bot_token or config.telegram_bot_token
    target_chat = chat_id or config.telegram_chat_id
    if not target_token or not target_chat:
        logging.getLogger(__name__).warning(
            "Telegram credentials missing; skipping %d listings", len(listings)
        )
        return 0

    sent = 0
    for listing in listings:
        if listing.photos:
            if _send_photo(listing, target_token, target_chat):
                sent += 1
                time.sleep(1)
                continue
            logging.getLogger(__name__).warning(
                "Photo send failed; falling back to text for %s", listing.external_id
            )
        if _send_message(listing, target_token, target_chat):
            sent += 1
        time.sleep(1)
    return sent


def _build_caption(listing: Listing) -> str:
    parts: List[str] = []
    label = f"[{_escape(listing.search_label)}] " if listing.search_label else ""
    parts.append(f"📦 {label}<b><a href=\"{_escape(listing.url)}\">{_escape(listing.title)}</a></b>")
    if listing.price:
        parts.append(f"💰 <b>{_escape(listing.price)}</b>")
    if listing.posted_at:
        parts.append(f"⏰ {_escape(listing.posted_at)}")
    return "\n".join(parts)


def _send_photo(listing: Listing, token: str, chat_id: str) -> bool:
    logger = logging.getLogger(__name__)
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    payload = {
        "chat_id": chat_id,
        "photo": listing.photos[0],
        "caption": _build_caption(listing),
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code != 200:
            logger.warning("Failed to send photo: %s %s", resp.status_code, resp.text)
            return False
        return True
    except Exception as exc:
        logger.warning("Error sending photo to Telegram: %s", exc)
        return False


def _send_message(listing: Listing, token: str, chat_id: str) -> bool:
    logger = logging.getLogger(__name__)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": _build_caption(listing),
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code != 200:
            logger.warning("Failed to send message: %s %s", resp.status_code, resp.text)
            return False
        return True
    except Exception as exc:
        logger.warning("Error sending message to Telegram: %s", exc)
        return False


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
