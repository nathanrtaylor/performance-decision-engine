"""
Propose-only THEME DISCOVERY.

Mirrors src/cde/benchmarks_recalc in spirit: it reads the same extract + config the
pipeline reads, computes population-level metric CO-MOVEMENT per ICP_Client cohort on
a direction-adjusted "bad" axis, and PROPOSES candidate coaching themes (groups of
metrics that move together) for a human SME to review.

It never wires into the decision pipeline and never edits configs/mappings/themes.yaml.
A theme is added to the theme list ONLY by a human SME (see cli/discover_themes.py):
even `--apply` defers every proposed theme for manual merge — it only records intent
in the governance changelog.
"""
