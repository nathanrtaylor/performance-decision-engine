---

# AI Root Cause Analysis Layer for the Coaching Decision Engine

## Objective

Build an AI-powered analysis layer that consumes deterministic decision receipts and recent call transcripts to identify likely transcript-supported root causes, surface representative calls, and generate coach-ready explanations.

This layer **must not modify or override** the deterministic coaching recommendation.

The deterministic engine remains the policy engine.

The AI layer becomes the reasoning engine.

---

# Architecture

```
Operational Metrics
        │
        ▼
Coaching Decision Engine
        │
        ▼
Decision Receipt
        │
        ▼
Transcript Retrieval
        │
        ▼
AI Root Cause Analysis
        │
        ├── Root cause hypotheses
        ├── Supporting transcript evidence
        ├── Representative recent calls
        ├── Contradictory evidence
        ├── Coaching narrative
        └── Abstention if insufficient evidence
```

---

# Guiding Principles

The deterministic engine answers:

* What should be coached?
* Why did this topic win?
* What evidence supports the recommendation?
* Is the evidence sufficient?

The AI layer answers:

* What conversational behaviors appear to explain the recommendation?
* What recurring patterns exist across recent calls?
* What is the most likely operational root cause?
* Which calls best demonstrate those patterns?
* What coaching approach would address them?

---

# Data Inputs

## Decision Receipt

Use the existing receipt as structured context.

Relevant fields include:

* expert_id
* recommendation tier
* selected topic
* driver metrics
* competing topics
* excluded signals
* coaching history
* decision timestamp
* call type
* cohort
* confidence
* priority score

The receipt is the grounding context.

---

## Transcript Dataset

Parquet containing approximately:

* expert_id
* session_id
* timestamp
* transcript
* speaker turns
* metadata

Limit analysis to the most recent 50 calls before the decision timestamp.

---

# Processing Pipeline

## Phase 1

Deterministic Retrieval

For each receipt:

Retrieve:

* matching expert
* calls before decision date
* newest first
* maximum 50 calls

No AI required.

---

## Phase 2

Call-Level Behavioral Analysis

Analyze each call independently.

Extract structured observations such as:

* incomplete troubleshooting
* premature transfer
* weak discovery
* expectation setting
* ownership language
* empathy
* resolution confirmation
* transition quality

Each observation must include:

* confidence
* transcript citations
* speaker turns
* timestamp

Do not infer personality.

Only observable behavior.

---

## Phase 3

Cross-Call Pattern Analysis

Aggregate all call observations.

Identify:

Recurring behaviors

Repeated operational failures

Contradictory evidence

Behavior frequency

Behavior consistency

Behavior recency

Weight recent calls higher than older calls.

---

## Phase 4

Root Cause Reasoning

Generate hypotheses only after patterns are established.

Each hypothesis should contain:

* confidence
* supporting evidence
* contradictory evidence
* missing evidence
* recommendation

Never present hypotheses as facts.

Example:

Supported

> Agent consistently transfers before completing troubleshooting.

Hypothesis

> This may indicate uncertainty applying troubleshooting procedures.

Not:

> Agent lacks confidence.

---

## Phase 5

Representative Calls

Surface 2–5 calls that best demonstrate the observed behavior.

Selection should maximize:

* evidence strength
* recency
* representativeness

Each call should include:

* session ID
* timestamp
* transcript excerpts
* explanation

---

## Phase 6

Coaching Narrative

Generate coach-facing language.

Example:

Instead of

"transfer_rate = 0.32"

Generate

"Across four recent calls the expert transferred customers after attempting only one troubleshooting step. This behavior aligns with the engine's Resolution Effectiveness recommendation."

---

# Required Guardrails

The AI must distinguish between:

Observed evidence

↓

Supported interpretation

↓

Hypothesis

Never blur these categories.

---

The AI must abstain if:

* insufficient transcript evidence
* conflicting evidence
* poor transcript quality
* behavior not observable from transcript

Output:

"No transcript-supported root cause identified."

This is a successful outcome.

---

Never infer:

* motivation
* attitude
* intelligence
* burnout
* coachability
* psychological state

Unless explicitly supported by evidence outside transcripts.

---

# Output Schema

```
Decision

Evidence Summary

Behavior Patterns

Root Cause Hypotheses

Representative Calls

Contradictory Evidence

Missing Evidence

Coach Narrative

Abstention
```

---

# Future Enhancements

## Phase 2

Add retrieval weighting using:

* driver metrics
* topic
* transcript embeddings

---

## Phase 3

Track intervention effectiveness.

Example:

```
Topic

↓

Root Cause

↓

Coaching Delivered

↓

Performance Change

↓

Learn effectiveness
```

Eventually estimate:

"What coaching intervention works best for experts exhibiting this behavioral pattern?"

---

## Phase 4

Introduce longitudinal agent reasoning.

Instead of isolated weekly recommendations:

```
Week 1

↓

Week 2

↓

Week 3

↓

Week 4

↓

Trajectory

↓

Recommended intervention
```

---

# Success Criteria

The AI layer should:

✓ Never override deterministic recommendations.

✓ Produce transcript-supported behavioral explanations.

✓ Surface representative recent calls.

✓ Explain recommendations in coach-friendly language.

✓ Explicitly identify uncertainty.

✓ Abstain when evidence is insufficient.

✓ Preserve complete auditability.

---

# Long-Term Vision

The Coaching Decision Engine remains the governed, deterministic decision maker.

The AI layer becomes an evidence-based reasoning system that transforms structured operational decisions into personalized coaching insights.

The result is a hybrid architecture where deterministic analytics determine **what** should be coached, while AI explains **why**, identifies **how** it manifests in real customer interactions, and recommends the most appropriate coaching intervention without sacrificing explainability or governance.
