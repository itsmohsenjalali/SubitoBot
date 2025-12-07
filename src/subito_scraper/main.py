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


def load_queries(db: Database, config: Config) -> list[SearchQuery]:
    queries = db.get_search_queries()
    return queries


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


def run_polling_loop() -> None:
    config = Config.load()
    configure_logging(config.log_level)
    logger = logging.getLogger(__name__)
    logger.info("Starting polling loop target ~2-4 minutes with per-query jitter")

    db = Database(config.sqlite_path)
    client = SubitoClient(config)
    try:
        while True:
            loop_start = time.time()
            queries = load_queries(db, config)
            if queries:
                jitter_budget = 60  # seconds max jitter per loop
                remaining = jitter_budget
                per_query_jitters = []
                for i in range(len(queries)):
                    # Allocate random jitter so total <= budget
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
                    process_queries(config, db, client, [query])

            # Ensure at least 120s between loop starts; cap around 240s by jitter design
            elapsed = time.time() - loop_start
            base_interval = 180
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
