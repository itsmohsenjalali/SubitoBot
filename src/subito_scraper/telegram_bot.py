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


def run_bot(kind: str = "default", token_override: Optional[str] = None) -> None:
    config = Config.load()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    token = token_override or (config.telegram_bot_token if kind == "default" else config.telegram_wa_bot_token)
    if not token:
        logger.error("Telegram bot token is required")
        return

    db = Database(config.sqlite_path)
    client = SubitoClient(config)
    offset = None
    try:
        while True:
            updates = _get_updates(token, offset)
            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update and "text" in update["message"]:
                    _handle_message(config, db, client, update["message"], kind, token)
                elif "callback_query" in update:
                    _handle_callback(config, db, client, update["callback_query"], kind, token)
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


def _handle_message(config: Config, db: Database, client: SubitoClient, message: Dict, kind: str, token: str) -> None:
    chat_id = message["chat"]["id"]
    text: str = message.get("text", "")

    state = db.get_state(chat_id, bot_kind=kind)
    if text.startswith("/addurl"):
        db.set_state(chat_id, "await_label", None, bot_kind=kind)
        _send_text(config, chat_id, "Send the label for this search query.", token=token)
        return
    if text.startswith("/urllist"):
        _send_url_list(config, db, chat_id, kind, token=token)
        return

    # If in a state, process text as input
    if state and state["name"] == "await_label":
        if not text.strip():
            _send_text(config, chat_id, "Label cannot be empty. Send a label.", token=token)
            return
        db.set_state(chat_id, "await_url", {"label": text.strip()}, bot_kind=kind)
        _send_text(config, chat_id, "Now send the URL for this label.", token=token)
        return
    if state and state["name"] == "await_url":
        _handle_add_input(config, db, client, chat_id, text, state.get("data"), kind, token=token)
        return
    if state and state["name"] == "edit_label":
        if not text.strip():
            _send_text(config, chat_id, "Label cannot be empty. Send a label.", token=token)
            return
        data = state.get("data") or {}
        data["label"] = text.strip()
        db.set_state(chat_id, "edit_url", data, bot_kind=kind)
        _send_text(config, chat_id, "Now send the new URL.", token=token)
        return
    if state and state["name"] == "edit_url":
        _handle_edit_input(config, db, client, chat_id, text, state.get("data"), kind, token)
        return
    if state and state["name"] == "add_bl_word":
        _handle_add_blacklist_word(config, db, chat_id, text, state.get("data"), token)
        return
    if state and state["name"] == "edit_bl_word":
        _handle_edit_blacklist_word(config, db, chat_id, text, state.get("data"), token)
        return

    _send_text(config, chat_id, "Unknown command. Use /addurl or /urllist.", token=token)


def _handle_callback(config: Config, db: Database, client: SubitoClient, callback: Dict, kind: str, token: str) -> None:
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")
    if data.startswith("select:"):
        query_id = int(data.split(":", 1)[1])
        row = db.get_search_query(query_id)
        if not row:
            _answer_callback(config, callback["id"], "Not found")
            return
        keyboard = _query_actions_keyboard(query_id)
        _edit_message(
            config,
            chat_id,
            callback["message"]["message_id"],
            f"<b>{_escape(row.label or 'no label')}</b>\n{_escape(row.url)}",
            keyboard,
            token,
        )
        _answer_callback(config, callback["id"], token=token)
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
            token,
        )
        _answer_callback(config, callback["id"], "Deleted", token=token)
        return

    if data.startswith("edit:"):
        query_id = int(data.split(":", 1)[1])
        db.set_state(chat_id, "edit_label", {"query_id": query_id}, bot_kind=kind)
        _send_text(config, chat_id, "Send the new label.", token=token)
        _answer_callback(config, callback["id"], token=token)
        return

    if data.startswith("blacklist:"):
        query_id = int(data.split(":", 1)[1])
        _send_blacklist_list(config, db, chat_id, callback["message"]["message_id"], query_id, token=token)
        _answer_callback(config, callback["id"], token=token)
        return
    if data.startswith("bladd:"):
        query_id = int(data.split(":", 1)[1])
        db.set_state(chat_id, "add_bl_word", {"query_id": query_id, "bot_kind": kind}, bot_kind=kind)
        _send_text(config, chat_id, "Send the blacklist word to add.", token=token)
        _answer_callback(config, callback["id"], token=token)
        return
    if data.startswith("blsel:"):
        word_id = int(data.split(":", 1)[1])
        _send_blacklist_word_detail(config, db, chat_id, callback["message"]["message_id"], word_id, token=token)
        _answer_callback(config, callback["id"], token=token)
        return
    if data.startswith("bledit:"):
        word_id = int(data.split(":", 1)[1])
        db.set_state(chat_id, "edit_bl_word", {"word_id": word_id, "bot_kind": kind}, bot_kind=kind)
        _send_text(config, chat_id, "Send the new blacklist word.", token=token)
        _answer_callback(config, callback["id"], token=token)
        return
    if data.startswith("bldel:"):
        word_id = int(data.split(":", 1)[1])
        word = db.get_blacklist_word(word_id)
        if word:
            db.delete_blacklist_word(word_id)
            _send_blacklist_list(config, db, chat_id, callback["message"]["message_id"], word.query_id, msg="Deleted.", token=token)
        _answer_callback(config, callback["id"], token=token)
        return
    if data.startswith("blback:"):
        query_id = int(data.split(":", 1)[1])
        _send_blacklist_list(config, db, chat_id, callback["message"]["message_id"], query_id, token=token)
        _answer_callback(config, callback["id"], token=token)
        return

    _answer_callback(config, callback["id"], "Unhandled action", token=token)


