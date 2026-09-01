"""Generate the mechanical training-domain mapping configs from the real training data.

Emits, under configs/training/mappings/:
  - metric_catalog.yaml   (category_defaults per category + one metric per skill/course)
  - benchmarks.yaml       (per-metric pass-mark default)
  - topic_map.yaml        (metric -> "Retake: <label>" topic, topic -> "Re-training")

Sources (each contributes metrics if its CSV is present in the raw dir):
  - training_assist_skills.csv : skill pass-rates. metric = skill id (from training_profiles).
  - training_cbt.csv           : CBT course scores. metric = "cbt_<courseid>" (namespaced so a
                                 course id can never collide with a skill id); label = coursename.

`source_metric_key` always matches the data's own key column (skill / courseid) exactly, so
build_signals maps rows without silent drops. Re-runnable: overwrites the three generated files.
The small files (active.yaml, thresholds, priorities, source_catalog) are hand-authored.

    python tools/gen_training_configs.py [--raw-dir DIR] [--pass-mark 0.80]
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "configs" / "training" / "mappings"
PROFILES = REPO / "configs" / "mappings" / "training_profiles.yaml"
PROD_CATALOG = REPO / "configs" / "mappings" / "metric_catalog.yaml"


def _labelize(s: str) -> str:
    return re.sub(r"[_\-]+", " ", str(s)).strip().title()


def _valid_weight_class() -> str:
    prod = yaml.safe_load(PROD_CATALOG.read_text(encoding="utf-8")) or {}
    prod_mc = prod.get("metric_catalog", prod)
    return ((prod_mc.get("category_defaults") or {}).get("quality_behavior") or {}).get(
        "base_weight_class", "low"
    )


def _skill_metrics(raw_dir: Path):
    """(metrics dict, categories list) from training_assist_skills.csv (+ profile labels)."""
    csv = raw_dir / "training_assist_skills.csv"
    if not csv.exists():
        return {}, []
    df = pd.read_csv(csv)
    pairs = (df[["skill", "category"]].drop_duplicates()
             .sort_values(["category", "skill"]).reset_index(drop=True))
    profiles = yaml.safe_load(PROFILES.read_text(encoding="utf-8")) or {}
    skills_meta = profiles.get("skills") or {}

    metrics = {}
    for _, row in pairs.iterrows():
        sid, cat = str(row["skill"]), str(row["category"])
        label = (skills_meta.get(sid) or {}).get("label") or _labelize(sid)
        metrics[sid] = _metric_entry(
            source="training_assist_skills", source_metric_key=sid, category=cat,
            desc=f"TrAIning Assist skill pass-rate: {label}.", label=label,
        )
    cats = list(dict.fromkeys(pairs["category"].tolist()))
    return metrics, cats


def _cbt_metrics(raw_dir: Path):
    """(metrics dict, categories list) from training_cbt.csv. metric name namespaced 'cbt_<id>'."""
    csv = raw_dir / "training_cbt.csv"
    if not csv.exists():
        return {}, []
    df = pd.read_csv(csv)
    # coursename -> label (first non-null per courseid)
    names = (df.dropna(subset=["courseid"]).groupby("courseid")["coursename"]
             .first().to_dict()) if "coursename" in df.columns else {}
    metrics = {}
    for cid in sorted(df["courseid"].dropna().unique(), key=str):
        label = _labelize(names.get(cid) or cid)
        name = "cbt_" + re.sub(r"[^0-9A-Za-z]+", "_", str(cid)).strip("_").lower()
        metrics[name] = _metric_entry(
            source="training_cbt", source_metric_key=cid, category="cbt",
            desc=f"CBT completion score: {label}.", label=label,
        )
    return metrics, (["cbt"] if metrics else [])


def _metric_entry(source: str, source_metric_key, category: str, desc: str, label: str) -> dict:
    return {
        "source": source,
        "source_metric_key": source_metric_key,   # matches the data's key column value exactly
        "category": category,
        "direction": "higher_is_better",           # pass-rate / score: higher is better
        "unit": "score",
        "description": desc,
        "required": False,
        "eligible_for_prioritization": True,
        "computation_override": {"expected_calculation": "score", "denominator_min": 3},
        "benchmark": {"type": "config"},
        "_label": label,                            # popped out into topic_map, not kept in catalog
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default=str(REPO / "data/raw/adhoc/latest"))
    ap.add_argument("--pass-mark", type=float, default=0.80)
    args = ap.parse_args()
    raw_dir = Path(args.raw_dir)

    weight_class = _valid_weight_class()

    skill_m, skill_cats = _skill_metrics(raw_dir)
    cbt_m, cbt_cats = _cbt_metrics(raw_dir)
    metrics = {**skill_m, **cbt_m}
    if not metrics:
        raise SystemExit(f"No training source CSVs found in {raw_dir} (need training_assist_skills.csv / training_cbt.csv).")
    categories = list(dict.fromkeys(skill_cats + cbt_cats))

    # split the label out of each entry (used only for topic naming)
    labels = {m: meta.pop("_label") for m, meta in metrics.items()}

    category_defaults = {
        c: {
            "base_weight_class": weight_class,
            "confidence_floor": 0.30,                       # day grain: sparser evidence
            "display": {"scale": 100, "suffix": "%", "decimals": 0},
            "recalc": {"recipe": "quality"},                # pass-rate -> p25 recipe
        }
        for c in categories
    }

    metric_catalog = {"metric_catalog": {"category_defaults": category_defaults, "metrics": metrics}}
    benchmarks = {"benchmarks": {m: {"default": args.pass_mark} for m in metrics}}
    metric_to_topic = {m: f"Retake: {labels[m]}" for m in metrics}
    topic_to_ct = {f"Retake: {labels[m]}": "Re-training" for m in metrics}
    topic_map = {"topic_map": {"metric_to_topic": metric_to_topic, "topic_to_conversation_type": topic_to_ct}}

    OUT.mkdir(parents=True, exist_ok=True)
    _dump(OUT / "metric_catalog.yaml", metric_catalog,
          "# GENERATED by tools/gen_training_configs.py -- training skill + CBT metrics.\n"
          "# Re-run after the source data changes. Curate freely afterward.\n")
    _dump(OUT / "benchmarks.yaml", benchmarks,
          f"# GENERATED by tools/gen_training_configs.py -- per-metric pass-mark ({args.pass_mark:.0%}).\n")
    _dump(OUT / "topic_map.yaml", topic_map,
          "# GENERATED by tools/gen_training_configs.py -- metric -> remediation topic.\n")

    print(f"Wrote {len(metrics)} metrics ({len(skill_m)} skills, {len(cbt_m)} CBT) "
          f"across {len(categories)} categories to {OUT}")
    return 0


def _dump(path: Path, obj: dict, header: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(header)
        yaml.safe_dump(obj, f, sort_keys=False, default_flow_style=False, allow_unicode=True)


if __name__ == "__main__":
    raise SystemExit(main())
