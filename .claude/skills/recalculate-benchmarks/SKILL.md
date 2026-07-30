---
name: recalculate-benchmarks
description: >-
  Recompute proposed benchmark values from the latest weekly extract, evaluate whether each
  metric/cohort clears the guardrails that justify a change, and produce an HTML dashboard comparing
  OLD vs NEW with a per-metric PROPOSE/HOLD verdict and justification. Propose-only and
  human-in-the-loop — never overwrites benchmarks without explicit authorization. Use when the user
  asks to "recalculate benchmarks", "recompute benchmarks", or "check if benchmarks should change".
---

# Recalculate benchmarks

A side-car module (`src/cde/benchmarks_recalc/`) that reads the same extract + config as the pipeline
but does not touch it. It computes candidate benchmark anchors (operational = per-cohort median;
quality/sentiment = p25 of the 8-week windowed-mean pass-rate, capped 0.95), then gates each against
guardrails (sample sufficiency, materiality, non-degeneracy, cohort-split validity, observed-range
sanity). A change is **PROPOSED** only when every applicable guardrail passes and the move is material.

## When the user asks to recalculate benchmarks

1. Run the recompute CLI (propose-only — this is the default and does NOT modify any config):

   ```
   python -m cde.cli.recalc_benchmarks --configs-dir configs \
       --out-dir outputs/benchmark_recalc/<yyyy-mm-dd_HHMMSS>
   ```

2. Report the printed summary counts: `PROPOSE / HOLD / UNCHANGED / SKIPPED`.

3. Surface the outputs in `--out-dir`:
   - `dashboard.html` — OLD vs NEW per metric/cohort, verdict chips, and justification. Point the user
     here and call out the rows marked **PROPOSE**.
   - `proposed_benchmarks.yaml` — the change-set (PROPOSE rows only), in benchmarks.yaml shape.
   - `benchmark_diff.json`, `summary.txt` — full detail.

4. **Do not modify `configs/mappings/benchmarks.yaml`.** Applying is a separate, authorized governance
   step. Only if the user explicitly approves, re-run with:

   ```
   python -m cde.cli.recalc_benchmarks --configs-dir configs \
       --out-dir outputs/benchmark_recalc/<id> --apply --approver "<name>"
   ```

   `--apply` performs value-only edits to existing keys (preserving the file's methodology comments)
   and appends `configs/governance/changelog.md`. New cohort splits are deferred for manual merge from
   `proposed_benchmarks.yaml` and listed in the output.

## Notes

- Sentiment behaviors are Verizon-only; the module splits mob-verizon vs pss-verizon only when they
  differ materially, matching the curated convention.
- Guardrail thresholds live in `src/cde/benchmarks_recalc/config.py` (`RecalcThresholds`).
