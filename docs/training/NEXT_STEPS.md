# Training Decision Engine — pick-up notes (2026-09-02)

Quick handoff to resume. Full design/status: `docs/training/training_decision_engine.md`.

> **Update 2026-09-09:** much has shipped since this note. **WI-3** (all TrAIning Assist behaviors
> mapped to skills) and **WI-5** (the `ASC-ASC-SIM-` SIM-ID typo, now fixed in the generator) are
> **DONE**. **CBTs are mapped to all 16 blocks** (`kind: cbt` components, read from the curriculum
> course export by `gen_program_components.py`). Pace and current block are now **inferred from
> crosswalked sim/CBT activity** — the roster `current_block_order` feed was removed. `pace.hours_per_day`
> is **7.0**. `check_training_program` validates the sim_id crosswalk + CBT coverage. The phase list,
> counts, and TODO items below are HISTORICAL — treat this banner as the current state.

## Latest session (2026-09-02) — dashboard polish, committed `113f415`
232 tests green. Suite + review-sim regenerate clean.
- **Completion hand-off report** for graduated experts (all blocks passed): readiness summary,
  watch-in-production skills, coach hand-off note — shown in place of the remediation plan.
- **Copy / Email / PDF share toolbar** on each expert (upper-right of the modal). Copy & Email
  build a plain-text summary (`summaryText()`); PDF uses a scoped `@media print` view.
- **ASCEND branding**: logo bundled at `src/pde/reporting/assets/ascend_logo.svg`, embedded as a
  base64 data URI (`_logo_data_uri()` + `__LOGO__` placeholder) so shared/emailed copies keep it.
  Shown upper-left inline with the title and the expert name; stays on the printed/PDF page. Tab
  title → "ASCEND Performance Decision Engine".
- **Locked-block blanking fix**: skills roll up only under blocks the expert has *reached*
  (`order <= current`); a skill shared with an earlier block no longer leaks scores into a locked
  block (was 471 such blocks). Locked/not-tracked blocks render blank.
- **Status honesty**: progress feed but no skill data yet → `in_training` (started), not
  `not_started` (now reserved for no-feed + no-data).
- **Roster format**: `roster.py` reads the current export headers (`Class ID`; `Class Type`
  dropped) and still the old ones; dedupes columns. Real roster is now **230 experts / 13 classes**.
- Review sim (`tools/gen_training_review_dashboard.py`) forces ~3 completed experts so the hand-off
  report is reviewable; output `outputs/training_runs/review_<date>/` (gitignored, labeled
  SIMULATED — REVIEW: real names + synthetic performance).

## Launch context (important)
**ASCEND launches 2026-09-14.** Until then TA runs the LEGACY sims (challenge_ids like
`victor_marks`), and the current roster classes (started 8/24) are pre-ASCEND — so the
Sims-100 personas/SIM-IDs in `training_program.yaml` do NOT match the live TA data yet
(0/93), roster experts show `not_started`, and gate/progress aren't scoring-linked. This
is expected pre-launch, not a defect. Components are keyed by `persona` (forward-looking
TA challenge_id), so once ASCEND cohorts run the new sims in TA the crosswalk resolves and
gates/progress/block-gated remediation light up automatically. The current runs are a
pre-launch dry run of the plumbing. Full ASCEND validation needs a post-9/14 cohort + roster.

## Where we are
Branch **`training-insights`** (off `theme-cohort-scoping` / PR #18 tip). Phases 0–4 done & committed + dashboard polish; full suite green (232 tests); live-call coaching pipeline untouched.

```
113f415  Training dashboard: hand-off report, share toolbar, ASCEND branding, locked-block fix
f432e0b  Training dashboard: 3-week pacing, pace signal, block-number remediation, demo
a839b96  Phase 4: training pipeline + data-backed remediation dashboard
2150de5  Phase 3 (engine): block-gated, steerable, once-only remediation logic
0b9e60b  Phase 3 (seed): ASCEND program structure + design/status docs
b3e1fb3  Phase 2 (CBT): day-grained CBT source (day-ready)
e434ad1  Phase 1: training-insights config set (day grain) + generator
d552279  Phase 0: consolidate scaffolding
```

**Working & validated end to end:** TrAIning Assist skills → shared engine → per-expert remediation plans + `training_dashboard.{html,json}` (1,442 experts). Dashboard has: class-ID filter, filter-linked summary tiles incl. time-progress, "Block N — short", on-track vs expected completion day, skills rolled up under blocks, Learning/Training-support/Coaching actions. Anonymized shareable copy: `docs/training/training_dashboard_shareable.html` (untracked).

## Next up — Phase 5 (in rough priority)
1. **DONE — real roster wired.** `src/pde/training/roster.py` reads `docs/training/training_class_roster.xlsx` (sheet "Expert Roster"); `run_training_pipeline` uses it as the **source of truth** for who appears + class_id (Session #) / trainer / start date. Validated: 121 experts, 6 classes. Roster experts with no skill data yet show `not_started`.
2. **DONE — TrAIning Assist is day-grained.** `training_assist.sql.j2` now emits `period = session day` (matches `training_cbt`); extract config `expected_columns` updated; window set to 2026-08-22…09-04 to cover the roster classes. Needs the DB re-extract run to land matching day data.
3. **Run the DB extract** (`extract_training_assist.yaml`, needs DB) so day-grain sim + CBT data lands for the roster window → skill/remediation content populates (currently 0/121 match because the on-disk extract predates the classes). Confirm CBT `userid`=employee id and `score` 0–100.
4. **Fill `training_program.yaml` TODOs** — per-block `gate.test_call` + `components` + tools/troubleshooting `develops:`. Flips remediation from the deficient-skill fallback to **gate-driven**, and gives a per-expert **progress feed** (current block from block completions/test calls) so pacing/on-track become exact instead of expected-position.
5. **Remediation-history ingestion** — the once-only source; `plan_remediation` already accepts `history`.
6. **Tune the remediation fallback** — materiality margin and/or per-skill pass-mark recalc.

## Assumptions to confirm (flagged in code)
- CBT `userid` == employee id; CBT `score` 0–100 (both one-line changes in `extraction/sql/training_cbt.sql.j2`).
- `expected_completion_day` is hand-set per block (pace is now `hours_per_day: 7.0`) — replace with real class calendar.
- Reconciliation gaps: 3 unmapped sim behaviors; 11 challenge_ids not in `training_profiles.yaml`.
- Rebase `training-insights` onto `main` once PR #18 merges.

## Resume commands
```bash
# validate + run end to end (uses the current adhoc extract + synthetic roster)
python -m pde.cli.check_config --configs-dir configs/training --raw-dir data/raw/adhoc/latest
python -m pde.cli.check_training_program
python -m pde.cli.run_training_pipeline --raw-dir data/raw/adhoc/latest --out-dir outputs/training_runs/<date>
python -m pytest -q
# regenerate mechanical configs after new training data lands
python -m pde.cli.build_training_assist_skills --raw-dir data/raw/adhoc/latest
python tools/gen_training_configs.py --raw-dir data/raw/adhoc/latest
```

## Key files
- Config set: `configs/training/` (`active.yaml`, `training_program.yaml`, `remediation.yaml`, `mappings/`, `thresholds/`, `priorities/`).
- Engine: `src/pde/training/program.py`; dashboard: `src/pde/reporting/training_dashboard.py`; narratives: `src/pde/explainability/training_templates.py`; pipeline: `src/pde/cli/run_training_pipeline.py`; checker: `src/pde/cli/check_training_program.py`.
- Source doc for the program: `docs/training/Ascend Learning Blocks Training Strategy.docx`.
