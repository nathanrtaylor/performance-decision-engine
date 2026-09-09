"""Training program structure + deterministic remediation logic (Phase 3).

Loads ``configs/training/training_program.yaml`` (the learning-block routing table)
and ``configs/training/remediation.yaml`` (the steerable policy), and provides the
remediation engine the training pipeline consumes:

  - ``load_program`` / ``load_policy`` -- parse YAML into typed structures.
  - ``build_skill_routing``           -- skill -> the blocks (in order) that develop it.
  - ``program_coverage``              -- report defined-vs-TODO (gates, components, skill maps).
  - ``evaluate_block_gates``          -- per-block gate pass / fail / unknown from test-call scores.
  - ``behind_schedule_blocks``        -- blocks past their expected_completion_day, not yet cleared.
  - ``plan_remediation``              -- deficient skills + failed gates + policy + history
                                         -> a per-expert ``RemediationPlan`` (what to retake, why).

Design notes
------------
* **Partial program is a first-class state.** A block may have no gate test-call and
  no enumerated components yet (both ``TODO`` in the seed). Gates without a test-call
  resolve to ``unknown``; blocks without components fall back to a *block-level* retake
  target ("revisit block X"), so the engine produces useful output before the structure
  is complete. ``program_coverage`` reports exactly what is still missing.
* **Pure and deterministic.** No I/O beyond the two ``load_*`` helpers; unit-tested in
  ``tests/test_training_program.py``. Wired into a training pipeline in Phase 4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import ceil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from pde.utils.io import load_yaml
from pde.utils.logging import get_logger

log = get_logger(__name__)

# A value of this string (or a missing value) marks a field as not-yet-defined.
_TODO = "TODO"


def _clean(v: Any) -> Optional[Any]:
    """Normalize an unset/placeholder config value to None."""
    if v is None:
        return None
    if isinstance(v, str) and v.strip().upper() == _TODO:
        return None
    return v


def cbt_metric_key(courseid: Any) -> str:
    """Live CBT metric key for a course id: ``cbt_`` + the normalized id.

    THE single source of this naming convention. The CBT<->block/metric join relies on
    the key produced here matching everywhere it is built -- gen_training_configs.py
    (metric catalog), gen_program_components.py (component ``ref``), and
    run_training_pipeline.py (dashboard nesting). Keeping it in one place stops those
    three from silently diverging (a divergence yields block_num = NaN with no error).
    """
    return "cbt_" + re.sub(r"[^0-9A-Za-z]+", "_", str(courseid)).strip("_").lower()


# --------------------------------------------------------------------------- #
# Typed program structure
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Component:
    kind: str                      # skill_sim | cbt | test_call
    ref: Optional[str]             # challenge_id / courseid (None while TODO)
    develops: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Block:
    id: str
    order: int
    label: str
    develops: List[str] = field(default_factory=list)        # block-level skill mapping
    components: List[Component] = field(default_factory=list)
    gate_test_call: Optional[str] = None
    gate_pass_mark: float = 0.90
    expected_completion_day: Optional[int] = None
    estimated_hours: Optional[float] = None
    assessment_methods: List[str] = field(default_factory=list)
    total_simulations: Optional[int] = None

    @property
    def develops_all(self) -> List[str]:
        """Skills developed by the block: block-level plus every component's."""
        seen: List[str] = list(self.develops)
        for c in self.components:
            for s in c.develops:
                if s not in seen:
                    seen.append(s)
        return seen

    @property
    def has_gate(self) -> bool:
        return self.gate_test_call is not None


@dataclass(frozen=True)
class Program:
    name: str
    blocks: List[Block]                       # sorted by order
    pace_hours_per_day: float = 6.0
    default_gate_pass_mark: float = 0.90

    @property
    def by_id(self) -> Dict[str, Block]:
        return {b.id: b for b in self.blocks}

    def block_order(self, block_id: str) -> Optional[int]:
        b = self.by_id.get(block_id)
        return b.order if b else None