def _handle_add_input(config: Config, db: Database, client: SubitoClient, chat_id: int, text: str, data: Optional[Dict], kind: str, token: Optional[str] = None) -> None:
    label = (data or {}).get("label")
    if not label:
        _send_text(config, chat_id, "Label missing. Start again with /addurl.", token=token)
        return
    url = text.strip()
    if not _valid_url(url):
        _send_text(config, chat_id, "Invalid URL. Try again.", token=token)
        return
    query_id = db.add_search_query_record(url, label, kind=kind)
    if query_id:
        _prime_seen_state(config, db, client, query_id, url)
    db.clear_state(chat_id, bot_kind=kind)
    _send_text(config, chat_id, f"Added:\n<b>{_escape(label)}</b>\n{_escape(url)}", parse_mode="HTML", token=token)


def _handle_edit_input(config: Config, db: Database, client: SubitoClient, chat_id: int, text: str, data: Optional[Dict], kind: str, token: Optional[str] = None) -> None:
    if not data or "query_id" not in data or "label" not in data:
        _send_text(config, chat_id, "No edit target. Start with /urllist.", token=token)
        return
    url = text.strip()
    label = data["label"]
    if not _valid_url(url):
        _send_text(config, chat_id, "Invalid URL. Try again.", token=token)
        return
    db.update_search_query(data["query_id"], url=url, label=label, kind=kind)
    _prime_seen_state(config, db, client, data["query_id"], url)
    db.clear_state(chat_id, bot_kind=kind)
    _send_text(config, chat_id, f"Updated:\n<b>{_escape(label)}</b>\n{_escape(url)}", parse_mode="HTML", token=token)


def _handle_add_blacklist_word(config: Config, db: Database, chat_id: int, text: str, data: Optional[Dict], token: str) -> None:
    if not data or "query_id" not in data:
        _send_text(config, chat_id, "No target. Start from Blacklist menu.", token=token)
        return
    word = text.strip().lower()
    if not word:
        _send_text(config, chat_id, "Word cannot be empty.", token=token)
        return
    db.add_blacklist_word(data["query_id"], word)
    db.clear_state(chat_id, bot_kind=data.get("bot_kind", "default"))
    _send_text(config, chat_id, f"Added blacklist word: <b>{_escape(word)}</b>", parse_mode="HTML", token=token)


def _handle_edit_blacklist_word(config: Config, db: Database, chat_id: int, text: str, data: Optional[Dict], token: str) -> None:
    if not data or "word_id" not in data:
        _send_text(config, chat_id, "No target. Start from Blacklist menu.", token=token)
        return
    word = text.strip().lower()
    if not word:
        _send_text(config, chat_id, "Word cannot be empty.", token=token)
        return
    db.update_blacklist_word(data["word_id"], word)
    db.clear_state(chat_id, bot_kind=data.get("bot_kind", "default"))
    _send_text(config, chat_id, f"Updated word to: <b>{_escape(word)}</b>", parse_mode="HTML", token=token)


def _send_blacklist_list(config: Config, db: Database, chat_id: int, message_id: int, query_id: int, msg: Optional[str] = None, token: str = "") -> None:
    words = db.get_blacklist(query_id)
    keyboard = {"inline_keyboard": []}
    for w in words:
        keyboard["inline_keyboard"].append([{"text": w.word, "callback_data": f"blsel:{w.id}"}])
    keyboard["inline_keyboard"].append([{"text": "➕ Add word", "callback_data": f"bladd:{query_id}"}])
    text = msg or "Blacklist words:"
    _edit_message(config, chat_id, message_id, text, keyboard, token)


def _send_blacklist_word_detail(config: Config, db: Database, chat_id: int, message_id: int, word_id: int, token: str = "") -> None:
    word = db.get_blacklist_word(word_id)
    if not word:
        _edit_message(config, chat_id, message_id, "Not found", None, token)
        return
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✏️ Edit", "callback_data": f"bledit:{word_id}"},
                {"text": "🗑 Delete", "callback_data": f"bldel:{word_id}"},
            ],
            [{"text": "⬅️ Back", "callback_data": f"blback:{word.query_id}"}],
        ]
    }
    _edit_message(config, chat_id, message_id, f"Word: <b>{_escape(word.word)}</b>", keyboard, token)


def _query_actions_keyboard(query_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Delete", "callback_data": f"delete:{query_id}"},
                {"text": "Edit", "callback_data": f"edit:{query_id}"},
                {"text": "Blacklist", "callback_data": f"blacklist:{query_id}"},
            ]
        ]
    }


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


def _send_url_list(config: Config, db: Database, chat_id: int, kind: str, token: str) -> None:
    queries = db.get_search_queries_by_kind(kind)
    if not queries:
        _send_text(config, chat_id, "No URLs stored.", token=token)
        return
    keyboard = {"inline_keyboard": []}
    for q in queries:
        keyboard["inline_keyboard"].append(
            [{"text": q.label or q.url, "callback_data": f"select:{q.id}"}]
        )
    _send_text(config, chat_id, "Select a query:", keyboard=keyboard, token=token)


def _send_text(config: Config, chat_id: int, text: str, keyboard: Optional[Dict] = None, parse_mode: str = "HTML", token: str = "") -> None:
    token = token or config.telegram_bot_token
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Failed to send message: %s", exc)


def _edit_message(config: Config, chat_id: int, message_id: int, text: str, keyboard: Optional[Dict], token: str = "") -> None:
    token = token or config.telegram_bot_token
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
            f"https://api.telegram.org/bot{token}/editMessageText",
            data=payload,
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Failed to edit message: %s", exc)


def _answer_callback(config: Config, callback_id: str, text: Optional[str] = None, token: str = "") -> None:
    token = token or config.telegram_bot_token
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
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
