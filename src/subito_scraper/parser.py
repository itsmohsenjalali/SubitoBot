from typing import Any, Dict, List, Optional

from .models import Listing


def parse_api_response(data: Dict[str, Any]) -> List[Listing]:
    items = data.get("ads") or []
    listings: List[Listing] = []

    for item in items:
        external_id = _extract_external_id(item)
        url = _extract_url(item)
        if not external_id or not url:
            continue
        external_id_int = _to_int(external_id)
        listing = Listing(
            external_id=external_id,
            external_id_int=external_id_int,
            title=item.get("subject") or "Untitled",
            url=url,
            price=_extract_price(item),
            location=_extract_location(item),
            posted_at=_extract_posted_at(item),
            photos=_extract_photos(item),
            transaction_status=_extract_transaction_status(item),
        )
        listings.append(listing)

    return listings


def _extract_external_id(item: Dict[str, Any]) -> Optional[str]:
    urn = item.get("urn") or ""
    if not urn:
        return None
    parts = urn.split(":")
    return parts[-1] if parts else None


def _extract_price(item: Dict[str, Any]) -> Optional[str]:
    # Try features with uri "/price"
    features = item.get("features") or []
    for feature in features:
        if feature.get("uri") == "/price":
            values = feature.get("values") or []
            if values:
                value = values[0].get("value")
                return value
    return None


def _extract_location(item: Dict[str, Any]) -> Optional[str]:
    geo = item.get("geo") or {}
    city = (geo.get("city") or {}).get("value") or ""
    region = (geo.get("region") or {}).get("value") or ""
    return ", ".join(filter(None, [city, region])) or None


def _extract_url(item: Dict[str, Any]) -> Optional[str]:
    urls = item.get("urls") or {}
    return urls.get("default") or urls.get("mobile")


def _extract_posted_at(item: Dict[str, Any]) -> Optional[str]:
    dates = item.get("dates") or {}
    return dates.get("display") or dates.get("display_iso8601")


def _extract_photos(item: Dict[str, Any]) -> List[str]:
    images = item.get("images") or []
    urls: List[str] = []
    for img in images:
        url = img.get("cdn_base_url")
        if url:
            url = f"{url}?rule=gallery-mobile-1x-auto"
            urls.append(url)
    return urls


def _to_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_transaction_status(item: Dict[str, Any]) -> Optional[str]:
    features = item.get("features") or []
    for feature in features:
        if feature.get("uri") == "/transaction_status":
            values = feature.get("values") or []
            if values:
                return values[0].get("value")
    return None
