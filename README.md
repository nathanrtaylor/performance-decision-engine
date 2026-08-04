# Coaching Decision Engine

Deterministic, explainable system for recommending the most appropriate
coaching topic and conversation type for a given coaching session.

This engine translates operational performance data into governed
coaching recommendations that are:

- Business-aligned
- Deterministic
- Versioned
- Auditable
- Explainable

It is designed to absorb frequent priority changes without losing rigor
or credibility.

---

# Architecture Overview

The system follows a layered, modular architecture:

Presto / Source Systems  
        ↓  
Extraction Layer (Versioned Raw Snapshots)  
        ↓  
Signal Construction  
        ↓  
Signal Gating (Thresholds)  
        ↓  
Multi-Axis Scoring  
        ↓  
Prioritization (Versioned Weights)  
        ↓  
Deterministic Topic Selection  
        ↓  
Theme / Break-Glass Selection (three-tier)  
        ↓  
Decision Receipts  

## Core Design Principles

- Business outcomes beat behavioral purity
- Determinism is a feature
- Explainability is mandatory
- Flexibility must be governed
- Configuration lives inside the system
- Learning is constrained and human-controlled

---

# Repository Structure

```
configs/
  active.yaml
  mappings/
    source_catalog.yaml
    metric_catalog.yaml
    topic_map.yaml
    benchmarks.yaml
    themes.yaml
  thresholds/
    signal_thresholds.yaml
  priorities/
    vYYYY_MM_DD_*.yaml

data/
  raw/
    weekly/<snapshot_id>/      # Immutable raw snapshots
    weekly/latest/             # Auto-maintained pointer
  samples/

extraction/
  sql/
  configs/
  scripts/

src/cde/
  signals/
  scoring/
  prioritization/
  engine/
  simulation/
  governance/
  benchmarks_recalc/          # guardrail-gated benchmark recalculation (propose-only)
  themes_discovery/           # guardrail-gated theme discovery (propose-only)
  cli/

outputs/
  runs/<timestamp>/
  benchmark_recalc/<id>/      # recalc dashboard + proposed change-set (not a pipeline run)
  theme_discovery/<id>/       # discovery dashboard + proposed themes (not a pipeline run)
```

The `engine/` package holds the three-tier selection layer: `select.py` (orchestrator),
`break_glass.py` (Tier 1 override), `themes.py` (Tier 2 themes), and `recommend.py` (Tier 3
single-behavior argmax, unchanged).

---

# Configuration Layers

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
| Active pointer | active.yaml | Selects current config set; may include `data_snapshot` (raw path resolution and default `required_tables` from `expected_sources`) |

Only `priorities/` and `active.yaml` should change frequently.

`conversation_types.by_topic` in `active.yaml` overrides `topic_map.topic_to_conversation_type` for matching topic strings; otherwise the topic map supplies defaults.

---

# Data Model

All sources are tall-skinny tables at this grain:

agent_id × period × call_type × metric

Required columns (canonical form):

- agent_id
- period (week-ending date)
- call_type
- metric
- numerator
- denominator
- calc

Raw snapshots are stored at:

```
data/raw/weekly/<snapshot_id>/
```

Snapshots are immutable.

The `latest/` folder is automatically maintained by the extraction pipeline.

---

# Extraction Layer

The extraction layer:

- Compiles parameterized SQL
- Executes via SQLAlchemy
- Writes versioned raw CSV snapshots
- Maintains a `latest/` pointer
- Produces a manifest for auditability

## Output Location

```
data/raw/weekly/
  <run_id>/
  latest/
```

## Running Extraction

From repo root:

```bash
python .\extraction\scripts\run_extract.py `
  --config extraction/configs/extract_run.yaml
```

This will:

- Compile SQL templates
- Execute queries
- Write CSVs into `data/raw/weekly/<run_id>/`
- Update `data/raw/weekly/latest/`

For `agent_metrics`, the metric list in SQL is filled from `configs/mappings/metric_catalog.yaml` when `metrics_from_catalog` is set in the extraction YAML (so extraction stays aligned with the decision catalog).

Optional: compile only (for debugging):

```bash
python .\extraction\scripts\compile_sql.py `
  --config extraction/configs/extract_run.yaml
```

---

# Running the Decision Pipeline

After extraction completes:

```bash
python -m cde.cli.run_pipeline `
  --out-dir outputs/runs/2026-03-03_TEST `
  --configs-dir configs
```

`--raw-dir` is optional when `data_snapshot` is set in `configs/active.yaml`: with `mode: latest` the engine reads `<root>/latest`; with `mode: explicit` it uses `<root>/<snapshot_id>`. You can still pass `--raw-dir` to override.

## Outputs

