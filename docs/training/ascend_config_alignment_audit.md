# ASCEND Config Alignment Audit — Work Plan

A working agenda for reconciling the training **program structure** with the **skills &
scoring** catalog — so every simulator and CBT an expert practices resolves to a learning
block and a scorable skill.

- **Files in scope:** `configs/training/training_program.yaml` ↔ `configs/training/training_profiles.yaml`
- **Snapshot:** 2026-09-08 (computed from both files)
- **Owners:** Training Content team + Analytics
- **Rich version:** `docs/training/ascend_config_alignment_audit.html` (self-contained, shareable/printable)

---

## 1. Purpose & participants

The training dashboard silently drops activity it can't attribute. A tester can run the Block 3
simulators all week and see a blank block, because the config that describes the *program* and
the config that describes *scoring* don't reference each other consistently. This audit closes
that gap with the people who own the source content.

- **Training content team** — owns simulator IDs & personas, which skills each block is meant to
  teach, and gate test-calls.
- **Analytics** — owns the skill catalog, the behavior→skill mapping, and the pipeline; applies
  decisions to YAML, regenerates configs, verifies.

**Single goal:** every simulator/CBT an expert practices → resolves to a block → rolls up into
scorable skills → shows on the dashboard.

## 2. How the two files connect

- `training_program.yaml` owns **structure** — 16 blocks, order, gates, and `components` (which
  sims/CBTs belong to each block).
- `training_profiles.yaml` owns **skills & scoring** — the skill catalog and 124 simulator
  `profiles` (which skills each sim evaluates).
- **No code reads both files together.** They meet only through a shared **skill-id** namespace.

### Three id-spaces — only one is connected

| ID-space | Example | Lives in | Runtime? |
|---|---|---|---|
| **skill-id** | `show_empathy` | profiles `skills:` + program `develops:` | **the join** |
| **challenge_id / persona** | `judy_terry` | profiles `profiles:` + program `component.persona` | ingestion-only (discarded) |
| **ASC-SIM ref** | `ASC-SIM-6J5TR3` | program `component.ref` / `gate.test_call` | unused |

> The only cross-file check today is `check_training_program.py` — it verifies every skill in a
> block's `develops:` exists in the catalog, and nothing more. It does **not** validate
> `persona`/`ref` against `challenge_id`; that gap is acknowledged, not accidental.

## 3. Current-state snapshot

**Scale:** 16 blocks · 26 skills · 124 profiles · 100 ASC-SIM refs.

**Aligned (what works):**
- 22/22 skill-ids used in block `develops:` exist in the catalog — **0 dangling**.
- 3 persona↔challenge_id matches (`judy_terry`, `douglas_beatty`, `isaac_barrows`; was 0 before this session).
- 22/26 catalog skills are developed by at least one block.

**Gaps (what the audit must close):**
- **0/100** ASC-SIM refs overlap any `challenge_id` — the core crosswalk gap.
- **90** component personas (13 blocks) are not profiles.
- **121/124** profiles are orphaned (referenced by no block component).
- **5** blocks have components but empty `develops:` (03, 04, 05, 07, 16).
- **6** gates are still `TODO`; **0** gates match a profile.
- **4** unmapped catalog skills; **1** malformed ref.

## 4. Alignment work items (walk in order)

Each item: **Issue / Decide / Source / Done-when.**

### WI-1 — Persona ↔ live challenge_id crosswalk  *(highest leverage · Content + Analytics)*
- **Issue:** program sim IDs (`ASC-SIM-…`) and personas don't match the live `challenge_id`
  (`scorecard_name` in the data). Overlap 0/100 → 90 personas can't map to a scorable profile.
- **Decide:** for each sim in scope, the live `challenge_id` for its ASC-SIM ref + persona.
- **Source:** Content team (SIM registry) + Analytics (live challenge_ids from the extract).
- **Done-when:** a committed crosswalk covers the personas in scope; overlap > 0 and rising.

### WI-2 — Block `develops:` completeness  *(blocks the Block-3 fix · Content team)*
- **Issue:** 5 blocks have components but empty `develops: []`, so they can never show skill
  progress — why Block 3 renders blank even after its personas matched.
- **Decide:** which skills each of the 16 blocks teaches — start with the 5 empties (03, 04, 05, 07, 16).
- **Source:** Content team (curriculum intent).
- **Done-when:** `check_training_program.py` shows no `develops_unknown` errors and no
  unexpectedly-empty blocks; Block 3 renders skills for a known tester.

### WI-3 — Behavior → skill mapping gaps  *(scoring coverage · Analytics + Content)*
- **Issue:** live sims emit behaviors with no skill (`scope`, `provide assurance`, `ask effective
  questions`, `keep customer informed`, `manage call efficiently`) → dropped in the rollup. 4
  catalog skills are also unmapped.
- **Decide:** per unmapped behavior — add a new skill or alias to an existing one; resolve the 4 unmapped skills.
- **Source:** Analytics (catalog) with content-team confirmation.
- **Done-when:** every behavior a scoped sim emits maps to a skill; rollup drops ~0 rows for those sims.

