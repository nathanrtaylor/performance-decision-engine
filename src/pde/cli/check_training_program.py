"""CLI: report the training program's coverage -- what's defined vs still TODO.

Companion to ``check_config`` for the training domain. Loads
``configs/training/training_program.yaml`` and the training metric catalog, and
reports which blocks still lack a gate test-call, enumerated components, or an
expected completion day, plus any ``develops:`` skill that isn't a real cataloged
metric and any cataloged *skill* no block develops yet (CBT-assessment metrics are
excluded from that check -- they are course completions, not block ``develops:`` skills).

It also checks component consistency: the profile<->program crosswalk (every profile in
``training_profiles.yaml`` that declares a ``sim_id`` must match a program component whose
``ref == sim_id`` and ``persona == challenge_id`` -- an inconsistent pairing is an error),
and warns on any component ``ref`` reused by more than one component (which silently
collapses the crosswalk). CBT components are also summarized: how many link to a scored
``cbt_*`` metric vs. are completion-only (no scored metric, which is expected).

    python -m pde.cli.check_training_program
    python -m pde.cli.check_training_program --strict     # TODOs (warnings) fail too

Exit codes: 0 = clean (or warnings-only without --strict); 1 = errors (a develops
entry referencing an unknown metric, or a structural problem) or, with --strict,
any warning.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import yaml

from pde.governance.versioning import resolve_active_config
from pde.ingestion.training_assist_skills import ascend_challenge_ids, build_behavior_skill_map, _norm
from pde.training.program import load_program, program_coverage, skills_observed_before_taught


def _catalog_metrics(configs_dir: Path) -> dict:
    """metric_key -> metric entry, from the active training metric catalog."""
    cfg = resolve_active_config(configs_dir)
    mc = cfg.get("metric_catalog") or {}
    mc = mc.get("metric_catalog", mc) if isinstance(mc, dict) else {}
    return mc.get("metrics") or {}


def _catalog_skill_ids(configs_dir: Path) -> set:
    """Defined skill ids from training_profiles.yaml `skills:` -- the authoritative develops targets
    (a skill can be defined + placed before it has data/a scored metric)."""
    path = configs_dir / "training_profiles.yaml"
    if not path.exists():
        return set()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return set((data.get("skills") or {}) if isinstance(data, dict) else {})


def _profiles_with_sim_id(configs_dir: Path) -> dict:
    """challenge_id -> sim_id for every profile in training_profiles.yaml that declares one."""
    path = configs_dir / "training_profiles.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles = (data.get("profiles") or {}) if isinstance(data, dict) else {}
    return {cid: p["sim_id"] for cid, p in profiles.items()
            if isinstance(p, dict) and p.get("sim_id")}


def _components(program_path: Path) -> list:
    """All program components as dicts {block, kind, ref, persona} (raw YAML: load_program drops persona)."""
    data = yaml.safe_load(program_path.read_text(encoding="utf-8")) or {}
    out: list = []
    for b in ((data.get("program") or {}).get("blocks") or []):
        for c in (b.get("components") or []):
            out.append({"block": b.get("id"), "kind": c.get("kind"),
                        "ref": c.get("ref"), "persona": c.get("persona")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Report training program coverage (defined vs TODO)")
    ap.add_argument("--configs-dir", default="configs/training", help="Training configs directory")
    ap.add_argument("--program", default=None, help="Path to training_program.yaml (default: <configs-dir>/training_program.yaml)")
    ap.add_argument("--strict", action="store_true", help="Treat TODO warnings as errors")
    ap.add_argument("--raw-dir", default=None,
                    help="If given, also lint observed vs taught: flag ASCEND behaviors that map to no "
                         "skill, and skills observed (via a persona's sim) in a block earlier than "
                         "their develops block (e.g. data/raw/adhoc/latest).")
    args = ap.parse_args()

    configs_dir = Path(args.configs_dir)
    program_path = Path(args.program) if args.program else configs_dir / "training_program.yaml"

    program = load_program(program_path)
    metrics = _catalog_metrics(configs_dir)
    # develops targets are validated against the DEFINED skill catalog (training_profiles.yaml
    # `skills:`), unioned with scored skill metrics. A skill can be defined + placed in a block's
    # develops before it has data/a scored metric (dormant until an exercising persona requires it);
    # validating against the derived metric_catalog alone would wrongly flag those as unknown.
    # CBT-assessment metrics are excluded (course completions, not block develops skills).
    catalog = _catalog_skill_ids(configs_dir) | {k for k, m in metrics.items() if (m or {}).get("category") != "cbt"}
    cbt_metric_keys = {k for k, m in metrics.items() if (m or {}).get("category") == "cbt"}
    cov = program_coverage(program, catalog)

    errors: list[str] = []
    warnings: list[str] = []

    # ---- structural errors ----
    ids = [b.id for b in program.blocks]
    dup_ids = [i for i, n in Counter(ids).items() if n > 1]
    if dup_ids:
        errors.append(f"duplicate block id(s): {dup_ids}")
    dup_order = [o for o, n in Counter(b.order for b in program.blocks).items() if n > 1]
    if dup_order:
        errors.append(f"duplicate block order value(s): {dup_order}")
    if cov["develops_unknown"]:
        errors.append(f"develops references metric(s) not in the catalog: {cov['develops_unknown']}")

    # ---- component consistency: crosswalk + duplicate refs ----
    comps = _components(program_path)
    ref_to_persona = {c["ref"]: c["persona"] for c in comps if c["ref"]}
    sim_ids = _profiles_with_sim_id(configs_dir)
    for cid, sid in sorted(sim_ids.items()):
        if sid not in ref_to_persona:
            errors.append(f"profile {cid!r} sim_id {sid!r} is not the ref of any training_program component")
        elif ref_to_persona[sid] != cid:
            errors.append(f"profile {cid!r} sim_id {sid!r} belongs to component persona "
                          f"{ref_to_persona[sid]!r}, not {cid!r}")

    # A ref reused across components silently collapses the crosswalk (ref -> persona is 1:1).
    dup_refs = sorted(r for r, n in Counter(c["ref"] for c in comps if c["ref"]).items() if n > 1)
    if dup_refs:
        warnings.append(f"{len(dup_refs)} component ref(s) reused by more than one component: {dup_refs}")

    # ---- TODO warnings ----
    if cov["blocks_missing_gate"]:
        warnings.append(f"{len(cov['blocks_missing_gate'])} block(s) have no gate test_call (TODO): {cov['blocks_missing_gate']}")
    if cov["blocks_missing_components"]:
        warnings.append(f"{len(cov['blocks_missing_components'])} block(s) have no enumerated components (TODO): {cov['blocks_missing_components']}")
    if cov["blocks_missing_expected_day"]:
        warnings.append(f"{len(cov['blocks_missing_expected_day'])} block(s) have no expected_completion_day: {cov['blocks_missing_expected_day']}")
    if cov["unmapped_catalog_skills"]:
        warnings.append(f"{len(cov['unmapped_catalog_skills'])} cataloged skill(s) not developed by any block: {cov['unmapped_catalog_skills']}")

    # ---- observed-vs-taught lint (only with --raw-dir): new/unmapped behaviors + earlier-than-taught skills ----
    if args.raw_dir:
        import pandas as pd
        ta = Path(args.raw_dir) / "training_assist.csv"
        if not ta.exists():
            warnings.append(f"--raw-dir given but {ta} not found; skipped observed-vs-taught lint")
        else:
            prof_data = yaml.safe_load((configs_dir / "training_profiles.yaml").read_text(encoding="utf-8")) or {}
            asc = ascend_challenge_ids(prof_data)
            b2s = build_behavior_skill_map(prof_data)
            profs = prof_data.get("profiles") or {}
            cid_sim = {_norm(p.get("challenge_id") or k): (p or {}).get("sim_id") for k, p in profs.items()}
            order_by_id = {b.id: b.order for b in program.blocks}
            ref_block = {c["ref"]: order_by_id.get(c["block"]) for c in comps if c["ref"]}
            df = pd.read_csv(ta, dtype={"scorecard_name": str})
            df["_cid"] = df["scorecard_name"].map(_norm)
            df = df[df["_cid"].isin(asc)].copy()
            df["_beh"] = df["behavior"].map(_norm)
            df["skill"] = df["_beh"].map(b2s)
            df["block"] = df["_cid"].map(cid_sim).map(ref_block)
            unmapped = sorted(set(df.loc[df["skill"].isna() & (df["_beh"] != ""), "_beh"]))
            if unmapped:
                warnings.append(f"{len(unmapped)} ASCEND behavior(s) map to no skill (new skills to map): {unmapped}")
            observed = [(s, b) for s, b in zip(df["skill"], df["block"]) if pd.notna(s) and pd.notna(b)]
            for skill, info in sorted(skills_observed_before_taught(program, observed).items()):
                warnings.append(f"skill {skill!r} observed in block(s) {info['early_blocks']} earlier than its "
                                f"taught block {info['taught']} (new-profile drift -- consider moving develops earlier)")

    # ---- render ----
    print(f"training program: {program.name!r} -- {cov['n_blocks']} blocks, "
          f"{cov['n_skills_mapped']} skills mapped, pace {program.pace_hours_per_day} h/day")
    if sim_ids:
        print(f"  ..  {len(sim_ids)} profile sim_id crosswalk link(s) checked against program components")
    cbt_refs = [c["ref"] for c in comps if c["kind"] == "cbt" and c["ref"]]
    if cbt_refs:
        linked = sum(1 for r in cbt_refs if r in cbt_metric_keys)
        print(f"  ..  {len(cbt_refs)} CBT component(s): {linked} linked to a scored metric, "
              f"{len(cbt_refs) - linked} completion-only / not yet scored")
    for e in errors:
        print(f"  ERROR  {e}")
    for w in warnings:
        print(f"  WARN   {w}")
    if not errors and not warnings:
        print("  OK  program structure fully defined")

    n_err = len(errors)
    n_warn = len(warnings)
    verdict = "FAIL" if (n_err or (args.strict and n_warn)) else "PASS"
    print(f"training-program-check {verdict}: {n_err} error(s), {n_warn} warning(s)")
    return 1 if (n_err or (args.strict and n_warn)) else 0


if __name__ == "__main__":
    sys.exit(main())
