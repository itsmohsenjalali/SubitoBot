from typing import Any, Dict
import random
import logging

from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Config

try:
    from random_user_agent.user_agent import UserAgent
    from random_user_agent.params import SoftwareName, OperatingSystem
except ImportError:  # graceful fallback if dependency missing
    UserAgent = None
    SoftwareName = OperatingSystem = None


class SubitoClient:
    def __init__(self, config: Config):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.session = Session()
        retries = Retry(
            total=config.request_retries,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods={"GET"},
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.ua_rotator = None
        if UserAgent and SoftwareName and OperatingSystem:
            self.ua_rotator = UserAgent(
                software_names=[SoftwareName.CHROME.value],
                operating_systems=[OperatingSystem.WINDOWS.value, OperatingSystem.LINUX.value, OperatingSystem.MAC.value],
                limit=100,
            )

    def _user_agent(self) -> str:
        if self.ua_rotator:
            try:
                return self.ua_rotator.get_random_user_agent()
            except Exception:
                pass
        return self.config.user_agent

    def fetch_items(self, url: str) -> Dict[str, Any]:
        ua = self._user_agent()
        headers = {"User-Agent": ua}
        self.logger.info("GET %s with UA=%s", url, ua)
        response = self.session.get(url, timeout=self.config.request_timeout, headers=headers)
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self.session.close()