@dataclass
class RemediationPolicy:
    gate_pass_mark: float = 0.90
    how_far_back: str = "current_block"        # current_block | back_n | to_skill_origin
    back_n: int = 1
    include_modalities: List[str] = field(default_factory=lambda: ["skill_sim", "test_call"])
    skills: str = "deficient_only"             # deficient_only | whole_block
    once_only: bool = True
    behind_schedule_enabled: bool = True
    behind_schedule_grace_days: int = 0


@dataclass(frozen=True)
class RemediationTarget:
    block_id: str
    kind: str                    # skill_sim | cbt | test_call | block  ('block' = whole-block fallback)
    ref: Optional[str]           # component ref, or the block id for the fallback
    reason_skills: List[str]     # the deficient skills that put this on the plan


@dataclass
class RemediationPlan:
    agent_id: str
    triggered_blocks: List[str] = field(default_factory=list)      # blocks whose gate failed / implicated
    targets: List[RemediationTarget] = field(default_factory=list)
    gate_to_clear: List[str] = field(default_factory=list)         # block ids whose test call must be re-passed
    behind_schedule: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.targets and not self.behind_schedule


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_program(path: str | Path) -> Program:
    raw = load_yaml(Path(path)) or {}
    prog = raw.get("program", raw) or {}
    default_pm = float(((prog.get("defaults") or {}).get("gate_pass_mark")) or 0.90)
    pace = float(((prog.get("pace") or {}).get("hours_per_day")) or 6.0)

    blocks: List[Block] = []
    for b in (prog.get("blocks") or []):
        gate = b.get("gate") or {}
        comps = []
        for c in (b.get("components") or []):
            comps.append(Component(
                kind=str(c.get("kind") or "").strip(),
                ref=_clean(c.get("ref")),
                develops=list(c.get("develops") or []),
            ))
        blocks.append(Block(
            id=str(b["id"]),
            order=int(b["order"]),
            label=str(b.get("label") or b["id"]),
            develops=list(b.get("develops") or []),
            components=comps,
            gate_test_call=_clean(gate.get("test_call")),
            gate_pass_mark=float(gate.get("pass_mark") or default_pm),
            expected_completion_day=_clean(b.get("expected_completion_day")),
            estimated_hours=_clean(b.get("estimated_hours")),
            assessment_methods=list(b.get("assessment_methods") or []),
            total_simulations=_clean(b.get("total_simulations")),
        ))
    blocks.sort(key=lambda x: x.order)
    return Program(name=str(prog.get("name") or "training program"),
                   blocks=blocks, pace_hours_per_day=pace, default_gate_pass_mark=default_pm)


def load_policy(path: str | Path) -> RemediationPolicy:
    raw = load_yaml(Path(path)) or {}
    r = raw.get("remediation", raw) or {}
    scope = r.get("scope") or {}
    bs = r.get("behind_schedule") or {}
    return RemediationPolicy(
        gate_pass_mark=float(r.get("gate_pass_mark") or 0.90),
        how_far_back=str(scope.get("how_far_back") or "current_block"),
        back_n=int(scope.get("back_n") or 1),
        include_modalities=list(scope.get("include_modalities") or ["skill_sim", "test_call"]),
        skills=str(scope.get("skills") or "deficient_only"),
        once_only=bool(r.get("once_only", True)),
        behind_schedule_enabled=bool(bs.get("enabled", True)),
        behind_schedule_grace_days=int(bs.get("grace_days") or 0),
    )


# --------------------------------------------------------------------------- #
# Routing + coverage
# --------------------------------------------------------------------------- #
def build_skill_routing(program: Program) -> Dict[str, List[str]]:
    """skill_id -> [block_id, ...] in program order (blocks that develop the skill)."""
    routing: Dict[str, List[str]] = {}
    for b in program.blocks:                       # already order-sorted
        for skill in b.develops_all:
            routing.setdefault(skill, [])
            if b.id not in routing[skill]:
                routing[skill].append(b.id)
    return routing


