import logging
from typing import Iterable, List
import time
import requests

from .config import Config
from .models import Listing


def send_telegram_listings(listings: Iterable[Listing], config: Config) -> int:
    if not config.telegram_bot_token or not config.telegram_chat_id:
        logging.getLogger(__name__).warning(
            "Telegram credentials missing; skipping %d listings", len(listings)
        )
        return 0

    sent = 0
    for listing in listings:
        if listing.photos:
            if _send_album(listing, config):
                sent += 1
                continue
            logging.getLogger(__name__).warning(
                "Photo send failed; falling back to text for %s", listing.external_id
            )
        if _send_message(listing, config):
            sent += 1
        time.sleep(1)
    return sent


def _build_caption(listing: Listing) -> str:
    parts: List[str] = []
    parts.append(f"📦 <b>{_escape(listing.title)}</b>")
    if listing.price:
        parts.append(f"💰 <b>{_escape(listing.price)}</b>")
    if listing.location:
        parts.append(f"📍 {_escape(listing.location)}")
    if listing.posted_at:
        parts.append(f"⏰ {_escape(listing.posted_at)}")
    parts.append(f"🔗 <a href=\"{_escape(listing.url)}\">Apri annuncio</a>")
    return "\n".join(parts)


def _send_photo(listing: Listing, config: Config) -> bool:
    logger = logging.getLogger(__name__)
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendPhoto"
    payload = {
        "chat_id": config.telegram_chat_id,
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


def _send_message(listing: Listing, config: Config) -> bool:
    logger = logging.getLogger(__name__)
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": config.telegram_chat_id,
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


def _send_album(listing: Listing, config: Config) -> bool:
    """
    Send up to 5 photos as an album with caption on first image.
    """
    logger = logging.getLogger(__name__)
    photos = listing.photos[:5]
    media = []
    for idx, url in enumerate(photos):
        item = {"type": "photo", "media": url}
        if idx == 0:
            item["caption"] = _build_caption(listing)
            item["parse_mode"] = "HTML"
        media.append(item)

    api_url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMediaGroup"
    try:
        resp = requests.post(
            api_url,
            json={"chat_id": config.telegram_chat_id, "media": media},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("Failed to send album: %s %s", resp.status_code, resp.text)
            return False
        return True
    except Exception as exc:
        logger.warning("Error sending album to Telegram: %s", exc)
        return False


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
