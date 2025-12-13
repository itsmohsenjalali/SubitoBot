import logging
import time
import random

from .client import SubitoClient
from .config import Config
from .db import Database
from .models import SearchQuery
from .parser import parse_api_response
from .notify import send_telegram_listings
from .url_builder import web_url_to_api


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def load_queries(db: Database, kind: str) -> list[SearchQuery]:
    return db.get_search_queries_by_kind(kind)


def process_queries(config: Config, db: Database, client: SubitoClient, queries: list[SearchQuery]) -> None:
    logger = logging.getLogger(__name__)
    for query in queries:
        max_seen = db.get_max_seen_id(query.id) if hasattr(query, "id") else 0
        blacklist = set(word.lower() for word in db.get_blacklist_words(query.id)) if query.id else set()
        api_url = web_url_to_api(query.url, db)
        if not api_url:
            logger.warning(
                "Skipping query '%s' because category id not found for URL %s",
                query.label or query.url,
                query.url,
            )
            continue

        logger.info("Fetching %s", api_url)
        data = client.fetch_items(api_url)
        listings = parse_api_response(data)
        # Determine new items vs max_seen
        candidates = [
            l for l in listings if l.external_id_int is not None and l.external_id_int > max_seen
        ]
        # Apply blacklist on title (case-insensitive substring)
        new_listings = []
        for l in candidates:
            title_lower = l.title.lower()
            if any(bw in title_lower for bw in blacklist):
                logger.info("Skipping blacklisted title for query %s: %s", query.label or query.url, l.title)
                continue
            new_listings.append(l)

        # Update max_seen using all candidates (even filtered) to avoid reprocessing
        if candidates:
            new_max = max(l.external_id_int for l in candidates if l.external_id_int is not None)
            if new_max and query.id:
                db.update_max_seen_id(query.id, new_max)
                max_seen = new_max
        if not new_listings:
            logger.info("[%s] No new listings", query.label or query.url)
            continue
        # Sort oldest to newest before notifying
        new_listings.sort(key=lambda l: l.external_id_int or 0)
        sent = send_telegram_listings(new_listings, config)
        logger.info(
            "[%s] Received %d items, sent %d new listings",
            query.label or query.url,
            len(listings),
            sent,
        )


def process_watchlist_queries(config: Config, db: Database, client: SubitoClient, queries: list[SearchQuery]) -> None:
    logger = logging.getLogger(__name__)
    for query in queries:
        max_seen = db.get_max_seen_id(query.id) if hasattr(query, "id") else 0
        blacklist = set(word.lower() for word in db.get_blacklist_words(query.id)) if query.id else set()
        api_url = web_url_to_api(query.url, db)
        if not api_url:
            logger.warning(
                "Skipping query '%s' because category id not found for URL %s",
                query.label or query.url,
                query.url,
            )
            continue

        logger.info("Fetching %s [watchlist]", api_url)
        data = client.fetch_items(api_url)
        listings = parse_api_response(data)
        candidates = [
            l for l in listings if l.external_id_int is not None and l.external_id_int > max_seen
        ]
        # blacklist filter
        filtered = []
        for l in candidates:
            if any(bw in l.title.lower() for bw in blacklist):
                logger.info("Skipping blacklisted (watchlist) %s", l.title)
                continue
            filtered.append(l)

        if candidates:
            new_max = max(l.external_id_int for l in candidates if l.external_id_int is not None)
            if new_max and query.id:
                db.update_max_seen_id(query.id, new_max)
                max_seen = new_max

        if not filtered:
            logger.info("[%s] No new watchlist items", query.label or query.url)
            continue

        db.add_watchlist_items([l.external_id for l in filtered])
        logger.info("[%s] Added %d items to watchlist", query.label or query.url, len(filtered))


def run_sold_checker(config: Config, db: Database, client: SubitoClient) -> None:
    logger = logging.getLogger(__name__)
    ids = db.get_watchlist_ids()
    if not ids:
        logger.info("Sold checker: no watchlist items.")
        return
    logger.info("Sold checker: checking %d items", len(ids))
    batch_size = 80
    sold_ids = []
    for i in range(0, len(ids), batch_size):
        batch = ids[i : i + batch_size]
        url = f"https://hades.subito.it/v1/search/items?list_ids={','.join(batch)}"
        try:
            data = client.fetch_items(url)
            listings = parse_api_response(data)
            for l in listings:
                if (l.transaction_status or "").upper() == "SOLD":
                    sold_ids.append(l)
        except Exception as exc:
            logger.warning("Sold checker batch failed: %s", exc)
            continue

    if sold_ids:
        send_telegram_listings(
            sold_ids,
            config,
            chat_id=config.telegram_wa_chat_id,
            bot_token=config.telegram_wa_bot_token,
        )
        db.remove_watchlist_items([l.external_id for l in sold_ids])
        logger.info("Sold checker: notified %d sold items", len(sold_ids))
    else:
        logger.info("Sold checker: no sold items detected")


def run_polling_loop() -> None:
    config = Config.load()
    configure_logging(config.log_level)
    logger = logging.getLogger(__name__)
    logger.info("Starting polling loop target ~2-4 minutes with per-query jitter")

    db = Database(config.sqlite_path)
    client = SubitoClient(config)
    last_sold_check = time.time()
    sold_check_interval = 6 * 3600  # 6 hours
    try:
        while True:
            loop_start = time.time()
            for kind, handler in (
                ("default", process_queries),
                ("watchlist", process_watchlist_queries),
            ):
                queries = load_queries(db, kind)
                if not queries:
                    continue
                jitter_budget = 120  # seconds max jitter per loop
                remaining = jitter_budget
                per_query_jitters = []
                for i in range(len(queries)):
                    remaining_slots = len(queries) - i
                    max_for_this = remaining if remaining_slots == 1 else remaining / remaining_slots * 2
                    jitter = random.uniform(0, max(0, min(remaining, max_for_this)))
                    jitter = min(jitter, remaining)
                    remaining -= jitter
                    per_query_jitters.append(jitter)
                for query, jitter in zip(queries, per_query_jitters):
                    if jitter > 0:
                        logger.info("Jitter before %s: %.0fs", query.label or query.url, jitter)
                        time.sleep(jitter)
                    handler(config, db, client, [query])

            if time.time() - last_sold_check >= sold_check_interval:
                run_sold_checker(config, db, client)
                last_sold_check = time.time()

            # Ensure at least 120s between loop starts; cap around 240s by jitter design
            elapsed = time.time() - loop_start
            base_interval = 120
            if elapsed < base_interval:
                sleep_for = base_interval - elapsed
                logger.info("Sleeping %.0fs to maintain min interval", sleep_for)
                time.sleep(sleep_for)
            else:
                logger.info("Loop elapsed %.0fs; starting next immediately", elapsed)
    finally:
        client.close()
        db.close()


if __name__ == "__main__":
    run_polling_loop()
