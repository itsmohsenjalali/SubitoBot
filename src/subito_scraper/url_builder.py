from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qsl

from .db import Database


API_BASE = "https://hades.subito.it/v1/search/items"


def web_url_to_api(url: str, db: Database) -> Optional[str]:
    """
    Convert a stored web URL to an API URL by:
    - Extracting category slug from the path, mapping to category_id via DB.
    - Copying query parameters as-is.
    - Injecting `c=<category_id>` if found, or leaving existing `c` intact if slug unknown.
    Returns None if cannot resolve a category id and no existing `c` present.
    """
    parsed = urlparse(url)
    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))

    # Always enforce newest-first ordering
    query_params["order"] = "datedesc"

    # If caller already stored an API URL or provided c, keep it.
    if parsed.netloc.startswith("hades.subito.it") or "c" in query_params:
        return f"{API_BASE}?{urlencode(query_params)}"

    slug = _extract_slug_from_path(parsed.path)
    category_id = db.get_category_id(slug) if slug else None

    if not category_id:
        return None

    query_params["c"] = category_id
    return f"{API_BASE}?{urlencode(query_params)}"


def _extract_slug_from_path(path: str) -> Optional[str]:
    parts = [p for p in path.split("/") if p]
    return parts[-1] if parts else None
