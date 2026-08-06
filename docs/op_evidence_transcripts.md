Yes—that is the stronger framing.

Using Auto QA scores to select transcripts would create a **closed loop**:

```text
Chosen behaviors
  → Auto QA measures those behaviors
  → Decision engine prioritizes those measurements
  → Transcript retrieval selects calls using the same measurements
  → LLM “discovers” those same behaviors
```

The AI output could look highly consistent while adding little new information. It would mostly validate the existing ontology.

## Better division of responsibilities

Use the decision receipt to define the **performance problem**, but do not give the LLM the Auto QA behavior scores during the initial discovery pass.

For example:

```text
Known evidence:
- Resolution rate is below benchmark
- Transfer rate is elevated
- Trend is worsening
- Resolution Effectiveness was selected
```

Then ask the model to inspect recent transcripts for:

* repeated conversational patterns associated with the outcome
* possible mechanisms not represented in the receipt
* contradictory examples
* relevant environmental or interaction factors
* evidence that no transcript-observable cause exists

This preserves the receipt as grounding without constraining the model to the current behavior taxonomy.

## Use a two-pass analysis

### Pass 1: Open behavioral discovery

Inputs:

* decision receipt
* raw transcripts
* timestamps
* session IDs
* basic call metadata

Exclude:

* Auto QA behavior labels
* Auto QA scores
* existing topic-to-behavior mappings beyond what is necessary to explain the decision

Ask the model to produce behavior-neutral observations first:

```json
{
  "observation": "The expert begins troubleshooting before fully clarifying the device state.",
  "calls": ["s17", "s22", "s31"],
  "evidence_spans": ["..."],
  "frequency": 3,
  "confidence": 0.84
}
```

Only after observations are collected should it propose a higher-level mechanism.

### Pass 2: Compare against the existing ontology

After the independent analysis is complete, join the Auto QA results and classify each discovered pattern as:

* **Already measured and aligned**
* **Already measured but apparently mis-scored**
* **Partially represented**
* **Not represented by the current QA taxonomy**
* **Not reliably observable**
* **Potentially environmental rather than agent-controlled**

This makes Auto QA a comparison and validation layer rather than the lens through which discovery occurs.

## This creates two useful outputs

### Individual coaching analysis

For the current recommendation:

* likely transcript-supported mechanisms
* recent representative calls
* contradictory calls
* missing evidence
* conservative abstention

### Behavior-taxonomy discovery

Across the population:

* recurring patterns not mapped to current QA behaviors
* patterns repeatedly associated with operational deficits
* existing behaviors that are too broad
* behaviors whose scoring does not match transcript evidence
* possible new behaviors for SME review

That second output may ultimately be more valuable than the immediate root-cause analysis.

## Important concern: receipt anchoring still remains

Even without Auto QA scores, giving the model the selected topic can bias it toward confirming that topic.

I would therefore use two parallel prompts on a sample of calls:

**Anchored analysis**

> Investigate transcript patterns that may explain the deterministic recommendation.

**Blind analysis**

> Identify repeated behaviors or interaction mechanisms that may affect customer and operational outcomes. Do not reveal the selected recommendation.

Then compare the findings.

If the blind analysis repeatedly discovers the same pattern, confidence rises. If only the anchored analysis finds it, the result may reflect confirmation bias.

You do not necessarily need to run both modes for every expert in production, but you should use them during evaluation and periodically as an audit.

## Transcript selection should also be neutral

Biasing toward recent calls is appropriate. Selecting calls because existing Auto QA scores are poor is less appropriate for discovery.

A better initial sample could include:

* the most recent calls
* calls spanning several days and call types
* a few operationally adverse calls, when outcome metadata exists
* a random control sample
* high-performing or apparently successful calls for contrast

The contrast calls are important. Root causes often become more visible by comparing where the expert succeeds versus where they struggle.

## Recommended architecture

```text
Decision receipt
      ↓
Deterministic call matching
      ↓
Recency-weighted + representative transcript sample
      ↓
Open LLM behavioral discovery
      ↓
Cross-call pattern synthesis
      ↓
Root-cause hypotheses or abstention
      ↓
Compare findings with Auto QA taxonomy and scores
      ├─ confirms existing behavior
      ├─ challenges existing score
      ├─ reveals missing behavior
      └─ indicates non-transcript cause
```

## The key product distinction

The objective should not be:

> Use transcripts to explain the QA behavior scores.

It should be:

> Use transcripts to investigate the operational decision and determine whether the current behavior model adequately explains it.

That gives the LLM permission to find evidence outside the existing measurement framework while still grounding the analysis in a deterministic, auditable performance signal. It also turns the system into a potential **behavior-discovery mechanism**, rather than merely a better narrative generator for the behaviors already encoded.
