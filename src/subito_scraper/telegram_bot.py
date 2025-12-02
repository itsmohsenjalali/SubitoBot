import json
import logging
import time
from typing import Dict, Optional
from urllib.parse import urlparse

import requests
import ujson

from .config import Config
from .db import Database
from .client import SubitoClient
from .parser import parse_api_response
from .url_builder import web_url_to_api

logger = logging.getLogger(__name__)


def run_bot() -> None:
    config = Config.load()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if not config.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is required for bot")
        return

    db = Database(config.sqlite_path)
    client = SubitoClient(config)
    offset = None
    try:
        while True:
            updates = _get_updates(config.telegram_bot_token, offset)
            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update and "text" in update["message"]:
                    _handle_message(config, db, client, update["message"])
                elif "callback_query" in update:
                    _handle_callback(config, db, client, update["callback_query"])
            time.sleep(1.5)
    finally:
        db.close()
        client.close()


def _get_updates(token: str, offset: Optional[int]) -> list:
    params = {"timeout": 10}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", [])


def _handle_message(config: Config, db: Database, client: SubitoClient, message: Dict) -> None:
    chat_id = message["chat"]["id"]
    text: str = message.get("text", "")

    state = db.get_state(chat_id)
    if text.startswith("/addurl"):
        db.set_state(chat_id, "await_label", None)
        _send_text(config, chat_id, "Send the label for this search query.")
        return
    if text.startswith("/urllist"):
        _send_url_list(config, db, chat_id)
        return

    # If in a state, process text as input
    if state and state["name"] == "await_label":
        if not text.strip():
            _send_text(config, chat_id, "Label cannot be empty. Send a label.")
            return
        db.set_state(chat_id, "await_url", {"label": text.strip()})
        _send_text(config, chat_id, "Now send the URL for this label.")
        return
    if state and state["name"] == "await_url":
        _handle_add_input(config, db, client, chat_id, text, state.get("data"))
        return
    if state and state["name"] == "edit_label":
        if not text.strip():
            _send_text(config, chat_id, "Label cannot be empty. Send a label.")
            return
        data = state.get("data") or {}
        data["label"] = text.strip()
        db.set_state(chat_id, "edit_url", data)
        _send_text(config, chat_id, "Now send the new URL.")
        return
    if state and state["name"] == "edit_url":
        _handle_edit_input(config, db, client, chat_id, text, state.get("data"))
        return

    _send_text(config, chat_id, "Unknown command. Use /addurl or /urllist.")


def _handle_callback(config: Config, db: Database, client: SubitoClient, callback: Dict) -> None:
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")
    if data.startswith("select:"):
        query_id = int(data.split(":", 1)[1])
        row = db.get_search_query(query_id)
        if not row:
            _answer_callback(config, callback["id"], "Not found")
            return
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "Delete", "callback_data": f"delete:{query_id}"},
                    {"text": "Edit", "callback_data": f"edit:{query_id}"},
                ]
            ]
        }
        _edit_message(
            config,
            chat_id,
            callback["message"]["message_id"],
            f"<b>{_escape(row.label or 'no label')}</b>\n{_escape(row.url)}",
            keyboard,
        )
        _answer_callback(config, callback["id"])
        return

    if data.startswith("delete:"):
        query_id = int(data.split(":", 1)[1])
        db.delete_search_query(query_id)
        _edit_message(
            config,
            chat_id,
            callback["message"]["message_id"],
            "Deleted.",
            None,
        )
        _answer_callback(config, callback["id"], "Deleted")
        return

    if data.startswith("edit:"):
        query_id = int(data.split(":", 1)[1])
        db.set_state(chat_id, "edit_label", {"query_id": query_id})
        _send_text(config, chat_id, "Send the new label.")
        _answer_callback(config, callback["id"])
        return

    _answer_callback(config, callback["id"], "Unhandled action")


def _handle_add_input(config: Config, db: Database, client: SubitoClient, chat_id: int, text: str, data: Optional[Dict]) -> None:
    label = (data or {}).get("label")
    if not label:
        _send_text(config, chat_id, "Label missing. Start again with /addurl.")
        return
    url = text.strip()
    if not _valid_url(url):
        _send_text(config, chat_id, "Invalid URL. Try again.")
        return
    query_id = db.add_search_query_record(url, label)
    if query_id:
        _prime_seen_state(config, db, client, query_id, url)
    db.clear_state(chat_id)
    _send_text(config, chat_id, f"Added:\n<b>{_escape(label)}</b>\n{_escape(url)}", parse_mode="HTML")


def _handle_edit_input(config: Config, db: Database, client: SubitoClient, chat_id: int, text: str, data: Optional[Dict]) -> None:
    if not data or "query_id" not in data or "label" not in data:
        _send_text(config, chat_id, "No edit target. Start with /urllist.")
        return
    url = text.strip()
    label = data["label"]
    if not _valid_url(url):
        _send_text(config, chat_id, "Invalid URL. Try again.")
        return
    db.update_search_query(data["query_id"], url=url, label=label)
    _prime_seen_state(config, db, client, data["query_id"], url)
    db.clear_state(chat_id)
    _send_text(config, chat_id, f"Updated:\n<b>{_escape(label)}</b>\n{_escape(url)}", parse_mode="HTML")


def _prime_seen_state(config: Config, db: Database, client: SubitoClient, query_id: int, url: str) -> None:
    api_url = web_url_to_api(url, db)
    if not api_url:
        return
    try:
        data = client.fetch_items(api_url)
        listings = parse_api_response(data)
        max_id = 0
        for l in listings:
            if l.external_id_int and l.external_id_int > max_id:
                max_id = l.external_id_int
        if max_id:
            db.update_max_seen_id(query_id, max_id)
    except Exception as exc:
        logger.warning("Prime seen_state failed for query %s: %s", query_id, exc)


def _send_url_list(config: Config, db: Database, chat_id: int) -> None:
    queries = db.get_search_queries()
    if not queries:
        _send_text(config, chat_id, "No URLs stored.")
        return
    keyboard = {"inline_keyboard": []}
    for q in queries:
        keyboard["inline_keyboard"].append(
            [{"text": q.label or q.url, "callback_data": f"select:{q.id}"}]
        )
    _send_text(config, chat_id, "Select a query:", keyboard=keyboard)


def _send_text(config: Config, chat_id: int, text: str, keyboard: Optional[Dict] = None, parse_mode: str = "HTML") -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
            data=payload,
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Failed to send message: %s", exc)


def _edit_message(config: Config, chat_id: int, message_id: int, text: str, keyboard: Optional[Dict]) -> None:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.telegram_bot_token}/editMessageText",
            data=payload,
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Failed to edit message: %s", exc)


def _answer_callback(config: Config, callback_id: str, text: Optional[str] = None) -> None:
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.telegram_bot_token}/answerCallbackQuery",
            data=payload,
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Failed to answer callback: %s", exc)


def _valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return bool(parsed.scheme and parsed.netloc)


def _split_url_label(text: str) -> tuple[str, Optional[str]]:
    parts = text.strip().split(" ", 1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1].strip() or None


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


if __name__ == "__main__":
    run_bot()
