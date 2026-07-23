**English** · [日本語](BE-0194-record-turn-payload-diet-ja.md)

# BE-0194 — Lean the record turn payload (compact the element tree, token-budget controls, per-category usage)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0194](BE-0194-record-turn-payload-diet.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0194") |
| Implementing PR | [#782](https://github.com/bajutsu-e2e/bajutsu/pull/782) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

Lean the **text** half of each record turn, and make its token spend visible and boundable. After the
screenshot, the largest per-turn input is the rendered accessibility element tree — one line per
element, with **no cap on element count** — and today a user has no way to bound a session's spend
(the `max_steps` limit and the `with_screenshot` toggle exist in the loop but are not exposed on the
CLI) nor to see where the tokens went (usage is reported only as one running total). This item does
three complementary things without touching accuracy: it compacts the element-tree rendering
losslessly, exposes the already-present token-budget knobs on the CLI, and breaks the usage report
down by category so the effect of any token-saving change (this one and its two siblings) is
measurable.

## Motivation

The per-turn user message is built by `_render` (`bajutsu/claude_agent.py`): after the goal, plan,
and recent-actions summary, it emits **one line per on-screen element** —
`- id=… label=… value=… traits=[…]` — for every addressable element. The parse
(`parse_describe_all`, `bajutsu/drivers/idb.py`) already discards `frame` and other raw fields, and
`_render` already skips the app-root and attribute-less nodes, but there is **no cap on the number of
elements** rendered. A screen with a long scrollable list or a data table therefore produces one line
per row, unbounded, so the element-tree token cost scales with on-screen element count. This is the
biggest text-side cost and the one term neither sibling proposal addresses (they target the image).

Two further gaps make the cost hard to *control* and hard to *see*:

- **No token-budget controls on the CLI.** `record()` accepts `with_screenshot: bool = True`
  (`bajutsu/record.py`) and defaults `max_steps=30`, but `bajutsu/cli/commands/record.py` exposes
  neither — so a user cannot cap a session's turns or drop the image even though the loop already
  supports both. There is no flag to bound the element tree either.
- **Usage is one opaque total.** `usage.py` accumulates a single `TokenUsage` across all AI calls; the
  CLI shows one running total. A user cannot see how much went to the screenshot vs. the element text
  vs. the up-front `plan` call vs. the alert-guard vision calls, so they cannot tell where to spend
  effort or confirm that a saving actually landed.

This item is the text-and-measurement counterpart to the two sibling proposals: the
`record-vision-on-demand` proposal cuts how often the image is sent and the `record-screenshot-downscale`
proposal caps its size; this one leans the element text and, crucially, adds the per-category
visibility that lets all three be measured. It is independent of
[BE-0178](../BE-0178-record-multi-action-turn/BE-0178-record-multi-action-turn.md) (turn count).

### Why accuracy is preserved

The element-tree compaction is **lossless for addressing**: every element the agent can act on
(anything carrying selector fields like `id`, `label`, `value`, or `traits`) is always rendered in full, so no action the agent would take
today becomes unavailable. Compaction removes only redundant characters (empty `value`/`traits`
fields that carry no information) and, for pathologically long screens, summarizes the purely
non-addressable remainder as a count rather than silently dropping it — the agent is told what was
omitted, never misled. The CLI knobs default to today's behavior (opt-in only), and the usage
breakdown is reporting-only. None of the three changes the agent's inputs on a normal screen in a way
that alters its decision.

## Detailed design

### 1. Lossless element-line compaction

Change `_render`'s per-element line to omit fields that carry no information: an empty `value` and an
empty `traits` are dropped rather than printed as `value='' traits=[]`. Every field that *does*
disambiguate an element — `id`, `label`, non-empty `value`, non-empty `traits` — is always kept, so
the rendering remains a faithful, fully-addressable list. This is pure character reduction; the set of
elements and the ability to address each is unchanged.

### 2. A deterministic, safe cap for pathological screens

For a screen whose element count exceeds a constant threshold, apply a cap that is **safe by
construction**: keep **all** elements carrying an `id`, `label`, `value`, or `traits` (selector fields the agent may
act on), and collapse the purely-decorative remainder (no id, label, value, traits) into a trailing summary line
— e.g. `- (+N further non-addressable elements omitted)`. An addressable element is never dropped, and
the agent is explicitly told the screen was truncated, so it can `swipe` to reveal more if needed (the
existing off-screen-control path). The threshold is a global constant, not per-app config.

### 3. Token-budget controls on the CLI

Wire the loop's existing knobs through `bajutsu/cli/commands/record.py`:

- `--max-steps N` — surface the loop's `max_steps` (today a hardcoded default of 30), so a user can
  cap the number of turns, and therefore the worst-case spend, up front.
- `--no-screenshot` — surface the loop's `with_screenshot=False`, for an elements-only session when
  the user knows the app is fully instrumented and wants the cheapest possible record. (This is the
  blunt, user-driven version of the `record-vision-on-demand` proposal's automatic gating; the two
  coexist — the flag forces off, the automatic path decides per turn when the flag is unset.)

Both default to today's behavior, so nothing changes unless a user opts in.

### 4. Per-category token usage

Extend the usage accounting (`bajutsu/usage.py` and the record CLI's report) to attribute tokens to
the call site that spent them — at minimum: the `plan` call, the per-turn `next_action` calls, and the
alert-guard vision calls — so the end-of-session summary reads as a small breakdown rather than one
total. This is reporting-only (it never touches pass/fail, consistent with `usage.py`'s existing
best-effort contract) and is what makes the effect of §1–§3 and the two sibling proposals
**measurable** rather than assumed.

### Determinism, prime-directive compliance, and the gate

Strictly **Tier 1 (record only)**: no model call is added to `run` or CI (prime directive 1). The
compaction and cap are deterministic text operations over the element list, with no `sleep` and no
change to selector resolution — an ambiguous selector still fails immediately (prime directive 2). The
compaction, cap threshold, and knobs are global, with no per-app branch (prime directive 3). The
recorded scenario artifact is unchanged, so `run`, `codegen`, the report, and their tests are
unaffected.

### Test strategy (fits the Linux `make check` gate, no Simulator)

- **Compaction rendering tests**: an element with empty `value`/`traits` renders without those fields;
  an element with an `id`/`label`/non-empty `value`/`traits` renders each; the compacted output still
  round-trips to the same set of addressable selectors.
- **Cap tests**: a screen under the threshold renders every element; a screen over it keeps every
  id/label-bearing element and collapses only the non-addressable remainder into the summary line, with
  the omitted count correct; no addressable element is ever dropped.
- **CLI knob tests**: `--max-steps` bounds the loop's turns; `--no-screenshot` yields an
  elements-only observation (no `ImagePart`); both default to today's behavior when unset.
- **Usage breakdown tests**: a scripted session with a `plan` call, N `next_action` calls, and an
  alert-guard call attributes tokens to the right categories and the categories sum to the existing
  total (no double counting).

### Scope & non-goals

**In scope:** lossless element-line compaction; a safe deterministic element cap; `--max-steps` and
`--no-screenshot` CLI flags; per-category token usage reporting.

**Non-goals:** deciding whether to send an image (the `record-vision-on-demand` proposal); resizing the
image (the `record-screenshot-downscale` proposal); reducing turn count (BE-0178); dropping addressable
elements or summarizing the tree with an LLM (that would risk accuracy and put a model on the authoring
critical path unnecessarily); the `crawl` explorer's rendering (out of scope here); any scenario-schema
or `run`/replay change.

## Alternatives considered

- **LLM-summarize a long element tree.** Ask a cheap model to condense the tree before the main turn.
  Rejected: it adds a model call to the authoring path for a text reduction that a deterministic
  compaction achieves without any accuracy risk, and it could drop the exact element the agent needed.
- **Hard-truncate the element list at N.** Simplest cap, but it can silently drop the addressable
  element the goal needs, breaking the record. Rejected in favor of the id/label-preserving cap that
  only collapses non-addressable noise and reports the omission.
- **Bound the recent-actions window further.** The history is already only the last 6 entries and each
  is a one-line summary (`_history_line`) — small relative to the element tree. Rejected as not worth a
  knob; the element tree is where the text cost is.
- **Leave usage as one total.** Simpler, but then no change here or in the sibling proposals can be
  *shown* to have worked. Rejected: the breakdown is cheap (reporting-only) and is the measurement
  backbone for the whole token-saving effort.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Lossless element-line compaction in `_render` (drop empty fields; keep every addressing field).
- [x] Safe deterministic element cap (keep all id/label elements; collapse non-addressable remainder
      into a reported summary line).
- [x] CLI token-budget flags: `--max-steps`, `--no-screenshot` (default to today's behavior).
- [x] Per-category token usage (plan / next_action / alert-guard) in `usage.py` and the record report.
- [x] Tests: compaction rendering; cap preserves addressable elements; CLI knobs; usage-breakdown sums.

**Log**

- [#782](https://github.com/bajutsu-e2e/bajutsu/pull/782) — All four parts shipped together: `_render` compacts each element line and reports the
  non-addressable remainder past `_LARGE_SCREEN_ELEMENTS`; `bajutsu record` gains `--max-steps` /
  `--no-screenshot`; `usage.py` attributes tokens per call-site category (`plan` / `next_action` /
  `alert-guard`) and the record report prints the breakdown under the total. Docs updated
  (`docs/recording.md` + `docs/ja/`). Strictly Tier 1 — no LLM added to `run`/CI.

## References

`bajutsu/claude_agent.py` (`_render`, the per-element line, `_history_line`), `bajutsu/drivers/idb.py`
(`parse_describe_all` — the parsed `Element` fields), `bajutsu/record.py` (`record`, `max_steps`,
`with_screenshot`), `bajutsu/cli/commands/record.py` (the CLI command that does not yet surface the
knobs), `bajutsu/usage.py` (`TokenUsage`, the running total to break down), `docs/recording.md`
(the authoring-agent architecture).

**Dependencies / related items:**
the sibling `record-vision-on-demand` proposal (`--no-screenshot` is the blunt manual counterpart to
its automatic gating; complementary), the sibling `record-screenshot-downscale` proposal (image size;
the usage breakdown here measures its effect), [BE-0178](../BE-0178-record-multi-action-turn/BE-0178-record-multi-action-turn.md)
(turn count; independent), [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
(redaction runs on the same element text this item compacts — compaction must preserve the redaction
step), [BE-0134](../BE-0134-serve-cli-flag-mirror-drift/BE-0134-serve-cli-flag-mirror-drift.md)
(keeping serve's flag mirror in sync — new record CLI flags must not reintroduce serve-to-CLI drift).
