import logging
import time

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
    if not queries:
        # Seed with default if table empty
        default = SearchQuery(url=config.default_search_url, label="default")
        db.add_search_query(default)
        queries = [default]
    return queries


def process_queries(config: Config, db: Database, client: SubitoClient, queries: list[SearchQuery]) -> None:
    logger = logging.getLogger(__name__)
    for query in queries:
        max_seen = db.get_max_seen_id(query.id) if hasattr(query, "id") else 0
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
        new_listings = [
            l for l in listings if l.external_id_int is not None and l.external_id_int > max_seen
        ]
        if not new_listings:
            logger.info("[%s] No new listings", query.label or query.url)
            continue
        # Sort oldest to newest before notifying
        new_listings.sort(key=lambda l: l.external_id_int or 0)
        sent = send_telegram_listings(new_listings, config)
        max_seen = max([max_seen] + [l.external_id_int for l in new_listings if l.external_id_int is not None])
        if hasattr(query, "id"):
            db.update_max_seen_id(query.id, max_seen)
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
    logger.info("Starting polling loop every %ss", int(config.scrape_delay or 180))

    db = Database(config.sqlite_path)
    client = SubitoClient(config)
    try:
        while True:
            queries = load_queries(db, config)
            process_queries(config, db, client, queries)
            sleep_for = max(config.scrape_delay, 180)
            logger.info("Sleeping %ss", sleep_for)
            time.sleep(sleep_for)
    finally:
        client.close()
        db.close()


if __name__ == "__main__":
    run_polling_loop()
