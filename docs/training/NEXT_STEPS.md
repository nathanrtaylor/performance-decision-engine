# Training Decision Engine — pick-up notes (2026-09-01)

Quick handoff to resume tomorrow. Full design/status: `docs/training/training_decision_engine.md`.

## Where we are
Branch **`training-insights`** (off `theme-cohort-scoping` / PR #18 tip). Phases 0–4 done & committed; full suite green (226 tests); live-call coaching pipeline untouched.

```
a839b96  Phase 4: training pipeline + data-backed remediation dashboard
2150de5  Phase 3 (engine): block-gated, steerable, once-only remediation logic
0b9e60b  Phase 3 (seed): ASCEND program structure + design/status docs
b3e1fb3  Phase 2 (CBT): day-grained CBT source (day-ready)
e434ad1  Phase 1: training-insights config set (day grain) + generator
d552279  Phase 0: consolidate scaffolding
```

**Working & validated end to end:** TrAIning Assist skills → shared engine → per-expert remediation plans + `training_dashboard.{html,json}` (1,442 experts). Dashboard has: class-ID filter, filter-linked summary tiles incl. time-progress, "Block N — short", on-track vs expected completion day, skills rolled up under blocks, Learning/Training-support/Coaching actions. Anonymized shareable copy: `docs/training/training_dashboard_shareable.html` (untracked).

## Next up — Phase 5 (in rough priority)
1. **Real expert roster** — replace the synthetic `class_id` / `training_start_date` / `current_block_order` (currently generated into `data/raw/adhoc/latest/agents.csv`) with the real roster. Makes pacing / on-track / class filter live and drops the "synthetic roster fields" badge.
2. **Fill `configs/training/training_program.yaml` TODOs** — per-block `gate.test_call` (the end-of-block Training Assist test call) + `components` (CBT `courseid`s + skill sims) + tools/troubleshooting `develops:` maps. This flips remediation from the deficient-skill fallback to **gate-driven**. Check with `python -m cde.cli.check_training_program`.
3. **Remediation-history ingestion** — the once-only source (blocks/components already remediated); `plan_remediation` already accepts `history`.
4. **CBT extract** — run `extract_training_assist.yaml` (needs DB) → confirm the two SQL assumptions (`userid` = numeric employee id; `score` is 0–100) → `gen_training_configs.py` catalogs the courses.
5. **TrAIning Assist day re-extract** — the sim extract is still weekly (`week_ending`); switch its SQL to session-day for true daily grain.
6. **Tune the remediation fallback** — add a materiality margin (skill must be materially below the mark) to cut noise until gates land; and/or calibrate per-skill pass-marks with the benchmark-recalc module.

## Assumptions to confirm (flagged in code)
- CBT `userid` == employee id; CBT `score` 0–100 (both one-line changes in `extraction/sql/training_cbt.sql.j2`).
- `expected_completion_day` derived at `pace.hours_per_day: 6` (content-only) — replace with real class calendar.
- Reconciliation gaps: 3 unmapped sim behaviors; 11 challenge_ids not in `training_profiles.yaml`.
- Rebase `training-insights` onto `main` once PR #18 merges.

## Resume commands
```bash
# validate + run end to end (uses the current adhoc extract + synthetic roster)
python -m cde.cli.check_config --configs-dir configs/training --raw-dir data/raw/adhoc/latest
python -m cde.cli.check_training_program
python -m cde.cli.run_training_pipeline --raw-dir data/raw/adhoc/latest --out-dir outputs/training_runs/<date>
python -m pytest -q
# regenerate mechanical configs after new training data lands
python -m cde.cli.build_training_assist_skills --raw-dir data/raw/adhoc/latest
python tools/gen_training_configs.py --raw-dir data/raw/adhoc/latest
```

## Key files
- Config set: `configs/training/` (`active.yaml`, `training_program.yaml`, `remediation.yaml`, `mappings/`, `thresholds/`, `priorities/`).
- Engine: `src/cde/training/program.py`; dashboard: `src/cde/reporting/training_dashboard.py`; narratives: `src/cde/explainability/training_templates.py`; pipeline: `src/cde/cli/run_training_pipeline.py`; checker: `src/cde/cli/check_training_program.py`.
- Source doc for the program: `docs/training/Ascend Learning Blocks Training Strategy.docx`.
