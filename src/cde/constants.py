# src/cde/constants.py
"""
Cross-cutting engine constants that must agree across otherwise-independent modules.

Leaf module: import-only, no dependencies, so any module can import it without cycles.
"""
from __future__ import annotations

# The decision window (in distinct weeks) the engine scores on. This single definition is imported by
# temporal aggregation, benchmark recalculation, and theme discovery, which previously each hardcoded 8.
WINDOW_WEEKS = 8