- recommendations.csv (one row per agent; `tier` column = break_glass | theme | single)
- decision_receipts.jsonl
- excluded_signals.csv
- scores_windowed.csv (primary score table used for topic candidates)
- scores_windowed_raw.csv (raw 8-week aggregates before scoring; diagnostic)
- eligible_signals.csv
- signals.csv (all built signals before gating; diagnostic)
- topic_candidates.csv (per-agent topic candidates after weighting + dampening; diagnostic)
- dashboard.html (self-contained run summary: recs by tier, recs by topic, splits by icp_client/mascot, metric warning signs)
- manifest.json
- config_snapshot/

Optional: add `--write-point-in-time-scores` to also write `scores.csv` (per-period scores before windowing; useful for diagnostics).

## Scoring Model (how a topic is chosen)

Scoring is a single, direction-aware, deterministic composition (in `src/cde/scoring/assemble.py`):

- **Deficit, not distance.** Each metric's `direction` (from `metric_catalog.yaml`) decides which way
  is "bad". Only underperformance vs benchmark scores; a strength scores ~0, so the engine never
  recommends coaching something an agent is already good at. Deficits are normalized by the benchmark
  so metrics on different scales are comparable.
- **Axes:** `score_level` (deficit magnitude), `score_trend` (worsening over the window),
  `score_confidence` (window coverage), `score_risk = level x (1 - confidence)`.
- **Composition:** `score_total = w_level*level + w_trend*trend + w_risk*risk` using `priority_model`
  weights in `active.yaml`. Prioritization then scales this by the **versioned** business weight for the
  metric's category (`priorities/*.yaml`). Only metrics flagged `eligible_for_prioritization` can drive
  a recommendation.

## Recency Dampening

To prevent coaching whiplash, a topic coached recently is dampened. Coaching history is extracted from
`l2_asurion_coachdb_coachdb_helixcoaching` into `coaching_history.csv` (optional input) and mapped to
engine topics via the governed crosswalk `configs/mappings/coaching_history_map.yaml`
(`behavior_selected -> topic`). A candidate is dampened when the same agent+topic was coached within
`dampening.periods` weeks of the decision period. With `dampening.mode: multiply` the topic's
`priority_score` is scaled by `dampening.multiplier` (kept in contention); with `suppress` it is removed.
If no `coaching_history.csv` is present, dampening is a no-op.

---

# Coaching Themes & Break-Glass Selection

Above single-metric selection sits a **three-tier** selection layer (`src/cde/engine/select.py`).
It still emits **exactly one recommendation per agent**, but that recommendation can now be a
*theme* (a pattern across several behaviors) or a *break-glass* single (a critical override), not
only the single best behavior. The tier is recorded on each recommendation (`tier` column) and in
the receipt.

Precedence, per agent (period, call_type):

1. **Break-glass single (override)** — Only metrics carrying a `break_glass` block in
   `metric_catalog.yaml` are eligible. Over the **latest `break_glass.recency_weeks` weeks** (a short
   recency window — not the 8-week decision window), an agent trips break-glass when it is in the
   **worst `worst_pct`% of its ICP_Client × metric cohort** (raw cohort percentile on the
   direction-adjusted "bad" axis) **and** is below benchmark. A tripped metric overrides any theme.
   This guarantees the worst performers on a truly critical metric get coached on it specifically,
   only when the deficiency is both deep and recent. Computed from `eligible_signals` (the only frame
   carrying the ICP_Client cohort).
2. **Theme** — `configs/mappings/themes.yaml` maps a theme name to its member metrics and a
   `conversation_type`. A theme **qualifies** for an agent when at least `theme_selection.count_fraction`
   (default 0.5, i.e. ≥50%) of its members are *deficient*, where deficient = evidence-gated (already
   enforced upstream) **and** `score_level ≥ theme_selection.score_level_floor`. That floor is
   deliberately looser than the solo-coaching bar: a metric not worth coaching on its own can still
   count toward a pattern. Among qualifying themes, the highest combined score
   (`theme_selection.aggregate` = mean|sum of member scores) wins.
3. **Single (fallback)** — today's deterministic single-behavior argmax
   (`recommend_for_population`), used when no theme qualifies and no break-glass trips. Unchanged.

**Backward compatible:** with no `themes.yaml` and no `break_glass` flags configured, tiers 1 and 2
are inert and every agent falls through to the single-behavior result — identical to the pre-theme
engine (plus an additive `tier` column).

Configuration:

```yaml
# configs/active.yaml
theme_selection:
  count_fraction: 0.5      # >= this fraction of a theme's members must be deficient to qualify
  score_level_floor: 0.15  # single low global "deficient" floor (looser than the solo bar)
  aggregate: mean          # mean | sum: how member scores combine into the theme score
break_glass:
  recency_weeks: 2         # latest-weeks slice for the override (short recency window)
  worst_pct: 10            # default worst-percent cohort tail; per-metric block can override
```

