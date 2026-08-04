from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from cde.governance.audit import RunAuditor
from cde.governance.versioning import config_content_hash, resolve_active_config, resolve_raw_export_dir
from cde.ingestion.extract import load_raw_exports
from cde.ingestion.normalize import normalize_inputs
from cde.ingestion.validate import validate_inputs
from cde.ingestion.coaching_history import build_coaching_history
from cde.prioritization.dampening import apply_recent_coaching_dampening
from cde.signals.build_signals import build_signals
from cde.scoring.assemble import assemble_scores, compute_windowed_scores
from cde.prioritization.apply import build_topic_candidates
from cde.engine.select import select_recommendations
from cde.engine.abstain import apply_abstention
from cde.engine.receipts import build_receipts
from cde.reporting.artifacts import export_run_artifacts
from cde.signals.thresholds import apply_signal_thresholds
from cde.temporal.aggregate import aggregate_scores_window


def _resolve_snapshot_id(raw_dir: Path) -> str:
    """Best-effort snapshot id for provenance: the raw manifest's run_id, else the folder name."""
    manifest = raw_dir / "manifest.json"
    if manifest.exists():
        try:
            import json

            data = json.loads(manifest.read_text(encoding="utf-8"))
            run_id = data.get("run_id")
            if run_id:
                return str(run_id)
        except Exception:
            pass
    return raw_dir.name


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Run end-to-end Coaching Decision Engine pipeline.")
    parser.add_argument(
        "--raw-dir",
        type=str,
        default=None,
        help="Raw export folder (e.g. data/raw/weekly/2026-02-16). "
        "If omitted, resolved from data_snapshot in configs/active.yaml.",
    )
    parser.add_argument("--out-dir", type=str, default=None, help="Output folder for artifacts. Default: outputs/runs/<run-id> (auto-timestamped, matching recalc/discover).")
    parser.add_argument("--configs-dir", type=str, default="configs", help="Path to configs directory")
    parser.add_argument("--run-id", type=str, default=None, help="Run id. Default: local timestamp; also names the auto out-dir so run-id and folder always match.")
    parser.add_argument(
        "--write-point-in-time-scores",
        action="store_true",
        help="Write scores.csv (per-period point-in-time scores). Recommendations use scores_windowed.csv only.",
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="Skip the config/snapshot preflight (config integrity + raw-data checks) before running.",
    )
    parser.add_argument(
        "--strict-preflight",
        action="store_true",
        help="Treat preflight warnings as fatal (abort the run).",
    )
    args = parser.parse_args(argv)

    # Unified run-id / out-dir: one timestamp names both, so the folder always matches the
    # audited run_id. An explicit --out-dir is honored as-is (run_id still derived if omitted).
    run_id = args.run_id
    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        if run_id is None:
            run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out_dir = Path("outputs/runs") / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    configs_dir = Path(args.configs_dir)

    config = resolve_active_config(configs_dir)
    raw_dir = Path(args.raw_dir) if args.raw_dir else resolve_raw_export_dir(configs_dir, config)

    # Preflight: validate config cross-references + raw snapshot before the ~10 min run.
    # Additive by design -- the current, valid config passes, so a normal run is unaffected;
    # only a genuinely broken config (bad direction, unregistered metric, missing table) aborts.
    if not args.no_preflight:
        from cde.governance.config_lint import lint_config, preflight_snapshot

        report = lint_config(config)
        report.merge(preflight_snapshot(raw_dir, config))
        print("Preflight:")
        print(report.render(strict=args.strict_preflight))
        if report.errors or (args.strict_preflight and report.warnings):
            raise SystemExit(
                "Preflight failed. Fix the errors above, or re-run with --no-preflight to bypass."
            )

    # Stamp provenance from the *resolved* snapshot so receipts + manifest reflect the real data
    # used (not a hand-edited meta value). Must happen before auditor.record_inputs copies meta.
    # config_hash is computed from config *content* (excludes data_snapshot) so provenance tracks
    # the actual governed configs even if meta.version wasn't hand-bumped.
    config["meta"] = {
        **(config.get("meta") or {}),
        "data_snapshot": _resolve_snapshot_id(raw_dir),
        "config_hash": config_content_hash(config),
    }

    auditor = RunAuditor(out_dir=out_dir, run_id=run_id)
    auditor.start_run()

    raw = load_raw_exports(raw_dir)
    auditor.record_inputs(raw_dir=raw_dir, config=config)

    normalized = normalize_inputs(raw, config)
    validate_inputs(normalized, config)

    signals = build_signals(normalized, config)
    signals.to_csv(out_dir / "signals.csv", index=False)

    thr_res = apply_signal_thresholds(signals, config)
    eligible_signals = thr_res.eligible_signals
    excluded_signals = thr_res.excluded_signals

    eligible_signals.to_csv(out_dir / "eligible_signals.csv", index=False)
    excluded_signals.to_csv(out_dir / "excluded_signals.csv", index=False)

    # --- Normalize period dtype for deterministic merges ---
    import pandas as pd

    eligible_signals["period"] = pd.to_datetime(
        eligible_signals["period"], errors="coerce"
    )

    # excluded_signals can be empty (no exclusions). In that case pandas creates a df with no columns.
    if excluded_signals is not None and not excluded_signals.empty:
        # support either canonical 'period' or legacy 'week_ending'
        if "period" not in excluded_signals.columns and "week_ending" in excluded_signals.columns:
            excluded_signals = excluded_signals.rename(columns={"week_ending": "period"})

        if "period" in excluded_signals.columns:
            excluded_signals["period"] = pd.to_datetime(excluded_signals["period"], errors="coerce")
    else:
        # force a well-formed empty df with expected columns so downstream writes don't break
        excluded_signals = pd.DataFrame(columns=["agent_id", "period", "call_type", "metric", "reason"])

    # --- Optional per-period diagnostic scores (point-in-time, before windowing) ---
    if args.write_point_in_time_scores:
        assemble_scores(eligible_signals, config).to_csv(out_dir / "scores.csv", index=False)

    # --- 8-week windowed aggregation: the decision grain for "next coaching" ---
    windowed = aggregate_scores_window(eligible_signals=eligible_signals, config=config)
    windowed.to_csv(out_dir / "scores_windowed_raw.csv", index=False)

    # --- Single direction-aware, weighted scoring pass (priority_model weights) ---
    scores_windowed = compute_windowed_scores(windowed, config)

    print("eligible_signals rows:", len(eligible_signals))
    print("scores_windowed rows:", 0 if scores_windowed is None else len(scores_windowed))

    if scores_windowed is None or scores_windowed.empty:
        raise RuntimeError(
            "Windowed scoring returned empty. Check src/cde/temporal/aggregate.py and that "
            "benchmark/direction flow through from build_signals into eligible_signals."
        )

    scores_windowed.to_csv(out_dir / "scores_windowed.csv", index=False)

    candidates = build_topic_candidates(eligible_signals, scores_windowed, config)

    # --- Recency dampening: soft-suppress recently-coached topics (no-op if no history) ---
    coaching_history = build_coaching_history(normalized, config)
    candidates = apply_recent_coaching_dampening(candidates, config, history=coaching_history)
    candidates.to_csv(out_dir / "topic_candidates.csv", index=False)

    # Three-tier selection: break-glass override -> theme -> single (fallback).
    # Identical to the prior single-argmax when no themes/break_glass are configured.
    recs, selection_detail = select_recommendations(
        candidates, eligible_signals, scores_windowed, config
    )

    # Abstention: withhold non-material single recs + surface universe agents with no rec.
    recs, abstentions = apply_abstention(recs, normalized.get("agents"), candidates, config)
    if abstentions is not None and not abstentions.empty:
        print(f"abstentions: {len(abstentions)} agent(s) received no recommendation")

    receipts = build_receipts(
        recs, candidates, eligible_signals, scores_windowed, config,
        excluded_signals=excluded_signals, selection_detail=selection_detail,
        abstentions=abstentions,
    )
    export_run_artifacts(
        out_dir, auditor, recs, receipts, config,
        excluded_signals=excluded_signals, abstentions=abstentions,
    )

    # HTML summary dashboard (standard output package). Non-fatal: a dashboard error must not fail the run.
    try:
        from cde.reporting.dashboard import write_dashboard

        write_dashboard(
            out_dir / "dashboard.html",
            recommendations=recs,
            signals=signals,  # full built signals: carries benchmark, gap, direction
            config=config,
            excluded_signals=excluded_signals,
            candidates=candidates,
            agents=normalized.get("agents"),  # primary source for icp_client / mascot splits
            abstentions=abstentions,
        )
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: dashboard generation failed: {e!r}")

    auditor.finish_run()
    print(f"Done. Wrote outputs to: {out_dir}")


if __name__ == "__main__":
    main()
