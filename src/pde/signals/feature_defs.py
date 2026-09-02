from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


Direction = Literal["higher_is_better", "lower_is_better"]


@dataclass(frozen=True)
class SignalDef:
    """
    Declarative definition of a metric signal:
    - metric: canonical name (e.g., 'transfer_rate')
    - direction: higher is better vs lower is better
    - benchmark: optional benchmark series key
    """
    metric: str
    direction: Direction
    benchmark: Optional[str] = None
    unit: Optional[str] = None
