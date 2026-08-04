

## python to run data extract process using yaml files under extraction

# 1. compile sql scripts (optional, useful if you want to validate sql)
python .\extraction\scripts\compile_sql.py --config .\extraction\configs\extract_run.yaml    

# 2. run extract (gets data from EDP and puts it in raw data files)
python .\extraction\scripts\run_extract.py --config .\extraction\configs\extract_run.yaml    

## python command to run the pipeline and turn raw data into signals
python -m cde.cli.run_pipeline --raw-dir data/raw/weekly/latest --out-dir outputs/runs/2026-03-03_TEST --configs-dir configs 

## recalculate benchmarks from the latest extract (propose-only; writes a dashboard + proposed change-set)
# does NOT modify configs/mappings/benchmarks.yaml
python -m cde.cli.recalc_benchmarks --configs-dir configs --out-dir outputs/benchmark_recalc/2026-03-03_recal
# then open outputs/benchmark_recalc/2026-03-03_recal/dashboard.html (OLD vs NEW + justification)

## apply the proposed benchmark changes (AUTHORIZED step only, after review)
# value-only edits to existing keys, preserves comments, appends configs/governance/changelog.md
python -m cde.cli.recalc_benchmarks --configs-dir configs --out-dir outputs/benchmark_recalc/2026-03-03_recal --apply --approver "Your Name"

## discover candidate coaching themes from population co-movement (propose-only)
# correlates metrics on a direction-adjusted "bad" axis per ICP_Client cohort and clusters
# the ones that move together into candidate themes; writes a dashboard + proposed_themes.yaml
# does NOT modify configs/mappings/themes.yaml (themes are added by a human SME only)
python -m cde.cli.discover_themes --configs-dir configs --out-dir outputs/theme_discovery/2026-03-03_themes
# then open outputs/theme_discovery/2026-03-03_themes/dashboard.html (candidate themes + verdict)
# a human SME renames + merges the wanted proposals from proposed_themes.yaml into configs/mappings/themes.yaml

## --apply for discovery only records a governance review note (still no auto-edit to themes.yaml)
python -m cde.cli.discover_themes --configs-dir configs --out-dir outputs/theme_discovery/2026-03-03_themes --apply --approver "Your Name"

