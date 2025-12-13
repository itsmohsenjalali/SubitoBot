import logging
from typing import Optional, Tuple, Dict
from urllib.parse import urlencode, urlparse, parse_qsl

import requests

from .db import Database
from .client import SubitoClient

API_BASE = "https://hades.subito.it/v1/search/items"
GEO_SEARCH = "https://hades.subito.it/v1/geo/search"
GEO_TIMEOUT = 8

logger = logging.getLogger(__name__)


def web_url_to_api(url: str, db: Database) -> Optional[str]:
    """
    Convert a stored web URL to an API URL by:
    - Extracting category slug from the path, mapping to category_id via DB.
    - Parsing optional location slugs (region/province/municipality/nearby).
    - Resolving geo keys (r/ci/to) via geo search API when applicable.
    - Copying query parameters as-is and enforcing `order=datedesc`.
    Returns None if cannot resolve a category id and no existing `c` present.
    """
    parsed = urlparse(url)
    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))

    # Always enforce newest-first ordering
    query_params["order"] = "datedesc"

    # If caller already stored an API URL or provided c, keep it.
    if parsed.netloc.startswith("hades.subito.it") or "c" in query_params:
        return f"{API_BASE}?{urlencode(query_params)}"

    parts = _extract_parts(parsed.path)
    logger.info("location filters is :", parts)
    category_id = db.get_category_id(parts["category"]) if parts["category"] else None
    if not category_id:
        return None

    query_params["c"] = category_id

    geo_params = _resolve_geo_params(
        region=parts["region"],
        province=parts["province"],
        municipality=parts["municipality"],
        nearby=parts["nearby"],
        db=db,
    )
    query_params.update({k: v for k, v in geo_params.items() if v})

    return f"{API_BASE}?{urlencode(query_params)}"


def extract_location_filters(url: str) -> Dict[str, Optional[str]]:
    """Return parsed location parts from the URL."""
    parsed = urlparse(url)
    return _extract_parts(parsed.path)


def _extract_parts(path: str) -> Dict[str, Optional[str]]:
    """
    Path pattern (slashes trimmed):
    annunci-{region}/vendita/{category}/[province]/[municipality]/
    region may have '-vicino' suffix (e.g., veneto-vicino).
    """
    segments = [p for p in path.split("/") if p]
    region = category = province = municipality = None
    nearby = False
    if segments:
        if segments[0].startswith("annunci-"):
            region = segments[0].replace("annunci-", "", 1)
        if len(segments) >= 3 and segments[1] == "vendita":
            category = segments[2]
            if len(segments) >= 4:
                province = segments[3]
            if len(segments) >= 5:
                municipality = segments[4]
        else:
            category = segments[-1]
        if region and region.endswith("-vicino"):
            region = region[: -len("-vicino")]
            nearby = True
    return {
        "region": region,
        "province": province,
        "municipality": municipality,
        "nearby": nearby,
        "category": category,
    }


def _resolve_geo_params(
    region: Optional[str],
    province: Optional[str],
    municipality: Optional[str],
    nearby: bool,
    db: Optional[Database] = None,
) -> Dict[str, str]:
    """
    Resolve geo keys via hades geo search:
      - If region == 'italia' -> no geo params.
      - If municipality present: search by municipality.
      - Else if province present: search by province.
      - Else if region present: search by region.
      - If nearby flag: set r to region.neighbors and skip ci/to.
    """
    if not region or region == "italia":
        return {}

    search_key = municipality or province or region
    if not search_key:
        return {}

    # Reuse UA rotation via SubitoClient to get a random UA
    ua = None
    try:
        if db:
            # db carries config path; instead instantiate SubitoClient for UA helper
            from .config import Config

            cfg = Config.load()
            client = SubitoClient(cfg)
            ua = client._user_agent()
            client.close()
    except Exception:
        ua = None

    headers = {"User-Agent": ua} if ua else None

    try:
        resp = requests.get(
            GEO_SEARCH, params={"key": search_key, "lim": 10}, timeout=GEO_TIMEOUT, headers=headers
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
    except Exception as exc:
        logger.warning("Geo lookup failed for %s: %s", search_key, exc)
        return {}

    if not data:
        return {}
    entry = data[0]
    if municipality:
        entry = data[1]
    region_key = entry.get("region", {}).get("key")
    city_key = entry.get("city", {}).get("key")
    town_key = entry.get("town", {}).get("key")
    params: Dict[str, str] = {}

    if nearby and entry.get("region", {}).get("neighbors"):
        params["r"] = entry["region"]["neighbors"]
        return params

    if region_key:
        params["r"] = region_key
    if city_key and (municipality or province):
        params["ci"] = city_key
    if town_key and municipality:
        params["to"] = town_key
    return params
