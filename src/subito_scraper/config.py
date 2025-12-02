import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass
class Config:
    default_search_url: str
    user_agent: str
    request_timeout: int
    request_retries: int
    scrape_delay: float
    sqlite_path: str
    log_level: str
    telegram_bot_token: str
    telegram_chat_id: str

    @property
    def base_url(self) -> str:
        parsed = urlsplit(self.default_search_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @classmethod
    def load(cls) -> "Config":
        load_dotenv()
        return cls(
            default_search_url=os.getenv(
                "SUBITO_SEARCH_URL",
                "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=switch&shp=true&order=datedesc&advt=0%2C2&ps=40&pe=80",
            ),
            user_agent=os.getenv(
                "SUBITO_USER_AGENT",
                "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
            ),
            request_timeout=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")),
            request_retries=int(os.getenv("REQUEST_RETRIES", "2")),
            scrape_delay=float(os.getenv("SCRAPE_DELAY_SECONDS", "180")),
            sqlite_path=os.getenv("SQLITE_PATH", "data/subito.db"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        )
