"""Remediation-phrased narratives for the training domain (Phase 4).

Turns a block-level remediation into plain-language actions, grouped into three
categories (per stakeholder-neutral framing):

  - learning_actions          -- what the EXPERT does to remediate (retake the material).
  - training_support_actions  -- how the TRAINER supports the re-try.
  - coaching_actions          -- how the COACH reinforces the weak behaviors.

Deterministic and template-based (no LLM); phrased in days/sessions, not weeks.
Component-aware: when a block has enumerated components (skill sims / CBTs) the
actions name them; otherwise they fall back to block-level phrasing.
"""
from __future__ import annotations

from typing import List, Optional, Sequence


def _join(labels: Sequence[str], max_n: int = 3) -> str:
    labels = list(labels)
    if not labels:
        return "the focus behaviors"
    shown = labels[:max_n]
    if len(labels) > max_n:
        return ", ".join(shown) + f", +{len(labels) - max_n} more"
    if len(shown) == 1:
        return shown[0]
    return ", ".join(shown[:-1]) + " and " + shown[-1]


def remediation_reason(
    block_label: str,
    focus_labels: Sequence[str],
    gate_score: Optional[float] = None,
    pass_mark: float = 0.90,
    block_num: Optional[int] = None,
) -> str:
    """One-line why-this-remediation, gate-aware when a test-call score is known.

    ``block_num`` (the learning-block number) is prepended so a trainer sees exactly
    which block to send the expert back to.
    """
    focus = _join(focus_labels)
    where = f"Block {block_num} — {block_label}" if block_num is not None else block_label
    if gate_score is not None:
        return (f"Test call for {where} scored {round(gate_score * 100)}% — below the "
                f"{round(pass_mark * 100)}% pass mark. Weakest behaviors: {focus}.")
    return f"{where} shows deficient behaviors below the {round(pass_mark * 100)}% pass mark: {focus}."


def learning_actions(block_label: str, focus_labels: Sequence[str],
                     component_labels: Optional[Sequence[str]] = None) -> List[str]:
    """What the expert does — retake the specific material, then the gate."""
    focus = _join(focus_labels)
    out: List[str] = []
    if component_labels:
        out.append(f"Redo these {block_label} components until passing: {_join(component_labels, 5)}.")
    else:
        out.append(f"Re-run the {block_label} skill-based simulations until passing (unlimited attempts).")
    out.append(f"Re-take the CBT refresher covering {focus}.")
    out.append(f"Attempt a fresh {block_label} test call once the simulations are passing.")
    return out


def training_support_actions(block_label: str, focus_labels: Sequence[str]) -> List[str]:
    """How the trainer supports the re-try."""
    focus = _join(focus_labels)
    return [
        f"Add to the next {block_label} re-try group (formed near the end of the block).",
        f"Observe a practice simulation and give targeted feedback on {focus}.",
        "Confirm CBT and simulation completion before the re-test.",
    ]


def coaching_actions(focus_labels: Sequence[str]) -> List[str]:
    """How the coach reinforces the weak behaviors."""
    labels = list(focus_labels)
    primary = labels[0] if labels else "the focus behaviors"
    secondary = labels[1] if len(labels) > 1 else primary
    return [
        f"Hold a 1:1 to reinforce {primary}.",
        f"Share a model-call example demonstrating {secondary}.",
        "Review the re-test result and update the remediation status.",
    ]


def build_action_groups(block_label: str, focus_labels: Sequence[str],
                        component_labels: Optional[Sequence[str]] = None) -> dict:
    """All three action groups, shaped for the dashboard's remediation view."""
    return {
        "learning": {
            "label": "Learning actions",
            "actor": "Expert",
            "actions": learning_actions(block_label, focus_labels, component_labels),
        },
        "training_support": {
            "label": "Training support actions",
            "actor": "Trainer",
            "actions": training_support_actions(block_label, focus_labels),
        },
        "coaching": {
            "label": "Coaching actions",
            "actor": "Coach",
            "actions": coaching_actions(focus_labels),
        },
    }