### WI-4 — Gate test-calls  *(Content team)*
- **Issue:** 6 blocks have `gate.test_call: TODO`; no gate maps to a scorable profile.
- **Decide:** the gate test-call per block, and whether gates score now or stay curriculum-only this pass.
- **Source:** Content team.
- **Done-when:** no `TODO` gates remain (or each is explicitly deferred with an owner).

### WI-5 — Data hygiene  *(Analytics)*
- **Issue:** Block 8 has malformed ref `ASC-ASC-SIM-3JEJ0P` (double `ASC-`, persona `reanna_jaskolski`).
- **Decide:** correct the ref; spot-check SIM ID formatting across components.
- **Source:** Analytics.
- **Done-when:** all refs match the `ASC-SIM-XXXXXX` pattern.

## 5. Audit workflow

- **Before:** circulate this doc + the appendix worksheets; content team pre-fills known
  persona→challenge_id mappings; analytics exports live challenge_ids + unmapped behaviors.
- **During:** walk WI-1 → WI-5; log every decision in a shared sheet; turn unknowns into
  actions with an owner + due date (don't block the room).
- **After:** analytics applies decisions to the YAMLs, regenerates configs, re-runs verification,
  and sends the refreshed snapshot back.

## 6. Verification & sign-off

- [ ] `python tools/gen_training_configs.py` (regenerate mappings)
- [ ] `python -m pde.cli.check_training_program` — no new `develops_unknown` errors
- [ ] `build_training_assist_skills` → `run_training_pipeline`
- [ ] A known tester's audited sim activity attaches to the intended block in `training_dashboard.json`
- [ ] **Sign-off:** ref↔challenge_id overlap, empty-develops count, and unmapped-behavior count all at target

## 7. Appendix — worksheets

### Component personas that aren't profiles (90 · 13 blocks)
- **block_04 (Client Tools):** zaria_streich, james_emard, elisha_bins, crystal_bernier, vivien_thompson, brigitte_kunze
- **block_05 (Sales Foundation):** darla_dimple, alexa_berge, bruce_gulgowski, korey_gutmann, mike_mann
- **block_06 (Greeting/Verify/Assess):** libbie_lubowitz, camryn_kihn, bennett_mante, stan_green, rebecca_brekke
- **block_07 (Troubleshooting Basics):** amanda_waller, rhett_stokes, alvis_ernser, heather_kunze, hyman_greenholt, sally_pollich, albin_boehm
- **block_08 (Device Basics):** quinten_balistreri, cara_sawayn, jenna_parisian, reanna_jaskolski, jaqueline_kassulke, helmer_ebert, della_morissette, sean_west
- **block_09 (Hardware vs Software):** nadine_stark, ansley_rutherford, nannie_considine, donny_schiller, rodrick_feest, dashawn_olson, elyssa_beer, jakayla_stroman
- **block_10 (Networking):** lana_lang, rolando_wolf, khalid_parker, marquis_windler, willow_wintheiser, chanel_muller, felix_lebsack, monroe_smitham, harvey_quitzon
- **block_11 (Activations & Onboarding):** devon_schaden, myles_will, lee_renner, alisha_lindgren, victor_hayes_west, dwight_wolf, carleton_nader, matilde_stanton, paris_weimann, emmy_carroll, virginie_mertz, lafayette_rath, desiree_thiel, conor_bailey, leda_grady
- **block_12 (Carrier Account):** arthur_curry, golda_roberts, kareem_bode, reina_haley, lia_shanahan, hattie_bahringer, robb_hermiston, hans_streich
- **block_13 (Insurance & Warranty):** aisha_campbell, hellen_klein, enoch_stoltenberg, marisa_casper
- **block_14 (Sales in Action):** jubilation_lee, rebeka_bahringer, doug_feil, brandt_terry, neil_morar, reba_veum, jeramie_stark
- **block_15 (Confirm, Recap, Close):** thomas_oliver, aron_kling, daisy_hansen, timmothy_o_hara
- **block_16 (Training Closing):** kara_danvers, leonard_doyle, lavina_wuckert, ginger_zemlak

### Blocks with components but empty `develops:` (5)
block_03 (Asurion Tools), block_04 (Client Tools), block_05 (Sales Foundation),
block_07 (Troubleshooting Basics), block_16 (Training Closing).

### Unmapped behaviors & skills (WI-3)
- **Live behaviors with no skill:** scope, provide assurance, ask effective questions, keep customer informed, manage call efficiently
- **Catalog skills flagged `unmapped`:** ask_to_enroll, recorded_disclosures_statement, resolve, understand_customer_issue

### Gate test-calls (10 set · 6 TODO)
- **TODO:** block_01, block_02, block_03, block_05, block_06, block_07
- **Have an ASC-SIM id (none maps to a profile):** block_04, block_08, block_09, block_10, block_11, block_12, block_13, block_14, block_15, block_16
