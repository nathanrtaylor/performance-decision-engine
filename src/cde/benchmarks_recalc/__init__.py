"""
Guardrail-gated benchmark recalculation.

A SEPARATE, independently-callable module (not wired into the pipeline) that analyzes the latest
weekly extract and, for each metric/cohort, evaluates whether the evidence clears the guardrails
required to justify changing its benchmark in configs/mappings/benchmarks.yaml.

It is PROPOSE-ONLY: it emits an HTML dashboard (OLD vs NEW with a per-metric PROPOSE/HOLD verdict and
the supporting evidence), a machine-readable proposed_benchmarks.yaml, and a diff. It never writes
benchmarks.yaml unless explicitly authorized via the CLI's ``--apply`` flag.

Entry point: ``python -m cde.cli.recalc_benchmarks``.
"""
from __future__ import annotations
