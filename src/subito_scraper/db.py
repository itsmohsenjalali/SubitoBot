import json
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional

from .models import Listing, SearchQuery, BlacklistWord


class Database:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        # Cleanup legacy tables
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                category_id TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                label TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_state (
                query_id INTEGER PRIMARY KEY,
                max_external_id_int INTEGER,
                FOREIGN KEY(query_id) REFERENCES search_queries(id) ON DELETE CASCADE
            )
            """
        )
        self.conn.commit()
        # State for Telegram chat interactions
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_state (
                chat_id INTEGER PRIMARY KEY,
                name TEXT,
                data TEXT
            )
            """
        )
        # Blacklist words per query
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                FOREIGN KEY(query_id) REFERENCES search_queries(id) ON DELETE CASCADE,
                UNIQUE(query_id, word)
            )
            """
        )
        self.conn.commit()

    def get_max_seen_id(self, query_id: int) -> int:
        row = self.conn.execute(
            "SELECT max_external_id_int FROM seen_state WHERE query_id = ?",
            (query_id,),
        ).fetchone()
        return row["max_external_id_int"] if row and row["max_external_id_int"] is not None else 0

    def update_max_seen_id(self, query_id: int, value: int) -> None:
        self.conn.execute(
            """
            INSERT INTO seen_state (query_id, max_external_id_int)
            VALUES (?, ?)
            ON CONFLICT(query_id) DO UPDATE SET max_external_id_int=excluded.max_external_id_int
            """,
            (query_id, value),
        )
        self.conn.commit()

    def set_state(self, chat_id: int, name: str, data: Optional[dict]) -> None:
        self.conn.execute(
            """
            INSERT INTO telegram_state (chat_id, name, data)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET name=excluded.name, data=excluded.data
            """,
            (chat_id, name, json.dumps(data) if data else None),
        )
        self.conn.commit()

    def clear_state(self, chat_id: int) -> None:
        self.conn.execute("DELETE FROM telegram_state WHERE chat_id = ?", (chat_id,))
        self.conn.commit()

    def get_state(self, chat_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT name, data FROM telegram_state WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if not row:
            return None
        data = None
        if row["data"]:
            try:
                data = json.loads(row["data"])
            except Exception:
                data = None
        return {"name": row["name"], "data": data}

    def add_search_query(self, query: SearchQuery) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO search_queries (url, label)
            VALUES (?, ?)
            """,
            (query.url, query.label),
        )
        self.conn.commit()

    def add_search_query_record(self, url: str, label: Optional[str]) -> int:
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO search_queries (url, label)
            VALUES (?, ?)
            """,
            (url, label),
        )
        self.conn.commit()
        return cursor.lastrowid

    def update_search_query(self, query_id: int, url: str, label: Optional[str]) -> None:
        self.conn.execute(
            """
            UPDATE search_queries SET url = ?, label = ? WHERE id = ?
            """,
            (url, label, query_id),
        )
        self.conn.commit()

    def delete_search_query(self, query_id: int) -> None:
        self.conn.execute(
            "DELETE FROM search_queries WHERE id = ?",
            (query_id,),
        )
        self.conn.commit()

    def get_search_query(self, query_id: int) -> Optional[SearchQuery]:
        row = self.conn.execute(
            "SELECT id, url, label FROM search_queries WHERE id = ?",
            (query_id,),
        ).fetchone()
        if not row:
            return None
        return SearchQuery(url=row["url"], label=row["label"], id=row["id"])

    def get_search_queries(self) -> List[SearchQuery]:
        rows = self.conn.execute(
            "SELECT id, url, label FROM search_queries ORDER BY id ASC"
        ).fetchall()
        return [SearchQuery(id=row["id"], url=row["url"], label=row["label"]) for row in rows]

    def get_default_query(self) -> Optional[SearchQuery]:
        queries = self.get_search_queries()
        return queries[0] if queries else None

    def add_category(self, slug: str, category_id: str) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO categories (slug, category_id)
            VALUES (?, ?)
            """,
            (slug, category_id),
        )
        self.conn.commit()

    def get_category_id(self, slug: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT category_id FROM categories WHERE slug = ?",
            (slug,),
        ).fetchone()
        if row:
            return row["category_id"]
        return None

    def close(self) -> None:
        self.conn.close()

    # Blacklist helpers
    def add_blacklist_word(self, query_id: int, word: str) -> int:
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO blacklist (query_id, word)
            VALUES (?, ?)
            """,
            (query_id, word),
        )
        self.conn.commit()
        return cursor.lastrowid

    def update_blacklist_word(self, word_id: int, word: str) -> None:
        self.conn.execute(
            "UPDATE blacklist SET word = ? WHERE id = ?",
            (word, word_id),
        )
        self.conn.commit()

    def delete_blacklist_word(self, word_id: int) -> None:
        self.conn.execute("DELETE FROM blacklist WHERE id = ?", (word_id,))
        self.conn.commit()

    def get_blacklist(self, query_id: int) -> List[BlacklistWord]:
        rows = self.conn.execute(
            "SELECT id, query_id, word FROM blacklist WHERE query_id = ? ORDER BY word ASC",
            (query_id,),
        ).fetchall()
        return [BlacklistWord(id=row["id"], query_id=row["query_id"], word=row["word"]) for row in rows]

    def get_blacklist_words(self, query_id: int) -> List[str]:
        rows = self.conn.execute(
            "SELECT word FROM blacklist WHERE query_id = ?",
            (query_id,),
        ).fetchall()
        return [row["word"] for row in rows]

    def get_blacklist_word(self, word_id: int) -> Optional[BlacklistWord]:
        row = self.conn.execute(
            "SELECT id, query_id, word FROM blacklist WHERE id = ?",
            (word_id,),
        ).fetchone()
        if not row:
            return None
        return BlacklistWord(id=row["id"], query_id=row["query_id"], word=row["word"])
