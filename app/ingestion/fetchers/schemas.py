"""采集器共享类型。"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FetchItem:
    external_id: str
    title: str
    url: str
    published_raw: Optional[str] = None
    summary: str = ""
    content: Optional[str] = None
