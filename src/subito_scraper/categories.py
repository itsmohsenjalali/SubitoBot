import logging
from typing import List, Optional, Tuple
from urllib.parse import urlparse
import time
from .config import Config
from .db import Database


def scrape_categories(config: Config, headless: bool = True) -> List[Tuple[str, str]]:
    """
    Opens subito.it, accepts cookies, opens the category modal, then clicks each category button
    to capture the redirected URL and derive the canonical slug.
    Returns list of (slug, category_id).
    """
    try:
        from playwright.sync_api import (
            sync_playwright,
            TimeoutError as PlaywrightTimeoutError,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for category scraping. Install with "
            "`pip install playwright` and run `python -m playwright install chromium`."
        ) from exc

    logger = logging.getLogger(__name__)
    categories: List[Tuple[str, str]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(user_agent=config.user_agent)
        try:
            page.goto(
                "https://www.subito.it/annunci-italia/vendita/usato/",
                timeout=config.request_timeout * 1000,
            )
            _accept_cookies_if_present(page)
            _open_category_modal(page)

            index = 0
            while True:
                buttons = page.query_selector_all('[class*="category-button"]')
                if index >= len(buttons):
                    break

                btn = buttons[index]
                category_id = btn.get_attribute("value")
                label = (btn.inner_text() or "").strip()

                if not category_id:
                    logger.warning("Skipping category with missing id: label=%s", label)
                    index += 1
                    continue

                try:
                    with page.expect_navigation(wait_until="domcontentloaded", timeout=16000):
                        btn.click()
                    final_url = page.url
                    slug = _slug_from_url(final_url)
                    if slug:
                        categories.append((slug, category_id))
                        logger.info("Captured category %s -> %s (id=%s)", label, slug, category_id)
                    else:
                        logger.warning("Could not derive slug after navigation: label=%s url=%s", label, final_url)
                except PlaywrightTimeoutError:
                    logger.warning("Navigation timeout for category: label=%s", label)
                    page.goto("https://www.subito.it/annunci-italia/vendita/usato/",timeout=8000, wait_until="domcontentloaded")
                    _open_category_modal(page)
                    continue
                
                # page.go_back(wait_until="load", timeout=8000)
                page.goto("https://www.subito.it/annunci-italia/vendita/usato/",timeout=8000, wait_until="domcontentloaded")
                _open_category_modal(page)
                index += 1
                # time.sleep(1)


        except PlaywrightTimeoutError as exc:
            raise RuntimeError("Failed to load categories modal; selectors not found.") from exc
        finally:
            browser.close()

    return categories


def update_categories(headless: bool = True) -> None:
    config = Config.load()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger(__name__)
    db = Database(config.sqlite_path)
    try:
        categories = scrape_categories(config, headless=headless)
        for slug, category_id in categories:
            db.add_category(slug, category_id)
        logger.info("Upserted %d categories", len(categories))
    finally:
        db.close()


def _slug_from_href(href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    parsed = urlparse(href)
    parts = [p for p in parsed.path.split("/") if p]
    return parts[-1] if parts else None


def _slugify(label: str) -> str:
    return (
        label.strip()
        .lower()
        .replace(" ", "-")
        .replace("'", "")
        .replace(",", "")
    )


def _slug_from_url(url: str) -> Optional[str]:
    return _slug_from_href(url)


def _accept_cookies_if_present(page) -> None:
    try:
        button = page.wait_for_selector("#didomi-notice-agree-button", timeout=3000)
        button.click()
    except Exception:
        return


def _open_category_modal(page) -> None:
    from playwright.sync_api import (
            TimeoutError as PlaywrightTimeoutError)
    logger = logging.getLogger(__name__)
    try:
        page.wait_for_selector("#search-category-container > div > button > span", timeout=10000).click()
        page.wait_for_selector('[class*="category-button"]', timeout=5000)
    except PlaywrightTimeoutError as exc:
        logger.warning("Navigation timeout for category")
        if "https://www.subito.it/annunci-italia/vendita/" not in page.url:
            page.go_forward(wait_until="domcontentloaded", timeout=8000)
        else:
            page.reload()
        _open_category_modal(page)


if __name__ == "__main__":
    update_categories(headless=False)
