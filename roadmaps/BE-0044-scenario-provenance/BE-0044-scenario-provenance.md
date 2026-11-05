**English** · [日本語](BE-0044-scenario-provenance-ja.md)

# BE-0044 — Scenario provenance (`from:` — step ↔ natural-language origin)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0044](BE-0044-scenario-provenance.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0044") |
| Implementing PR | [#235](https://github.com/bajutsu-e2e/bajutsu/pull/235) (schema, record, lint), [#264](https://github.com/bajutsu-e2e/bajutsu/pull/264) (trace + report display) |
| Topic | Authoring experience |
| Origin | BE-0002 follow-up (provenance was deferred there as "remaining light") |
<!-- /BE-METADATA -->

## Introduction

This item adds a first-class DSL field, `from:`.
The field records **which natural-language phrase a recorded construct came from**.
It attaches to each step, each `expect` assertion, and each `capturePolicy` rule.
The scenario's original goal stays at the scenario level.

`record` (Tier 1, AI) fills the field while it normalizes natural language into the structured scenario.
`run` (Tier 2) ignores the field entirely.

The field is plain, reviewable YAML that survives the `load ↔ dump` round-trip.
So `update` (M4 minimal-diff proposals) and re-recording never silently drop it.

## Motivation

[DESIGN §6.5](../../DESIGN.md) requires that the origin of each step or rule be preserved as **provenance**.
The origin is the natural language that the step or rule was normalized from.
DESIGN says to "leave the origin as a `# from:` comment or a sidecar".

[BE-0002](../BE-0002-m2-ai-loop-and-evidence/BE-0002-m2-ai-loop-and-evidence.md) shipped the M2 authoring loop.
It explicitly left provenance "light — a follow-up rather than an alternative".
Today nothing carries provenance.
`Step` has a free-form `name`, but no field ties a step back to the natural language that produced it.

Four reasons explain why provenance matters, and why it is a structured field rather than one of the alternatives.

1. **Human review of AI output.**
   The scenario is the durable artifact that a human owns and reviews in a PR ([DESIGN §6.5](../../DESIGN.md)).
   A reviewer can see that *"this `tap` exists because the goal said 'open settings'"*.
   That view lets the reviewer judge whether the normalization is faithful.
   It is the core check that keeps `record` from quietly producing a scenario that does not match the intent.
2. **`update` needs an anchor.**
   M4 self-healing proposes a **minimal diff** when the UI changes ([DESIGN §6.5](../../DESIGN.md), `triage.py`).
   Re-deriving only the broken step helps when the intent that the step served is known.
   Provenance is the natural anchor for "re-author *this* intent", instead of re-recording the whole scenario.
3. **Comments do not survive the round-trip.**
   The DESIGN-era idea of a `# from:` comment is technically blocked today.
   Scenarios are emitted through `scenario_dict()` → `_prune()` → `model_dump()` → `_yaml.safe_dump()` (`scenario.py`).
   That path **drops all YAML comments**.
   Every `record` write and every `update` or re-dump would erase the provenance.
   A structured field rides inside the model, so it round-trips by construction.
4. **Determinism is untouched.**
   `from:` is pure metadata.
   `run` never reads it, so it adds **no** LLM call to the gate and cannot affect pass or fail.
   The field stays squarely on the authoring side ([DESIGN §2](../../DESIGN.md), [CLAUDE.md](../../CLAUDE.md) prime directive 1).

## Detailed design

### The DSL: an optional `from:` field

`from:` is an **optional string**.
It holds the natural-language phrase that the construct was normalized from.

The field is a *modifier* alongside `name` and `capture`, and never an action.
So it does not disturb the "exactly one action" and "exactly one assertion kind" validators (`_STEP_ACTIONS` and `_ASSERTION_KINDS` in `scenario.py`).
It attaches at four levels:

| Level | Model (`scenario.py`) | Meaning |
|---|---|---|
| Scenario | `Scenario.from_` (alias `from`) | The original natural-language goal the scenario was recorded from |
| Step | `Step.from_` (alias `from`) | The phrase/intent this single step was derived from |
| Assertion | `Assertion.from_` (alias `from`) | The phrase the `expect` / `assert` check came from |
| Capture rule | `CaptureRule.from_` (alias `from`) | The instruction the evidence rule was normalized from (e.g. "screenshot every time submit is tapped") |

```yaml
- name: open settings and reindex
  from: "Open settings, reindex, and confirm the normalization setting is gone"   # ← original goal
  steps:
    - tap: { id: settings.open }
      from: "Open settings"
    - tap: { id: settings.reindex }
      from: "Reindex"
      capture: [screenshot.after, deviceLog]
  expect:
    - exists: { label: "Normalization setting changed", negate: true }
      from: "The normalization setting is gone"
  capturePolicy:
    - on: { action: tap, idMatches: "*.submit" }
      capture: [screenshot.after, elements, network]
      from: "Capture a screenshot and network log on every submit"
```

- **Grouping is emergent, not a new construct.**
  When one utterance produces several steps, those steps carry the **same** `from:` string.
  The report (and `trace`) collapse a run of equal `from:` values into one labeled group.
  No span or range syntax is introduced.
  This keeps the round-trip trivial and the field independently editable per step.
- **Round-trip.**
  `from:` is dumped by alias and pruned when unset (`_prune` drops `None`).
  A hand-written scenario with no provenance stays clean.
  A recorded scenario preserves its provenance across any number of `load` / `dump` cycles.
- **Language.**
  The string is kept verbatim, in whatever language the author wrote.
  The project is bilingual, and provenance is not translated.

### Producing the field at record time (Tier 1)

`record` is the only writer.
The `Agent` already proposes one action per turn, and the provenance is the natural-language intent behind that action.

- In `claude_agent.py`, each action tool (`tap` / `type_text` / `wait_for`) gains an optional `intent` argument.
  The argument is the short phrase that describes *why* the action happens.
  It is persisted into `Step.from_`.
- The `finish` tool's assertions likewise carry the phrase that each one verifies, into `Assertion.from_`.
- The scenario-level `from:` is simply the `Observation.goal`.
- All of this stays within Tier 1 (AI authors), consistent with prime directive 1.
  The scripted test `Agent` fills deterministic intents, so the record tests stay AI-free.

### Run, lint, and report

- **`run` (Tier 2):** ignores `from:` completely.
  The orchestrator does not read the field, so determinism and the no-AI-in-the-gate rule are preserved by construction.
- **`lint`:** validates the field type.
  It can also surface an **advisory** provenance-coverage figure: how many steps in a recorded scenario carry `from:`.
  This mirrors `doctor`'s advisory style, and it never fails a run, because a hand-authored scenario legitimately has none.
- **`trace` / `report.html`:** show each step's `from:` inline ("why this step is here"), and label emergent groups.
  The timeline becomes a natural-language ↔ action map.
  This is the user-visible payoff, and it can land after the schema.

## Alternatives considered

- **`# from:` YAML comments (the original DESIGN wording).**
  Rejected as the storage form.
  The current dumper (`model_dump` → `safe_dump`) strips comments, so every `record` or `update` write would erase them.
  Preserving comments would require replacing the serializer with a comment-aware one (e.g. `ruamel.yaml`) across the whole load/dump path.
  That is a large, invasive change, and it gives strictly worse round-trip guarantees than a model field.
- **A sidecar provenance file** (`<scenario>.provenance.yaml`, keyed by step id).
  It keeps the scenario byte-clean.
  But it introduces a second file to keep in sync with every hand-edit, and a drift risk.
  Steps may be reordered or removed in the YAML, leaving stale entries in the sidecar.
  The project's principle is "the YAML is the single durable artifact", so provenance belongs *in* it.
- **A normalized intent table** (a scenario-level list of `{id, text}` intents, with steps referencing `from: <intentId>`).
  It duplicates less when many steps share one utterance.
  But it is heavier to author and edit, and the indirection hurts at-a-glance review.
  The inline string with emergent grouping is simpler.
  A normalized table can be revisited if duplication proves painful.
- **Reusing `Step.name`.**
  `name` is a free-form human label for the step, not the originating utterance, and it exists for hand-authored scenarios too.
  Overloading it would conflate human-authored naming with AI provenance.
  It would also lose the scenario, assertion, and rule levels.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [DESIGN §6.5](../../DESIGN.md) — normalization & round-trip; the `# from:` / provenance requirement
- [DESIGN §2](../../DESIGN.md), [CLAUDE.md](../../CLAUDE.md) — AI authors, never judges; determinism first
- [BE-0002 — AI authoring loop & evidence (M2)](../BE-0002-m2-ai-loop-and-evidence/BE-0002-m2-ai-loop-and-evidence.md) — where provenance was deferred
- [recording.md](../../docs/recording.md) — the `record` loop and the `Agent` abstraction
- [scenarios.md](../../docs/scenarios.md), `bajutsu/scenario.py` — the scenario schema and load/dump path
