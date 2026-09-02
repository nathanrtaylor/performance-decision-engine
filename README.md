# Performance Decision Engine

A deterministic, explainable engine that turns operational performance data into
**governed, auditable recommendations** — and applies the *same* decision core to more
than one performance problem.

Today it powers two domains:

- **Coaching** — from live-call Auto QA, recommend the single most appropriate **coaching
  topic and conversation type** for each expert's next session.
- **Training** — from new-hire simulation and computer-based-training data, surface the
  concrete **remediation** an expert needs: which parts of onboarding to go back and retake,
  and whether they are on pace to graduate.

Both are the same machine — signals → scoring → priorities → selection → explainable
receipt — pointed at a different, governed configuration set. Every recommendation it makes is:

- **Business-aligned** — priorities are set by Ops, in versioned config, not buried in code.
- **Deterministic** — same data + same config → same decision, every time.
- **Versioned & auditable** — every run snapshots the config and data it used.
- **Explainable** — each decision carries a receipt: why this, why now, why not the others.

It is designed to absorb frequent priority changes without losing rigor or credibility.

---

# One Engine, Two Domains

| | **Coaching domain** | **Training domain** |
|---|---|---|
| **Question it answers** | "What should this expert's next coaching session be about?" | "What should this new hire go back and retake — and are they on track?" |
| **Population** | Live production agents | New-hire / conversion trainees (ASCEND Launchpad) |
| **Primary inputs** | Auto QA behavior scores, agent metrics | TrAIning Assist simulations, computer-based-training scores |
| **Grain** | Week | Day |
| **Output** | One coaching topic + conversation type per agent | Per-expert remediation plan + learning-block progress dashboard |
| **Config set** | `configs/` | `configs/training/` |
| **Entry point** | `pde.cli.run_pipeline` | `pde.cli.run_training_pipeline` |

**~70–80% of the engine is domain-generic and shared unchanged** (extraction, signal
construction, gating, scoring, prioritization, the selection layer, receipts). A domain is
defined by (a) an isolated, governed **config set**, selected with `--configs-dir`, and (b) a
few thin domain-specific modules layered on top. The live coaching pipeline and the training
pipeline never interfere: they read different configs and write different outputs.

---

# Architecture Overview

The shared core is a layered, modular pipeline:

```
Presto / Source Systems
        ↓
Extraction Layer (versioned raw snapshots)
        ↓
Signal Construction
        ↓
Signal Gating (evidence thresholds)
        ↓
Multi-Axis Scoring (deficit, trend, confidence, risk)
        ↓
Prioritization (versioned business weights)
        ↓
Deterministic Selection
        ↓
Decision Receipts (why this / why now / why not others)
```

The **coaching** domain adds a three-tier selection layer (break-glass → theme → single) on
top; the **training** domain adds program-structure + remediation logic and a per-expert
dashboard. Neither changes the shared middle.

## Core Design Principles

- **One engine, many domains** — reuse the decision core; express each new problem as governed config.
- Business outcomes beat behavioral purity.
- Determinism is a feature.
- Explainability is mandatory.
- Flexibility must be governed.
- Configuration lives inside the system.
- Learning is constrained and human-controlled.

---

# Repository Structure

```
configs/                       # COACHING domain config set
  active.yaml
  mappings/  (source_catalog, metric_catalog, topic_map, benchmarks, themes, coaching_history_map)
  thresholds/  priorities/

configs/training/              # TRAINING domain config set (isolated; selected via --configs-dir)
  active.yaml
  training_program.yaml        # the ASCEND learning blocks, gates, pacing
  remediation.yaml             # steerable remediation policy
  training_profiles.yaml       # skills + simulator profiles
  mappings/  thresholds/  priorities/

data/
  raw/weekly/<snapshot_id>/    # immutable coaching snapshots (+ latest/ pointer)
  raw/adhoc/<snapshot_id>/     # training snapshots (+ latest/ pointer)

extraction/
  sql/         (agent_metrics, behavior_scores, coaching_history, training_assist, training_cbt, …)
  configs/     (extract_run.yaml [coaching], extract_training_assist.yaml [training])
  scripts/

src/pde/
  signals/  scoring/  prioritization/  temporal/   # shared core
  engine/          # shared selection layer: select / break_glass / themes / recommend / abstain / receipts
  reporting/       # dashboard_kit + expert_dashboard (coaching) + training_dashboard (training)
  governance/  benchmarks_recalc/  themes_discovery/
  training/        # training-only: program.py (blocks/gates/remediation), roster.py
  cli/             # run_pipeline (coaching), run_training_pipeline (training), check_config, check_training_program, …

docs/training/     # program source material, class roster, design docs
outputs/
  runs/<timestamp>/            # coaching pipeline runs
  training_runs/<id>/          # training pipeline runs
  benchmark_recalc/<id>/  theme_discovery/<id>/
```