def program_coverage(program: Program, catalog_skills: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Report which parts of the program are defined vs still TODO."""
    catalog_skills = set(catalog_skills or set())
    routing = build_skill_routing(program)
    mapped = set(routing.keys())

    blocks_missing_gate = [b.id for b in program.blocks if not b.has_gate]
    blocks_missing_components = [b.id for b in program.blocks if not b.components]
    blocks_missing_expected_day = [b.id for b in program.blocks if b.expected_completion_day is None]
    develops_unknown = sorted(mapped - catalog_skills) if catalog_skills else []
    unmapped_catalog_skills = sorted(catalog_skills - mapped) if catalog_skills else []

    return {
        "n_blocks": len(program.blocks),
        "blocks_missing_gate": blocks_missing_gate,
        "blocks_missing_components": blocks_missing_components,
        "blocks_missing_expected_day": blocks_missing_expected_day,
        "develops_unknown": develops_unknown,
        "unmapped_catalog_skills": unmapped_catalog_skills,
        "n_skills_mapped": len(mapped),
    }


# --------------------------------------------------------------------------- #
# Gate evaluation + pacing
# --------------------------------------------------------------------------- #
def evaluate_block_gates(
    program: Program, test_call_scores: Dict[str, float]
) -> Dict[str, str]:
    """Per block: 'passed' | 'failed' | 'unknown'.

    ``test_call_scores`` maps a gate's ``test_call`` ref (or the block id) to the
    expert's latest test-call pass-rate (0-1). A block with no gate test-call, or
    with no score available, resolves to 'unknown'.
    """
    out: Dict[str, str] = {}
    for b in program.blocks:
        if not b.has_gate:
            out[b.id] = "unknown"
            continue
        score = test_call_scores.get(b.gate_test_call)
        if score is None:
            score = test_call_scores.get(b.id)
        if score is None:
            out[b.id] = "unknown"
        else:
            out[b.id] = "passed" if float(score) >= b.gate_pass_mark else "failed"
    return out


def expected_completion_day(program: Program, block_id: str) -> Optional[int]:
    """Explicit expected_completion_day, else derived from cumulative estimated_hours."""
    b = program.by_id.get(block_id)
    if b is None:
        return None
    if b.expected_completion_day is not None:
        return int(b.expected_completion_day)
    # derive from cumulative hours at the program pace
    cum = 0.0
    for blk in program.blocks:
        if blk.estimated_hours:
            cum += float(blk.estimated_hours)
        if blk.id == block_id:
            return ceil(cum / program.pace_hours_per_day) if program.pace_hours_per_day else None
    return None


def behind_schedule_blocks(
    program: Program,
    days_since_start: int,
    completed_block_ids: Optional[Set[str]] = None,
    grace_days: int = 0,
) -> List[str]:
    """Blocks the expert should have completed by now (per expected_completion_day) but hasn't."""
    completed = set(completed_block_ids or set())
    out: List[str] = []
    for b in program.blocks:
        if b.id in completed:
            continue
        ecd = expected_completion_day(program, b.id)
        if ecd is not None and days_since_start > ecd + grace_days:
            out.append(b.id)
    return out


# --------------------------------------------------------------------------- #
# Remediation mapping
# --------------------------------------------------------------------------- #
def blocks_implicated_by_skills(program: Program, deficient_skills: Set[str]) -> List[str]:
    """Blocks that develop at least one deficient skill (fallback trigger when gates are unknown)."""
    routing = build_skill_routing(program)
    hit: List[str] = []
    for skill in deficient_skills:
        for bid in routing.get(skill, []):
            if bid not in hit:
                hit.append(bid)
    # return in program order
    order = {b.id: b.order for b in program.blocks}
    return sorted(hit, key=lambda bid: order.get(bid, 0))


def _scope_blocks(program: Program, block: Block, policy: RemediationPolicy,
                  deficient_skills: Set[str]) -> List[Block]:
    """Which blocks a failure in `block` pulls into remediation, per how_far_back."""
    if policy.how_far_back == "current_block":
        return [block]
    if policy.how_far_back == "back_n":
        lo = block.order - max(0, policy.back_n)
        return [b for b in program.blocks if lo <= b.order <= block.order]
    if policy.how_far_back == "to_skill_origin":
        routing = build_skill_routing(program)
        ids = {block.id}
        for skill in (set(block.develops_all) & deficient_skills):
            origin = routing.get(skill) or []
            if origin:
                ids.add(origin[0])                 # earliest block teaching it
        return [b for b in program.blocks if b.id in ids]
    log.warning("training remediation: unknown how_far_back=%r; defaulting to current_block", policy.how_far_back)
    return [block]


def _targets_for_block(block: Block, policy: RemediationPolicy,
                       deficient_skills: Set[str]) -> List[RemediationTarget]:
    """Retake targets within one in-scope block, per the skills + modality policy."""
    want_modalities = set(policy.include_modalities)
    targets: List[RemediationTarget] = []

    if not block.components:
        # Partial program: no enumerated components -> a block-level "revisit" target.
        reason = sorted(set(block.develops_all) & deficient_skills) or sorted(block.develops_all)
        targets.append(RemediationTarget(block_id=block.id, kind="block", ref=block.id, reason_skills=reason))
    else:
        for c in block.components:
            if c.kind not in want_modalities:
                continue
            overlap = sorted(set(c.develops) & deficient_skills)
            if policy.skills == "deficient_only" and not overlap:
                continue
            targets.append(RemediationTarget(
                block_id=block.id, kind=c.kind, ref=c.ref,
                reason_skills=overlap or sorted(c.develops),
            ))

    # The end-of-block test call is retaken to clear the gate (if modality included).
    if "test_call" in want_modalities and block.has_gate:
        already = any(t.kind == "test_call" for t in targets)
        if not already:
            targets.append(RemediationTarget(
                block_id=block.id, kind="test_call", ref=block.gate_test_call,
                reason_skills=sorted(set(block.develops_all) & deficient_skills),
            ))
    return targets


def plan_remediation(
    agent_id: str,
    program: Program,
    policy: RemediationPolicy,
    deficient_skills: Sequence[str],
    *,
    triggered_blocks: Optional[Sequence[str]] = None,
    history: Optional[Set[str]] = None,
    days_since_start: Optional[int] = None,
    completed_block_ids: Optional[Set[str]] = None,
) -> RemediationPlan:
    """Build a per-expert remediation plan.

    Parameters
    ----------
    deficient_skills : skills the expert is materially deficient on (from scoring).
    triggered_blocks : blocks whose gate failed (from ``evaluate_block_gates``). When
        omitted/empty, falls back to blocks implicated by the deficient skills (used
        while gate test-calls are still TODO), and a note records the fallback.
    history : ids of already-remediated blocks/components to exclude (once_only).
    days_since_start / completed_block_ids : enable the behind-schedule flag.
    """
    deficient = set(deficient_skills)
    history = set(history or set())
    plan = RemediationPlan(agent_id=str(agent_id))

    trig = list(triggered_blocks or [])
    if not trig:
        trig = blocks_implicated_by_skills(program, deficient)
        if trig:
            plan.notes.append("triggered by deficient skills (no gate test-call results available)")
    plan.triggered_blocks = trig

    order = {b.id: b.order for b in program.blocks}
    seen_keys: Set[tuple] = set()
    for bid in trig:
        block = program.by_id.get(bid)
        if block is None:
            continue
        for s in _scope_blocks(program, block, policy, deficient):
            for t in _targets_for_block(s, policy, deficient):
                # once-only: skip material already remediated (by component ref or block id)
                if policy.once_only and (
                    (t.ref is not None and t.ref in history) or t.block_id in history
                ):
                    continue
                key = (t.block_id, t.kind, t.ref)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                plan.targets.append(t)
            if s.has_gate and s.id not in plan.gate_to_clear:
                plan.gate_to_clear.append(s.id)

    plan.targets.sort(key=lambda t: (order.get(t.block_id, 0), t.kind, t.ref or ""))
    plan.gate_to_clear.sort(key=lambda bid: order.get(bid, 0))

    if policy.behind_schedule_enabled and days_since_start is not None:
        plan.behind_schedule = behind_schedule_blocks(
            program, days_since_start, completed_block_ids, policy.behind_schedule_grace_days
        )
    return plan
