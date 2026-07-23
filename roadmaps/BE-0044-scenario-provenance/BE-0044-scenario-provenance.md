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

A first-class DSL field, `from:`, that records **which natural-language phrase a recorded
construct came from** — attached to each step, each `expect` assertion, and each `capturePolicy`
rule, with the scenario's original goal kept at the scenario level. `record` (Tier 1, AI) fills it
while normalizing natural language into the structured scenario; `run` (Tier 2) ignores it
entirely. It is plain, reviewable YAML that survives the `load ↔ dump` round-trip, so `update`
(M4 minimal-diff proposals) and re-recording never silently drop it.

## Motivation

[DESIGN §6.5](../../DESIGN.md) requires that the origin of each step / rule (the natural
language it was normalized from) be preserved as **provenance** — "leave the origin as a `# from:`
comment or a sidecar". [BE-0002](../BE-0002-m2-ai-loop-and-evidence/BE-0002-m2-ai-loop-and-evidence.md)
shipped the M2 authoring loop but explicitly left this "light — a follow-up rather than an
alternative". Today nothing carries it: `Step` has a free-form `name`, but no field ties a step
back to the natural language that produced it.

Why this matters, and why a structured field rather than the alternatives:

1. **Human review of AI output.** The scenario is the durable artifact a human owns and reviews in
   a PR ([DESIGN §6.5](../../DESIGN.md)). Seeing *"this `tap` exists because the goal said
   'open settings'"* is what lets a reviewer judge whether the normalization is faithful — the
   core check that keeps `record` from quietly producing a scenario that does not match the intent.
2. **`update` needs an anchor.** M4 self-healing proposes a **minimal diff** when the UI changes
   ([DESIGN §6.5](../../DESIGN.md), `triage.py`). To re-derive only the broken step it helps to
   know which intent that step served; provenance is the natural anchor for "re-author *this*
   intent" instead of re-recording the whole scenario.
3. **Comments do not survive the round-trip.** The DESIGN-era idea of a `# from:` comment is
   technically blocked today: scenarios are emitted through
   `scenario_dict()` → `_prune()` → `model_dump()` → `_yaml.safe_dump()` (`scenario.py`), a path
   that **drops all YAML comments**. Every `record` write and every `update` / re-dump would erase
   the provenance. A structured field rides inside the model, so it round-trips by construction.
4. **Determinism is untouched.** `from:` is pure metadata. `run` never reads it, so it adds **no**
   LLM call to the gate and cannot affect pass/fail — it stays squarely on the authoring side
   ([DESIGN §2](../../DESIGN.md), [CLAUDE.md](../../CLAUDE.md) prime directive 1).

## Detailed design

### The DSL — an optional `from:` field

`from:` is an **optional string** holding the natural-language phrase the construct was normalized
from. It is added as a *modifier* (alongside `name` / `capture`), never as an action, so it does
not disturb the "exactly one action / one assertion kind" validators (`_STEP_ACTIONS` /
`_ASSERTION_KINDS` in `scenario.py`). It is attached at four levels:

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

- **Grouping is emergent, not a new construct.** When one utterance produces several steps, those
  steps carry the **same** `from:` string; the report (and `trace`) collapse a run of equal `from:`
  values into one labeled group. No span/range syntax is introduced — keeping the round-trip
  trivial and the field independently editable per step.
- **Round-trip.** `from:` is dumped by alias and pruned when unset (`_prune` drops `None`), so a
  hand-written scenario with no provenance stays clean, and a recorded one preserves it across any
  number of `load`/`dump` cycles.
- **Language.** The string is kept verbatim in whatever language the author wrote (the project is
  bilingual; provenance is not translated).

### Producing it at record time (Tier 1)

`record` is the only writer. The `Agent` already proposes one action per turn; the provenance is
the natural-language intent behind that action:

- `claude_agent.py`: each action tool (`tap` / `type_text` / `wait_for`) gains an optional
  `intent` argument — the short phrase describing *why* this action — persisted into `Step.from_`.
  The `finish` tool's assertions likewise carry the phrase each verifies, into `Assertion.from_`.
  The scenario-level `from:` is simply the `Observation.goal`.
- This is wholly within Tier 1 (AI authors), consistent with prime directive 1. The scripted test
  `Agent` fills deterministic intents so the record tests stay AI-free.

### Run, lint, report

- **`run` (Tier 2):** ignores `from:` completely — it is not read by the orchestrator, so
  determinism and the no-AI-in-the-gate rule are preserved by construction.
- **`lint`:** validates the field type and can surface an **advisory** provenance-coverage figure
  (how many steps in a recorded scenario carry `from:`), mirroring `doctor`'s advisory style — it
  never fails a run (a hand-authored scenario legitimately has none).
- **`trace` / `report.html`:** show each step's `from:` inline ("why this step is here") and label
  emergent groups, turning the timeline into a natural-language ↔ action map. This is the
  user-visible payoff and can land after the schema.

## Alternatives considered

- **`# from:` YAML comments (the original DESIGN wording).** Rejected as the storage form: the
  current dumper (`model_dump` → `safe_dump`) strips comments, so every `record` / `update` write
  would erase them. Preserving comments would require replacing the serializer with a
  comment-aware one (e.g. `ruamel.yaml`) across the whole load/dump path — a large, invasive change
  for strictly worse round-trip guarantees than a model field.
- **A sidecar provenance file** (`<scenario>.provenance.yaml`, keyed by step id). Keeps the
  scenario byte-clean, but introduces a second file to keep in sync with every hand-edit and a
  drift risk (steps reordered/removed in the YAML, stale entries in the sidecar). The project's
  principle is "the YAML is the single durable artifact" — provenance belongs *in* it.
- **A normalized intent table** (scenario-level list of `{id, text}` intents; steps reference
  `from: <intentId>`). Less duplication when many steps share one utterance, but heavier to author
  and edit, and an indirection that hurts at-a-glance review. The inline string with emergent
  grouping is simpler; a normalized table can be revisited if duplication proves painful.
- **Reusing `Step.name`.** `name` is a free-form human label for the step, not the originating
  utterance, and exists for hand-authored scenarios too. Overloading it would conflate
  human-authored naming with AI provenance and lose the scenario/assertion/rule levels.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [DESIGN §6.5](../../DESIGN.md) — normalization & round-trip; the `# from:` / provenance requirement
- [DESIGN §2](../../DESIGN.md), [CLAUDE.md](../../CLAUDE.md) — AI authors, never judges; determinism first
- [BE-0002 — AI authoring loop & evidence (M2)](../BE-0002-m2-ai-loop-and-evidence/BE-0002-m2-ai-loop-and-evidence.md) — where provenance was deferred
- [recording.md](../../docs/recording.md) — the `record` loop and the `Agent` abstraction
- [scenarios.md](../../docs/scenarios.md), `bajutsu/scenario.py` — the scenario schema and load/dump path