```yaml
# configs/mappings/metric_catalog.yaml — per-metric override flag (curated few only)
cancel_rate:
  break_glass: { enabled: true, worst_pct: 5 }
```

Themes are **human-curated**: a theme is added to `themes.yaml` only by an SME. The discovery tool
below can *propose* candidate themes, but never writes them.

---

# Discovering Themes

`themes.yaml` is a curated artifact. The **theme discovery** module (`src/cde/themes_discovery/`)
looks for metrics that **move together** across the population and **proposes** candidate themes for
an SME to review. It runs independently of the decision pipeline (it only *reads* the same config +
extract) and is **propose-only**: it never edits `themes.yaml`.

Trigger it by running:

```bash
python -m cde.cli.discover_themes `
  --configs-dir configs `
  --out-dir outputs/theme_discovery/2026-03-03_themes
```

`--raw-dir` is optional (resolved from `data_snapshot` in `active.yaml`, like the pipeline).

## How a theme is proposed

For each ICP_Client cohort, every metric's 8-week windowed mean per agent is placed on a common
**direction-adjusted "bad" axis** (`bad = gap if lower_is_better else -gap`) so a low-is-better and a
high-is-better metric that reflect the same underlying problem show up as *positively* correlated.
Metrics are Pearson-correlated across agents within the cohort; pairs that clear the correlation and
cohort-coverage guardrails are clustered (connected components) into candidate themes. Guardrails:
sample sufficiency (`min_sample`), correlation strength (`min_correlation`), cohort coverage
(`min_cohort_coverage`), and theme-size sanity. Thresholds live in
`src/cde/themes_discovery/config.py` (`DiscoveryThresholds`). Per-candidate verdict is `PROPOSE`,
`HOLD` (weak/inconsistent), or `SKIPPED` (insufficient sample).

## Outputs (in `--out-dir`)

- `dashboard.html` — candidate themes with per-theme verdict, mean correlation, cohort coverage.
- `proposed_themes.yaml` — the PROPOSE candidates in `themes.yaml` shape (a suggestion to merge).
- `theme_diff.json`, `summary.txt` — full detail.

## Applying

There is no automatic apply: a theme enters the engine **only when a human SME merges it** into
`configs/mappings/themes.yaml` (renaming the candidate and confirming its `conversation_type`). Even
`--apply --approver "<name>"` does **not** edit `themes.yaml`; it only records a governance review
entry in `configs/governance/changelog.md` and defers every proposal for manual merge.

---

# Recalculating Benchmarks

`benchmarks.yaml` is a curated artifact. The **benchmark recalculation** module
(`src/cde/benchmarks_recalc/`) re-derives candidate benchmark values from the latest extract and
**proposes** changes only where the evidence clears guardrails. It runs independently of the decision
pipeline (it only *reads* the same config + extract) and is **propose-only**: it never edits
`benchmarks.yaml` without explicit authorization.

Trigger it by asking to "recalculate benchmarks", or run:

```bash
python -m cde.cli.recalc_benchmarks `
  --configs-dir configs `
  --out-dir outputs/benchmark_recalc/2026-03-03_recal
```

`--raw-dir` is optional (resolved from `data_snapshot` in `active.yaml`, like the pipeline).

## How a value is proposed

For each metric/cohort a candidate anchor is computed on the 8-week windowed-mean-per-agent grain (the
grain the engine scores on), then gated through guardrails. The per-metric verdict is one of:

- **PROPOSE** — cleared every guardrail and moved materially vs the current value.
- **HOLD** — evidence insufficient / degenerate / outside the observed value range; do not change.
- **UNCHANGED** — within the materiality threshold of the current value.
- **SKIPPED** — source inactive (e.g. tool-usage metrics with no data).

Anchors mirror the curated methodology: operational metrics = per-cohort median; quality/sentiment
behaviors = p25 of the windowed mean, capped at 0.95; degenerate cohort distributions keep their
absolute default; sentiment is Verizon-only and splits by cohort only when cohorts differ materially.
Guardrails: sample sufficiency, materiality, non-degeneracy, cohort-split validity, and observed-range
sanity. Thresholds live in `src/cde/benchmarks_recalc/config.py` (`RecalcThresholds`).

## Outputs (in `--out-dir`)

- `dashboard.html` — OLD vs NEW per metric/cohort, verdict, and justification.
- `proposed_benchmarks.yaml` — the change-set (PROPOSE rows only), in `benchmarks.yaml` shape.
- `benchmark_diff.json`, `summary.txt` — full detail.

## Applying (authorized)

Applying is a separate, governed step. Only after review:

```bash
python -m cde.cli.recalc_benchmarks `
  --configs-dir configs `
  --out-dir outputs/benchmark_recalc/2026-03-03_recal `
  --apply --approver "Your Name"
