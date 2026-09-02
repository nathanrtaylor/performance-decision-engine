from __future__ import annotations

import logging
from typing import Optional


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler()
        fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        h.setFormatter(fmt)
        logger.addHandler(h)
    logger.setLevel(level)
    return logger
