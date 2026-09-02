# Training class roster — format & location

The class roster is the **source of truth** for who appears in the training dashboard: which
experts, their class, trainer, and training start date (the day the timeline count begins).

## Where the live file goes

```
data/training/training_class_roster.xlsx
```

`data/training/` is **gitignored** — the roster carries PII (names, employee IDs) and is
refreshed for each run, so it is never committed. For a daily run, drop the latest export at
that exact path and re-run; nothing else changes. It is **not** placed under
`data/raw/adhoc/latest/` because the extract rewrites that folder on every run.

Override the path per run with `--roster <path>` on `run_training_pipeline` /
`gen_training_review_dashboard` if you keep it elsewhere (e.g. a shared drive).

## Expected shape

- **Workbook sheet:** `Expert Roster` (other sheets, e.g. a `Summary`, are ignored).
- **Columns** (loaded by `src/pde/training/roster.py::load_class_roster`):

| Column in the export | Maps to | Notes |
|---|---|---|
| `Expert Name` | `agent_name` | |
| `EEID` | `agent_id` | Employee id; joins to the skill/coaching data. Normalized to a clean string (e.g. `725820`). |
| `Trainer Name` | `trainer` | |
| `Start Date` | `training_start_date` | Parsed to `YYYY-MM-DD`. |
| `Class ID` | `class_id` | Current header. `Session #` is still accepted for older exports. |

`Class Type` was dropped from the export and is optional (ignored if present). Rows without an
`EEID` are dropped; duplicate `EEID`s are de-duplicated to one row per expert.