---
---

# PART I — THE COACHING DOMAIN

The original and most mature application: recommend the next coaching topic for live agents.

## Configuration Layers

Each configuration file has a single responsibility.

| Layer | File | Purpose |
|-------|------|----------|
| Source ingestion | source_catalog.yaml | Dataset schemas + signal computation rules |
| Metric registry | metric_catalog.yaml | Canonical metric definitions |
| Topic semantics | topic_map.yaml | Metric → coaching topic mapping |
| Coaching history | coaching_history_map.yaml | Coaching behavior → topic crosswalk (for dampening) |
| Benchmarks | benchmarks.yaml | Target/reference values |
| Coaching themes | themes.yaml | Theme name → member metrics + conversation type (SME-curated) |
| Signal gating | signal_thresholds.yaml | Eligibility rules |
| Business emphasis | priorities/*.yaml | Versioned weight configurations |
| Active pointer | active.yaml | Selects current config set; may include `data_snapshot` |

Only `priorities/` and `active.yaml` should change frequently.
`conversation_types.by_topic` in `active.yaml` overrides `topic_map.topic_to_conversation_type`.

## Adding a New Coachable Metric

A metric has to be registered consistently across several config files before the pipeline will
score it, recommend it, and explain it. The config-integrity linter (run automatically in the
preflight, or on demand with `python -m pde.cli.check_config`) will flag any missing
cross-reference — one broken edge at a time. This section is the **happy path**: do all the
steps below in one pass and the metric passes the linter and becomes coachable on the first run.

**Assumption:** the metric already comes from a query/source the pipeline ingests — i.e. a row for
it already lands in an existing tall source table (e.g. `agent_metrics`), keyed by some metric-name
string. If so, **no extraction or `source_catalog.yaml` change is needed**: `build_signals` selects
metrics by matching `(source, source_metric_key)` from `metric_catalog` against the rows that source
already emits. (If the metric is *not* yet in any query, extract it first — a separate upstream task.)

### The five surfaces (2 optional)

| # | File | What you add | Required? |
|---|------|--------------|-----------|
| 1 | `mappings/metric_catalog.yaml` | The metric definition (identity, direction, category, benchmark source) | **Required** |
| 2 | `mappings/topic_map.yaml` | Metric → coaching topic, and topic → conversation type | **Required** |
| 3 | `mappings/benchmarks.yaml` | The reference/target value(s) | **Required** when `benchmark.type: config` |
| 4 | `thresholds/signal_thresholds.yaml` | Per-metric evidence-gating override | Optional — inherits `by_category` |
| 5 | `priorities/<active>.yaml` | Per-metric weight override | Optional — inherits `by_category` |
| (+) | `mappings/coaching_history_map.yaml` | Delivered-coaching behavior → this topic (so it dampens) | Optional |

### Worked example

Adding `reopen_rate` (reopened tickets / resolved tickets; lower is better), already emitted by
`agent_metrics` under the source key `"reopen rate"`.

**1. `mappings/metric_catalog.yaml`** — under `metric_catalog.metrics:`:

```yaml
    reopen_rate:
      source: agent_metrics
      source_metric_key: "reopen rate"    # EXACT metric string as it appears in the source rows
      category: solve                     # MUST be a key in metric_catalog.category_defaults
      direction: lower_is_better          # REQUIRED (a blank/misspelled direction is a hard linter error)
      unit: rate
      description: "Reopened tickets / resolved tickets."
      eligible_for_prioritization: true   # THIS is what makes the metric recommendable
      computation_override:
        expected_calculation: rate
        denominator_min: 20
      benchmark:
        type: config                      # value supplied by benchmarks.yaml (step 3)
```

**2. `mappings/topic_map.yaml`** — register the topic in **both** maps:

```yaml
  metric_to_topic:
    reopen_rate: "Reduce Reopen Rate"
  topic_to_conversation_type:
    "Reduce Reopen Rate": "Performance Correction"
```

**3. `mappings/benchmarks.yaml`** — `default` is mandatory; per-cohort overrides optional:

```yaml
  reopen_rate:
    default: 0.08
    by_icp_client:
      mob-verizon: 0.06
```

**4–5 (optional)** — per-metric gating / weight overrides in `signal_thresholds.yaml` /
`priorities/<active>.yaml`; otherwise inherit `by_category`. **(+)** add to
`coaching_history_map.yaml` only if delivered coaching for the topic should dampen it.

### Derived (composite) metrics

A **derived** metric has *no* source row and is computed from *other* metrics'
`numerator`/`denominator` columns. Use `source: derived` plus a `derived:` block (e.g.
`sp100 = enrolled ÷ Sale Opportunities`). A group missing any component is **skipped**, never
fabricated. Implementation: `src/pde/signals/derived_metrics.py`.

### Validate before the full run

```
python -m pde.cli.check_config --configs-dir configs
```

A clean metric produces `config-lint PASS`. The pipeline runs this automatically as a preflight
(`--strict-preflight` makes warnings fatal).

## Onboarding an ICP Client cohort

The cohort roster has **one source of truth**: `icp_clients:` in `configs/active.yaml`. That single
edit feeds extraction (`icp_clients_from: configs/active.yaml`), benchmark recalculation, and
per-cohort values under `by_icp_client`. `python -m pde.cli.check_config` fails if anything drifts.

## Data Model

All sources are tall-skinny tables at the grain **agent_id × period × call_type × metric**, with
canonical columns `agent_id, period, call_type, metric, numerator, denominator, calc`. Raw snapshots
live under `data/raw/weekly/<snapshot_id>/` and are immutable; `latest/` is auto-maintained.

## Extraction Layer

Compiles parameterized SQL → executes via SQLAlchemy (Presto) → writes versioned raw CSV snapshots →
maintains a `latest/` pointer → produces a manifest. Run from repo root:

```bash
python extraction/scripts/run_extract.py --config extraction/configs/extract_run.yaml
```

For `agent_metrics`, the metric list is filled from `metric_catalog.yaml` when `metrics_from_catalog`
is set, so extraction stays aligned with the decision catalog. `compile_sql.py --config …` compiles
only (debugging).

## Running the Coaching Pipeline

```bash
python -m pde.cli.run_pipeline --out-dir outputs/runs/2026-03-03_TEST --configs-dir configs
```

`--raw-dir` is optional when `data_snapshot` is set in `active.yaml`.

**Outputs** include `recommendations.csv` (with a `tier` column = break_glass | theme | single),
`abstentions.csv`, `decision_receipts.jsonl`, `scores_windowed.csv`, `summary_dashboard.html`, and
`expert_dashboard.html` (interactive per-expert receipts). Diagnostics: `signals.csv`,
`topic_candidates.csv`, `eligible_signals.csv`, `manifest.json`, `config_snapshot/`.

## Scoring Model (how a topic is chosen)

A single, direction-aware, deterministic composition (`src/pde/scoring/assemble.py`):

- **Deficit, not distance.** Each metric's `direction` decides which way is "bad." Only
  underperformance vs benchmark scores; a strength scores ~0 — the engine never recommends coaching
  something an agent is already good at. Deficits are normalized by the benchmark for comparability.
- **Axes:** `score_level` (deficit magnitude), `score_trend` (worsening), `score_confidence`
  (window coverage), `score_risk = level × (1 − confidence)`.
- **Composition:** `score_total = w_level·level + w_trend·trend + w_risk·risk`; prioritization scales
  by the **versioned** business weight for the metric's category. Only metrics flagged
  `eligible_for_prioritization` can drive a recommendation.

## Recency Dampening

To prevent coaching whiplash, a topic coached recently is dampened. Coaching history from
`l2_asurion_coachdb_coachdb_helixcoaching` → `coaching_history.csv` is mapped to topics via
`coaching_history_map.yaml`. A candidate coached within `dampening.periods` weeks is scaled down
(`multiply`) or removed (`suppress`). No coaching history present → no-op.

## Coaching Themes & Break-Glass Selection

Above single-metric selection sits a **three-tier** selection layer (`src/pde/engine/select.py`),
still emitting **exactly one recommendation per agent** (its `tier` is recorded on the rec + receipt):

1. **Break-glass single (override)** — only metrics with a `break_glass` block are eligible. Over the
   latest `recency_weeks`, an agent trips when it is in the worst `worst_pct`% of its ICP_Client ×
   metric cohort **and** below benchmark. Guarantees the worst performers on a critical metric get
   coached on it specifically, only when the deficiency is deep and recent.
2. **Theme** — `themes.yaml` maps a theme to member metrics + a `conversation_type`. A theme qualifies
   when ≥ `count_fraction` of members are deficient (evidence-gated **and** `score_level ≥ floor`).
   Highest combined score wins. Themes are **human-curated** (SME-added).
3. **Single (fallback)** — the deterministic single-behavior argmax when no theme qualifies and no
   break-glass trips.

With no `themes.yaml` and no `break_glass` flags, tiers 1–2 are inert (identical to the pre-theme
engine plus an additive `tier` column).

## Signal Gating & Abstention

Two mechanisms keep recommendations trustworthy: **evidence gating** at the front and an
**abstention floor** at the end.

- **Production evidence gating** (`signal_thresholds.yaml`, `mode: production`) — fail-closed on
  evidence quality: require a reference point, minimum confidence, minimum denominator (per category,
  with `by_metric` overrides). `require_bad_magnitude` stays off — "is the deficit big enough" is
  decided once, downstream, by the abstention floor.
- **Abstention** (`src/pde/engine/abstain.py`) — since scoring is deficit-only, a well-performing agent
  would otherwise get their least-bad topic. Abstention withholds a rec when unwarranted and **records
  why** (`below_coaching_floor` | `no_qualified_signal`). Every coachable agent ends in exactly one of
  *recommended* or *abstained* — never a silent gap. Surfaced in `abstentions.csv`, the receipts, and a
  dashboard "No recommendation" section.

## Discovering Themes / Recalculating Benchmarks (propose-only)

Two governed, **propose-only** modules re-derive curated artifacts and never write them automatically:

- **Theme discovery** (`pde.cli.discover_themes`) finds metrics that move together across the
  population and proposes candidate themes (per-candidate `PROPOSE`/`HOLD`/`SKIPPED`), with a
  dashboard + `proposed_themes.yaml`. A theme enters the engine only when a human SME merges it.
- **Benchmark recalculation** (`pde.cli.recalc_benchmarks`, or ask to "recalculate benchmarks")
  re-derives candidate benchmarks on the engine's scoring grain, gated through guardrails
  (`PROPOSE`/`HOLD`/`UNCHANGED`/`SKIPPED`), with a dashboard + `proposed_benchmarks.yaml`. Applying is
  a separate, authorized step (`--apply --approver "<name>"`) that makes value-only edits and appends a
  governance changelog entry.

## Decision Receipts

Every decision carries: why this topic, why now, why not others, excluded signals (with reason codes),
config version, data snapshot ID, engine version. The receipt shape adapts to the `tier` — **single**
(one driver + competitors), **theme** (member drivers + `theme_membership`), **break_glass** (tripped
metric + cohort percentile), **abstained** (`recommended_topic: null` + reason). Stored as JSONL.

---
---

# PART II — THE TRAINING DOMAIN

The same engine, pointed at new-hire onboarding, to answer a different question: **when a trainee
falls short, exactly which parts of their training should they retake — and are they on pace to
graduate?** This is *remediation routing*, produced as a per-expert plan plus a data-backed dashboard.

## The program

`configs/training/training_program.yaml` describes the **ASCEND Launchpad** onboarding as an ordered
sequence of **learning blocks**, each with the skills it develops, an end-of-block gate (a test call),
and an `expected_completion_day` (a 3-week pacing schedule). This is the structure remediation and
pacing are measured against.

## Remediation model

Three properties, all governed by `configs/training/remediation.yaml` (tune without code changes):

- **Block-gated** — a block ends with a test call; falling short of the acceptable mark triggers
  remediation for that block (retake the block's skill simulations for the deficient skills, then the
  test call, before advancing).
- **Steerable** — *how far back* (this block / back N / to the block that first taught a weak skill)
  and *what* (which modalities, deficient-skills-only vs whole-block) are config knobs.
- **Once-only** — a trainee is not sent back to material they have already remediated (remediation
  history is subtracted, so plans never loop).

Engine: `src/pde/training/program.py` (`plan_remediation`, gate evaluation, pacing). A remediation plan
is framed as three role-split action groups: **Learning** (expert), **Training support** (trainer),
**Coaching** (coach).

## Day grain, and the class roster as source of truth

Training progresses day-by-day, so the domain runs on a **daily grain** — a config-only change (the
windowing selects the last N distinct `period` values; binding `period` to the event day makes the
whole pipeline day-grained). The **class roster** (`docs/training/training_class_roster.xlsx`, loaded
by `src/pde/training/roster.py`) is the source of truth for **who** appears, their **class**, **trainer**,
and **start date** (the day the training-timeline count begins). Progress is based on **actual block
completion** (from a progress feed) — never inferred from the schedule; the expected date is used only
to judge on-track vs behind.

## Training data sources

Landed in `data/raw/adhoc/latest/` by one extract run (`extract_training_assist.yaml`):

- **`training_assist`** — TrAIning Assist simulated-conversation skill pass-rates (day grain).
- **`training_cbt`** — computer-based-training completion + scores from Workday Learning (the CBT
  `score` *is* the assessment).
- **`coaching_history`** — reuses the coaching domain's (unbounded) extract; the dashboard queries it
  for **only the class-roster cohort** to show each trainee's recent coaching history.

## Running the training pipeline

```bash
python extraction/scripts/run_extract.py --config extraction/configs/extract_training_assist.yaml
python -m pde.cli.build_training_assist_skills --raw-dir data/raw/adhoc/latest
python -m pde.cli.run_training_pipeline --raw-dir data/raw/adhoc/latest --out-dir outputs/training_runs/<date>
```

It runs the shared engine over `configs/training`, then builds the training dashboard. Governance for
the program structure has its own checker: `python -m pde.cli.check_training_program`.

## The training dashboard (`training_dashboard.{html,json}`)

A self-contained, shareable HTML with a per-expert modal. For each trainee it shows:

- **Program summary tiles** that recompute live from filters (class ID, status), including time-progress.
- **Current learning block** as "Block N — short description," with **on-track vs the expected
  completion day** and a pace signal (ahead / on track / behind).
- A **re-training block** — the remediation plan (go back to Block N; focus behaviors; Learning /
  Training-support / Coaching actions) — or, for a graduate, a **completion hand-off report** (readiness
  summary + watch-in-production skills + a coach hand-off note).
- **Recent coaching history** (Type / Topic / Date / Status) for the trainee, bounded to the roster —
  the same block used on the coaching expert dashboard; hidden when there is none.
- **Learning blocks & skills** — skills rolled up under the block that develops them; locked/not-yet-
  reached blocks stay blank.
- A per-expert **Copy / Email / PDF** toolbar so a trainer can hand a full summary to a coach.

**Statuses**: `not_started` (on the roster, no data / no progress feed) · `in_training` (started,
awaiting skill signal) · `retraining` (a below-mark skill) · `on_track` / `behind` (vs expected) ·
`completed` (cleared all blocks).

## Launch context

**ASCEND launches 2026-09-14.** Pre-launch, trainees run legacy simulations, so the new program's
per-block components (keyed by forward-looking simulation personas) don't match live data yet — an
expected pre-launch state, not a defect. The crosswalk resolves automatically once cohorts run the new
simulations. Design/status detail: `docs/training/training_decision_engine.md` and
`docs/training/NEXT_STEPS.md`.

---
---

# Governance Model

- Engineering owns engine behavior; Ops Leadership owns priorities; Analytics supports validation and
  controlled discovery.
- Configuration changes are versioned and auditable; each domain has its own governed config set and
  its own integrity checker (`check_config` for coaching, `check_training_program` for training).
- Benchmarks and themes are **proposed** by guardrail-gated modules and changed only with explicit,
  logged human authorization.
- Ungoverned overrides are considered system failure.

---

# Current System Capabilities

- One deterministic, explainable decision core serving **two domains** (coaching + training) from
  isolated, governed config sets.
- Versioned SQL extraction; immutable raw snapshots; deterministic signal computation.
- Production evidence gating (reference point + confidence + denominator) and an abstention floor with
  explicit, explained non-recommendations.
- Versioned priority weighting; deterministic selection; explainable receipts.
- **Coaching:** three-tier selection (break-glass → theme → single); recency dampening; guardrail-gated,
  propose-only benchmark recalculation and theme discovery; interactive expert dashboard.
- **Training:** day-grain pipeline; block-gated, steerable, once-only remediation routing; class-roster
  source of truth; pacing vs expected schedule; learning-block + skill dashboard with completion
  hand-off, recent-coaching-history, and share/print.
- Snapshot reproducibility across both domains.

---

# Guiding Principle

The system should not slow leaders down.
It should make fast decisions work on purpose — the same way, whether it is coaching a veteran or
graduating a new hire.
