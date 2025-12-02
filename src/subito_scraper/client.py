from typing import Any, Dict

from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Config


class SubitoClient:
    def __init__(self, config: Config):
        self.config = config
        self.session = Session()
        self.session.headers.update({"User-Agent": config.user_agent})
        retries = Retry(
            total=config.request_retries,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods={"GET"},
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def fetch_items(self, url: str) -> Dict[str, Any]:
        response = self.session.get(url, timeout=self.config.request_timeout)
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self.session.close()
