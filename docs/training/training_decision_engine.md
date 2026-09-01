# Training Decision Engine — Design & Status

**Status:** Phases 0–2 complete on branch `training-insights`. Phase 3 (remediation engine) not yet started — this document is the review artifact before that build.
**Branch:** `training-insights` (based on `theme-cohort-scoping` / PR #18 tip).
**Last updated:** 2026-09-01.
**Scope of this doc:** everything we know and have built for the training decision engine to date, the design decisions and their rationale, open questions, and the proposed schema you'll use to define the program structure.

---

## 1. What it is

The **Training Decision Engine** extends the Coaching Decision Engine's methodology — *weight data → find signals → summarize → recommend* — from live-call coaching to **new-hire / expert training**.

Its specific goal is **remediation routing**: for each expert in training, surface — when needed — the concrete remediation steps, i.e. **which specific parts of their training to go back and retake**, targeted at the skills where they are deficient. When nothing is materially deficient, no remediation is prescribed (the expert is on-track / complete).

The output is a governed, per-expert **remediation plan** (what to retake, why, with the progression gate) plus a training dashboard — engine-generated and evidence-backed, not hand-written.

### The remediation model (three properties that drive the design)
- **Block-gated trigger.** Training is organized into sequential **learning blocks**, each ending in a **test call**. Performance below the acceptable threshold on that test call triggers remediation for the block. The typical prescription: retake the block's **skill-level simulations** (for the deficient skills) **and** the **end-of-block test call**, and the expert cannot progress until the gate is cleared.
- **Steerable scope.** We must be able to tune *how far back* to send an expert (this block only / back N blocks / to the block that first taught a weak skill) and *what areas* (which modalities and skills) a remediation covers — via config, without code changes.
- **Once-only.** An expert is never sent back to remediate material they have **already remediated**. A remediation-history exclusion guarantees plans don't loop on completed material.

---

## 2. Architecture — how it reuses the coaching engine

The core insight from the exploration phase: **~70–80% of the analytical engine is domain-generic** (percentile scoring, temporal windowing, evidence gating, prioritization, the three-tier selection layer), and most of the remaining difference is **pure YAML config**. Coaching-specific behavior is concentrated in ingestion, a shallow org-enrichment block, narrative text, and dashboards.

So training is an **additive, parallel domain**: it reuses the shared `src/cde` core unchanged and runs from **its own governed config set** (`configs/training/`). **The live-call coaching pipeline is untouched.**

```
Training data (tall-skinny)                     Shared engine (unchanged)
  TrAIning Assist skills  ─┐
  CBT completion/scores   ─┤→ build_signals → thresholds → temporal window →
                           │   scoring (percentile-of-deficit) → prioritization →
                           │   three-tier selection → abstention → receipts
                           │
  configs/training/ (isolated config set) drives all metric/benchmark/topic/threshold semantics
```

Everything specific to training — the metrics, benchmarks, directions, categories, topics, weights, gating thresholds, windowing — lives in `configs/training/`, not in code.

### Two data sources (not three)
1. **TrAIning Assist** (AI-assisted simulator): raw per-behavior `behaviorPresent` flags → rolled up to **skill-level pass-rates**; also the source of end-of-block **test calls**.
2. **Computer-Based Training (CBT)** (Workday Learning): course completion + **scores**. **The CBT `score` *is* the assessment** — there is no separate assessments feed.

Both conform to the engine's **tall-skinny contract**: `(agent_id, period, call_type, metric_key, numerator, denominator, calc)`, valued as a 0–1 pass-rate (`value = calc`), exactly like the coaching `behavior_scores` source.

### Temporal grain = DAY
Training progresses day-by-day (blocks, retakes), so the training domain runs on a **daily grain** rather than the coaching engine's weekly grain. This is a **config-level change, not a core rewrite**: the windowing selects the *last N distinct `period` values* (it is period-generic, not 7-day-calendar math), and the trend slope runs over real timestamps. Binding the training `period` to the event **day** and setting a day-unit `temporal:` block makes the whole pipeline day-grained. The window is a **rolling last-N-days** window (currently N = 30). Internal score columns keep their weekly-era names (`level_8w`, `weeks_present`) — the *values* are day-based; the names are just dictionary keys and can be cleaned up later.

---

## 3. Current status by phase

| Phase | Scope | Status | Commit |
|-------|-------|--------|--------|
| 0 | Branch + consolidate prior scattered work | **Done** | `d552279` |
| 1 | Training config set + first recommendations from real sim data | **Done** | `e434ad1` |
| 2 | CBT source (= completion + assessment scores) | **Done (day-ready)** | `b3e1fb3` |
| 3 | Program structure + block-gated remediation engine | **Engine built + tested; structure being defined** | — |
| 4 | Remediation plans + real dashboard + training narratives | **Built + validated** (synthetic roster fields pending real roster) | — |
| 5 | Config-driven org-enrichment, governance, tests, docs | Not started | — |

### Phase 0 — consolidation
Created `training-insights` off the PR #18 tip (green baseline + newest primitives). Brought the previously-scattered training work onto one branch — the TrAIning Assist extract/ingestion/profiles + the ASCEND dashboard generator and its JSON contract — with zero conflicts. Suite green.

### Phase 1 — config set + validated recommendations
Built the isolated `configs/training/` set and cataloged **22 TrAIning Assist skills across 11 categories** (all `higher_is_better`, 80% pass-mark, `eligible_for_prioritization`), with remediation-flavored `Retake: <skill>` topics → conversation type `Re-training`. Day-grain `temporal:` block; **abstention ON** (cleanly separates "needs remediation" from "on-track").

Validated end-to-end on the current extract (`run_pipeline --configs-dir configs/training --raw-dir data/raw/adhoc/latest`): config-lint clean; **104 remediation recommendations vs 2,075 on-track (abstained)**; receipts carry real evidence (e.g. *"account_assessment averages 22% against a benchmark of 80% … the gap has been widening, so it is timely to intervene"*). This proves the engine-reuse thesis with **zero core-code changes**.

### Phase 2 — CBT source (day-ready)
Added `extraction/sql/training_cbt.sql.j2` (DAY grain) reading `hive.care.l1_asurion_flatfile_dbo_workday_learning_course_enrollment` → tall-skinny `(agent_id, period=completion day, courseid, …, calc = mean(score)/100)`. Registered the `training_cbt` source; made `tools/gen_training_configs.py` multi-source (auto-catalogs `cbt_<courseid>` metrics once `training_cbt.csv` exists). Because the CBT score is the assessment, **no separate assessments extract is needed**.

---

## 4. What's built — files & where

**Config set — `configs/training/`** (isolated; the live-call `configs/` is untouched)
- `active.yaml` — composition root; day-grain `temporal:` block; abstention ON; `Re-training` conversation type.
- `mappings/source_catalog.yaml` — `agents` roster dimension, `training_assist_skills`, `training_cbt`.
- `mappings/metric_catalog.yaml` — 22 skill metrics + category defaults (generated).
- `mappings/benchmarks.yaml` — per-metric 80% pass-mark (generated).
- `mappings/topic_map.yaml` — `Retake: <label>` topics → `Re-training` (generated).
- `thresholds/signal_thresholds.yaml` — day-grain evidence gates.
- `priorities/v2026_09_01_training_baseline.yaml` — equal category weights (tunable).

**Extraction**
- `extraction/sql/training_assist.sql.j2` — sim behavior extract (currently week-grained; day-flip pending re-extract).
- `extraction/sql/training_cbt.sql.j2` — CBT extract (day-grained).
- `extraction/configs/extract_training_assist.yaml` — runs both training outputs into `data/raw/adhoc`.

**Code (reused / added)**
- Ingestion: `src/cde/ingestion/training_assist_skills.py` + CLI `src/cde/cli/build_training_assist_skills.py` (behavior → skill rollup via `configs/mappings/training_profiles.yaml`).
- Generator: `tools/gen_training_configs.py` (regenerates the mechanical mapping configs from the real data).
- Dashboard mock + JSON contract: `tools/ascend_training_dashboard_example.py` (the target output shape for Phase 4).
- **Reused unchanged:** `scoring/assemble.py`, `temporal/aggregate.py`, `signals/*`, `prioritization/*`, `engine/*` (select / break_glass / themes / recommend / abstain / receipts), `reporting/dashboard_kit.py`.

**Built (Phase 3 — remediation engine):**
- `configs/training/training_program.yaml` — the 16-block ASCEND Launchpad seed (Section 5).
- `configs/training/remediation.yaml` — the steerable policy (how-far-back, areas, once-only, behind-schedule).
- `src/cde/training/program.py` — loader, skill→block routing, coverage report, block-gate evaluation, `expected_completion_day` derivation, behind-schedule check, and the `plan_remediation` mapper (scope policy + once-only + block-level fallback for partial structure).
- `src/cde/cli/check_training_program.py` — coverage checker (defined-vs-TODO).
- `tests/test_training_program.py` — 19 unit tests. Validated on the real Phase-1 deficient experts (routes skills → blocks; block-level fallback while components/gates are TODO).

**Built (Phase 4 — plans, dashboard, narratives):**
- `src/cde/reporting/training_dashboard.py` — `build_training_records` (UI-agnostic per-expert records, schema v1.1) + a data-backed HTML dashboard. Filters incl. **class ID**; program summary tiles recompute live from the filters (incl. time-progress: avg days in program, % on pace); current block shown as "Block N — short"; per-expert **on-track vs expected completion day**; behavior/skill data **rolled up under the learning blocks**; remediation framed as **Learning / Training-support / Coaching** actions.
- `src/cde/explainability/training_templates.py` — the three action-group narratives.
- `src/cde/cli/run_training_pipeline.py` — runs the shared engine over the training configs, then builds the dashboard. Validated end-to-end (`outputs/training_runs/…/training_dashboard.{html,json}`); `tests/test_training_dashboard.py` (7 tests).

**Not yet built (Phase 5):** remediation-history ingestion (the once-only data source); real roster fields (class ID, training start date, current block — currently synthesized for validation); config-driven org-enrichment; and — until block-gate test-call data exists — remediation triggers on any reached skill below the mark (the fallback), narrowing to true block-gate failures once gates are populated.

---

## 5. Proposed `training_program.yaml` schema (for you to define)

Phase 3's crux is the **program structure**, which you will define incrementally. It is a config you fill in a block at a time; the engine runs on whatever is defined and a checker reports what's still missing. Below is the **proposed schema** (empty scaffold + field docs) — please review and adjust the shape before we build against it.

> **Seeded from the ASCEND strategy doc.** A 16-block instance now exists at `configs/training/training_program.yaml` (blocks, order, labels, assessment methods, simulation counts, gates at `pass_mark: 0.90`, and best-effort `develops:` skill mappings — all 22 cataloged skills covered). It adds **pacing dimensions** on top of the schema below: per-block `estimated_hours` and **`expected_completion_day`** (days since training start, derived from cumulative hours at a program-level `pace.hours_per_day` knob — provisional until the real class calendar replaces it). `gate.test_call` refs, component (CBT/sim) enumeration, and the tools/troubleshooting skill mappings remain `TODO`.

```yaml
# configs/training/training_program.yaml  (PROPOSED SCHEMA -- for review)
program:
  name: "<program name>"           # e.g. the new-hire training program
  # Learning blocks in the order an expert progresses through them.
  blocks:
    - id: block_1                   # stable id used by program.py + receipts
      label: "<human label>"        # shown in the dashboard
      order: 1                      # progression order (lower = earlier)

      # The end-of-block TEST CALL that gates progression to the next block.
      # Identifies which TrAIning Assist test-call result represents this gate.
      gate:
        test_call: "<challenge_id or test-call key in the data>"
        pass_mark: 0.80             # optional; falls back to a program/global default

      # Remediable COMPONENTS in this block. Each names the skill(s) it develops,
      # which drives routing (deficient skill -> the component(s) to retake).
      components:
        - kind: skill_sim           # a TrAIning Assist practice scenario
          ref: "<challenge_id / profile>"
          develops: [ "<skill_id>", "<skill_id>" ]
        - kind: cbt                 # a Workday Learning course (score = assessment)
          ref: "<courseid>"
          develops: [ "<skill_id>" ]

    # - id: block_2 ...             # add blocks as you define them

  # Optional program-level defaults (per-block gate.pass_mark overrides these).
  defaults:
    gate_pass_mark: 0.80
```

**Notes on the schema**
- `develops:` is the routing table in inverse form (component → skills). `program.py` derives the forward map (deficient skill → components to retake) from it. This keeps the config in the natural "here's what this course/sim teaches" phrasing.
- **Partial is fine.** Unmapped skills, courses not tied to a block, or a block missing a gate are reported by the checker as `TODO`, not treated as errors. The engine remediates only where the structure is defined.
- Skill ids are the ones already in the catalog (e.g. `warm_greeting`, `account_assessment`). CBT `ref` is the `courseid` (cataloged as `cbt_<courseid>` once the CBT extract lands). Sim `ref` is the `challenge_id`.

### Proposed `remediation.yaml` (steerable policy)
```yaml
# configs/training/remediation.yaml  (PROPOSED SCHEMA -- for review)
remediation:
  gate_pass_mark: 0.80            # acceptable performance on a block-gate test call
  scope:
    how_far_back: current_block   # current_block | back_n | to_skill_origin
    back_n: 1                     # used when how_far_back = back_n
    include_modalities: [ skill_sim, test_call ]   # what a remediation covers
    skills: deficient_only        # deficient_only | whole_block
  once_only: true                 # never re-prescribe already-remediated material
```

---

## 6. Key design decisions & rationale

| Decision | Choice | Why |
|----------|--------|-----|
| Branch vs fork | **Branch** (`training-insights`), same repo | ~70–80% of the core is reusable unchanged; most difference is config; prior training work already lived here; a fork would strand it and guarantee drift. Extract a shared package only if training later becomes a separately-governed product. |
| Branch base | `theme-cohort-scoping` (PR #18 tip) | Green baseline + newest primitives (composite cohorts, pooled windowing). Rebase onto `main` after PR #18 merges. |
| Config isolation | Dedicated `configs/training/` set via `--configs-dir` | Full isolation + independent governance; shared code; the coaching pipeline is untouched. |
| Grain | **Day** (rolling last-N-days window) | Training progresses day-by-day; supported by config alone (period-generic windowing). |
| Materiality | **Abstention ON** | Cleanly separates "needs remediation" from "on-track"; a withheld remediation is an explicit, explained decision. |
| Benchmarks | Flat 80% pass-mark (first pass) | Simple start; per-skill calibration via the recalc module against real daily data (see open items). |
| Assessments | = CBT `score` | Confirmed: not a separate feed. |

---

## 7. Assumptions to confirm & open items

**CBT assumptions (each a one-line SQL change, flagged in `training_cbt.sql.j2`):**
- `userid` is the **numeric employee id** (so CBT joins TrAIning Assist on `agent_id`). If it's a username, we add a `userid → employee_id` map.
- `score` is on a **0–100** scale (normalized `/100` to the 0–1 pass-rate scale). If already 0–1, drop the `/100`.

**Reconciliation gaps (surfaced during Phase 1 ingestion; SME review):**
- 3 sim behaviors have no skill mapping and are dropped: `ask to enroll`, `celebrate enrollment`, `disclosures statement`.
- 11 `challenge_id`s in the data are not in `training_profiles.yaml` (look like newer/test simulator profiles); their rows are dropped until reconciled.

**Benchmark calibration:** a flat 80% pass-mark misfits some skills — e.g. `resolve_concerns` never dips below 0.83, so nobody is ever "deficient" on it. Tune per-skill pass-marks with the benchmark-recalc module against real daily data.

**Validation caveat:** Phase 1 numbers are **plumbing-valid on weekly data with day-grain knobs**; real magnitudes await the **daily re-extract**. CBT is **day-ready but not yet validated end-to-end** (no DB access locally).

**Program structure:** the biggest open item — the learning blocks, their components, gates, and skill mappings (Section 5) — is a domain/SME definition you are working on.

---

## 8. How to run (today)

```bash
# 1. Extract training data (needs DB access) -> data/raw/adhoc/latest/
python extraction/scripts/run_extract.py --config extraction/configs/extract_training_assist.yaml

# 2. Roll TrAIning Assist behaviors up to skill pass-rates
python -m cde.cli.build_training_assist_skills --raw-dir data/raw/adhoc/latest

# 3. (Re)generate the mechanical training configs from the real data
python tools/gen_training_configs.py --raw-dir data/raw/adhoc/latest

# 4. Validate config integrity
python -m cde.cli.check_config --configs-dir configs/training --raw-dir data/raw/adhoc/latest

# 5. Run the training domain through the shared engine
python -m cde.cli.run_pipeline --configs-dir configs/training \
    --raw-dir data/raw/adhoc/latest --out-dir outputs/training_runs/<date>
```
(A dedicated `run_training_pipeline` entrypoint + training dashboard arrive in Phase 4.)

---

## 9. Roadmap

- **Phase 3 — Program structure + remediation engine.** Define `training_program.yaml` (Section 5) incrementally; build `program.py` (block/modality status, block-gate trigger, steerable last-N-days scope, once-only exclusion) + `remediation.yaml` + a coverage checker that reports defined-vs-missing. Validate against the TrAIning Assist data already flowing.
- **Phase 4 — Remediation plans + dashboard + narratives.** Per-expert remediation plan (retake targets + progression gate + evidence + role-split expert/trainer/coach actions); promote the mockup into a real data-backed `training_dashboard.py`; write remediation-phrased narratives (`training_templates.py`); add `run_training_pipeline.py`.
- **Phase 5 — Enrichment, governance, tests, docs.** Config-driven org-enrichment (trainee roster: class / trainer / tenure_group), config-lint coverage for the program structure, tests, and README.

---

## 10. Guiding principle (inherited from the coaching engine)

> The system should not slow leaders down. It should make fast decisions work on purpose.

For training, that means: the moment an expert falls short at a block gate, the engine names exactly what to retake and why — and stays quiet for everyone who's on track.