```

`--apply` makes value-only edits to existing keys in `configs/mappings/benchmarks.yaml` (preserving the
file's methodology comments) and appends an entry to `configs/governance/changelog.md`. New cohort
splits are **not** auto-written; they are deferred for manual merge from `proposed_benchmarks.yaml`.

---

# Decision Receipts

Every recommendation includes:

- Why this topic
- Why now
- Why not others
- Excluded signals (with reason codes)
- Config version
- Data snapshot ID
- Engine version

The receipt shape adapts to the selection `tier`:

- **single** — one driver metric + competing topics (as before).
- **theme** — the member metrics that drove it (multiple drivers) plus a `theme_membership` block
  (`n_deficient` / `n_members` / deficient metrics).
- **break_glass** — the tripped metric with `override: true`, `reason: "break_glass"`, and the
  agent's cohort percentile.

Receipts are stored as JSONL for auditability and downstream ingestion.

---

# Governance Model

- Coaching Technology owns engine behavior.
- Ops Leadership owns priorities.
- Analytics supports validation and controlled discovery.
- Configuration changes are versioned and auditable.
- Benchmark changes are *proposed* by the recalculation module and applied only with explicit
  authorization (`--apply --approver`), which records a `configs/governance/changelog.md` entry.
- Coaching themes are *proposed* by the discovery module but added to `themes.yaml` only by a human
  SME; discovery never writes themes automatically.
- Ungoverned overrides are considered system failure.

---

# Appendix A — Troubleshooting & Diagnostics

## 1. Verify Raw Snapshot Health

```bash
python -c "import pandas as pd; df=pd.read_csv('data/raw/weekly/latest/agent_metrics.csv'); print(len(df)); print(df.head())"
```

Check calc health:

```bash
python -c "import pandas as pd; df=pd.read_csv('data/raw/weekly/latest/agent_metrics.csv'); print('calc null %', df['calc'].isna().mean()); print('den==0 %', (df['denominator']==0).mean())"
```

## 2. Verify Signal Inputs

```bash
python -c "import pandas as pd; df=pd.read_csv('outputs/runs/2026-03-03_TEST/eligible_signals.csv'); print(df[['metric','call_type','value','benchmark','gap']].head(20))"
```

If benchmark is null everywhere → benchmark mapping failure.

## 3. Verify Scores Are Not All Zero

```bash
python -c "import pandas as pd; df=pd.read_csv('outputs/runs/2026-03-03_TEST/scores_windowed.csv'); print(df['score_total'].describe()); print('nonzero:', (df['score_total']!=0).sum())"
```

If nonzero == 0:

- Check benchmark lookup
- Check call_type alignment
- Check metric name casing

## 4. Check Topic Mapping Coverage

```bash
python -c "import pandas as pd, yaml; scores=pd.read_csv('outputs/runs/2026-03-03_TEST/scores_windowed.csv'); cfg=yaml.safe_load(open('configs/active.yaml',encoding='utf-8')); tm=(cfg.get('topic_map') or {}); tm=tm.get('topic_map', tm); m2t=tm.get('metric_to_topic') or {}; print('unmapped metrics:', [m for m in scores['metric'].unique() if m not in m2t])"
```

If many metrics are unmapped → recommendations will be blank.

## 5. Validate Period Alignment

```bash
python -c "import pandas as pd; df=pd.read_csv('data/raw/weekly/latest/agent_metrics.csv'); s=pd.to_datetime(df['period']); print(s.dt.day_name().value_counts())"
```

All weekly periods must align to the same weekday.

## 6. Force-Fail on All-Zero Scores (Recommended Guardrail)

Add after scoring:

```python
if scores["score_total"].max() == 0:
    raise ValueError("All score_total values are zero. Check benchmark mapping and call_type alignment.")
```

---

# Appendix B — Common Failure Modes

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| All scores = 0 | Benchmark mapping failure | Fix metric names / call_type alignment |
| Blank recommendations | Topic map missing | Update topic_map.yaml |
| Duplicate score rows | Join explosion | Fix upstream extraction grouping |
| KeyError on metric | Column renamed | Canonicalize column names |
| Trend always 0 | Period misalignment | Standardize week-ending period |

---

# Current System Capabilities

- Versioned SQL extraction
- Immutable raw snapshots
- Deterministic signal computation
- Configurable gating
- Versioned priority weighting
- Deterministic topic selection
- Three-tier selection: break-glass override → coaching theme → single behavior
- Explainable receipts (single / theme / break-glass variants)
- Snapshot reproducibility
- Guardrail-gated benchmark recalculation (propose-only, authorized apply)
- Guardrail-gated theme discovery (propose-only; themes are human-added)

---

# Guiding Principle

The system should not slow leaders down.  
It should make fast decisions work on purpose.
