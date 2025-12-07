from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Listing:
    external_id: str
    external_id_int: Optional[int]
    title: str
    url: str
    price: Optional[str] = None
    location: Optional[str] = None
    posted_at: Optional[str] = None
    photos: List[str] = field(default_factory=list)


@dataclass
class SearchQuery:
    url: str
    label: Optional[str] = None
    id: Optional[int] = None


@dataclass
class SeenState:
    query_id: int
    max_external_id_int: int = 0


@dataclass
class BlacklistWord:
    id: int
    query_id: int
    word: str
