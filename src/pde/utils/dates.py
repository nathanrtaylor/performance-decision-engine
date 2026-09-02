from __future__ import annotations

from datetime import datetime
from typing import Optional


def parse_date(s: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None
