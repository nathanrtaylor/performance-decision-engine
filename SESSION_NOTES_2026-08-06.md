# Session notes — 2026-08-06

*(Scratch/handoff note — untracked, not committed. Delete whenever.)*

## Where things stand
- **On `main`, clean working tree**, at `6437e0c`. Engine healthy: `python -m pytest` → **98 passed**.
- `data/raw/weekly/latest/` has agents, agent_metrics, behavior_scores (+ coaching_history) — so `python -m cde.cli.run_pipeline` runs immediately off the current (late-July) snapshot.

## What happened today
1. **Merged `docs/add-metric-guide` → `main`** (resolved config conflicts; `abstention.enabled: false` and the two `eligible_for_prioritization: false` flips were intentional). Pushed to `origin/main`.
2. **Built an AI root-cause / transcript-analysis layer** on branch **`ai-root-cause-analysis`** (6 commits, local only, never pushed): downstream module that pulls an expert's recent call transcripts and LLM-analyzes them for behavioral root causes, never overriding the deterministic rec. Included: `src/cde/ai_root_cause/`, CLI, mock dry-run + tests, org-gateway wiring (verified live against Sonnet/Opus via the Asurion gateway on fixture transcripts), a real transcript SQL, and cost levers (per-call Phase 2, per-phase models w/ Haiku on Phase 2, `--limit`).
3. **Shelved that route** — too costly/fragile. The warehouse transcript pull is multi-GB and long-running; it hung repeatedly and, even after fixes (50-call/expert cap, partition pruning, streamed parquet), aborted on a transient Trino worker crash after 3.8 GB. All-experts-every-run LLM cost projected into the low thousands.
4. **Wound down cleanly**: returned to `main`, deleted the 3.8 GB transcript PII pull from disk, kept the branch parked. Recorded the decision in memory (`cde-ai-root-cause-parked`).

## Resume tomorrow
- **Deterministic engine work** proceeds normally on `main`.
- Fresh data (CSV-only, fast, no transcripts): `python extraction/scripts/run_extract.py --config extraction/configs/extract_run.yaml`.
- **If the AI route is ever revisited** (branch `ai-root-cause-analysis`), the two blockers to solve first: (a) warehouse-side query resilience — cohort-chunked + retry so one worker hiccup doesn't kill a multi-GB pull; (b) analysis-side loader reads the whole transcripts parquet into memory — needs per-agent/partition reads. Also decide scope: analyzing *all* experts every run is the cost ceiling; scoping to changed/actioned recs cuts it linearly.
