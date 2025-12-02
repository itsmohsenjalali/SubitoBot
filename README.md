# Subito Scraper

 Python scraper for subito.it listings that polls the Subito API and notifies a Telegram chat about new items (no listing storage, only seen IDs kept for dedupe).

## Project Layout

- `src/subito_scraper/`: scraper package.
- `requirements.txt`: Python dependencies.
- `Dockerfile`: container build.
- `.env.example`: configuration template.
- `data/`: SQLite database location (created at runtime).

## Quickstart (local)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=src
cp .env.example .env  # adjust queries/region/category
python -m subito_scraper.main
```

## Quickstart (Docker)

```bash
docker build -t subito-scraper .
docker run --rm -v "$(pwd)/data:/app/data" --env-file .env subito-scraper
```

## Notes

- Configure the default web search URL in `.env` (`SUBITO_SEARCH_URL`) or insert more URLs into the `search_queries` table.
- The scraper converts stored web URLs into API calls to `https://hades.subito.it/v1/search/items?...` (JSON), enforces `order=datedesc`, and only stores seen `external_id` values to avoid duplicates.
- On each poll, only unseen ads are sent to Telegram with title, price, date, location, link, and first photo.
- A 3-minute polling loop runs by default (`SCRAPE_DELAY_SECONDS`, min 180s enforced). Mount `data/` to keep SQLite (for seen IDs and category map).

### Category mapping

API calls require category ids. Store them in the `categories` table (`slug`, `category_id`). Example:

```bash
sqlite3 data/subito.db "INSERT OR REPLACE INTO categories (slug, category_id) VALUES ('videogiochi', '44');"
```

If a stored URL already has `c=<id>` or is an API URL, it is used directly. Otherwise, the scraper extracts the last path segment as `slug`, looks up `categories.slug`, and injects `c=<category_id>`. URLs without a resolvable category are skipped.

### Auto-refresh categories (Playwright)

The category list is shown only after clicking `#main-category-selection`. Use the Playwright helper to refresh mappings:

```bash
pip install playwright
python -m playwright install chromium  # one-time browser install
PYTHONPATH=src python -m subito_scraper.categories
```

The helper opens subito.it, clicks the main category selector, extracts each `category-button` `value` (id) and text (label), derives a slug from the link or label, and upserts `(slug, category_id)` into SQLite. It logs and skips entries with missing data.

### Telegram notifications

- Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
- New listings (unseen `external_id`) are formatted and sent; if no credentials are set, notifications are skipped with a warning.
