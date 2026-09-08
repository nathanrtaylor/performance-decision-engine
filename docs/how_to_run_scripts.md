

## python to run data extract process using yaml files under extraction

# 1. compile sql scripts (optional, useful if you want to validate sql)
python .\extraction\scripts\compile_sql.py --config .\extraction\configs\extract_run.yaml    

# 2. run extract (gets data from EDP and puts it in raw data files)
python .\extraction\scripts\run_extract.py --config .\extraction\configs\extract_run.yaml    

## python command to run the pipeline and turn raw data into signals
python -m pde.cli.run_pipeline --raw-dir data/raw/weekly/latest --out-dir outputs/runs/2026-03-03_TEST --configs-dir configs 
# signal gating runs in production mode (configs/thresholds/signal_thresholds.yaml: mode: production).
# outputs: recommendations.csv (actionable only) + abstentions.csv (agents withheld, with a reason) +
# decision_receipts.jsonl (recommendations + abstentions). To debug gating, temporarily set mode: development.
# When changing gating thresholds or the abstention floor (active.yaml: abstention.min_priority_score),
# re-run and compare eligible_signals / scores_windowed / recommendation + abstention counts before committing.

## TRAINING flow (TrAIning Assist + CBT) — run these THREE steps in order
# The training extract is isolated from the weekly snapshot: it writes to data/raw/adhoc/latest
# (see extraction/configs/extract_training_assist.yaml). Edit start_date/end_date there to cover
# the class start dates in the roster before running.

# 1. run the training extract (TrAIning Assist + CBT + coaching_history -> data/raw/adhoc/latest)
python extraction/scripts/run_extract.py --config extraction/configs/extract_training_assist.yaml

# 2. REQUIRED rollup: turn raw training_assist.csv into training_assist_skills.csv (tall-skinny, per-skill).
#    DON'T SKIP THIS. The pipeline preflight needs training_assist_skills.csv; without it you get:
#      ERROR snapshot: required table 'training_assist_skills' missing at ...\training_assist_skills.csv
#      config-lint FAIL / Preflight failed.
#    (training_cbt.csv is already day-grain tall-skinny -- no rollup needed for CBT.)
python -m pde.cli.build_training_assist_skills --raw-dir data/raw/adhoc/latest

# 3. run the training pipeline + dashboard. Reads the class roster fresh each run from
#    data/training/training_class_roster.xlsx (gitignored PII; drop the latest export there first).
#    The roster is the dashboard's source of truth for WHO appears -- agents not in it are dropped.
python -m pde.cli.run_training_pipeline --raw-dir data/raw/adhoc/latest --out-dir outputs/training_runs/2026-03-03_TEST
# output: outputs/training_runs/<run>/training_dashboard.html

## recalculate benchmarks from the latest extract (propose-only; writes a dashboard + proposed change-set)
# does NOT modify configs/mappings/benchmarks.yaml
python -m pde.cli.recalc_benchmarks --configs-dir configs --out-dir outputs/benchmark_recalc/2026-03-03_recal
# then open outputs/benchmark_recalc/2026-03-03_recal/dashboard.html (OLD vs NEW + justification)

## apply the proposed benchmark changes (AUTHORIZED step only, after review)
# value-only edits to existing keys, preserves comments, appends configs/governance/changelog.md
python -m pde.cli.recalc_benchmarks --configs-dir configs --out-dir outputs/benchmark_recalc/2026-03-03_recal --apply --approver "Your Name"

## discover candidate coaching themes from population co-movement (propose-only)
# correlates metrics on a direction-adjusted "bad" axis per ICP_Client cohort and clusters
# the ones that move together into candidate themes; writes a dashboard + proposed_themes.yaml
# does NOT modify configs/mappings/themes.yaml (themes are added by a human SME only)
python -m pde.cli.discover_themes --configs-dir configs --out-dir outputs/theme_discovery/2026-03-03_themes
# then open outputs/theme_discovery/2026-03-03_themes/dashboard.html (candidate themes + verdict)
# a human SME renames + merges the wanted proposals from proposed_themes.yaml into configs/mappings/themes.yaml

## --apply for discovery only records a governance review note (still no auto-edit to themes.yaml)
python -m pde.cli.discover_themes --configs-dir configs --out-dir outputs/theme_discovery/2026-03-03_themes --apply --approver "Your Name"

