**English** · [日本語](BE-0014-record-demarcation-ja.md)

# BE-0014 — Demarcation from the existing AI record

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0014](BE-0014-record-demarcation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0014") |
| Implementing PR | [#390](https://github.com/bajutsu-e2e/bajutsu/pull/390), [#392](https://github.com/bajutsu-e2e/bajutsu/pull/392) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

Define the division of roles among the authoring surfaces Bajutsu is growing — the AI record loop, action-capture (BE-0012), and the scenario GUI editor (BE-0013) — and specify how a scenario moves between them. Authoring from *intent*, authoring from *demonstration*, and *editing* an existing scenario are three answers to three different questions, but they must produce and consume one `Scenario` so the author can mix them freely on the same file. This proposal settles which surface to reach for when, and specifies the one conversion that needs machinery: enriching a structurally-captured or hand-edited scenario with the assertions only intent inference can supply.

## Motivation

The AI record loop was the only path from "a thing a human wants verified" to a scenario. Two more are landing close behind it: action-capture (BE-0012) authors a flow from a live demonstration, offline and without an API key; the GUI editor (BE-0013) edits an existing scenario with a screenshot-driven element picker. Three authoring surfaces arriving together is a real risk, not a hypothetical one: without a stated boundary they accrete divergent scenario shapes, duplicate the same selector/save plumbing three times, and leave the author guessing which one to open. They genuinely solve different problems, so the answer is not to merge them but to draw the boundary explicitly and to make the file the thing they share.

The boundaries are by authoring *input*:

- The **AI loop** authors from *intent* — a natural-language goal. Best when the author does not yet know the exact steps, when the flow needs exploration, or when the goal is easier to state than to perform. It infers both the steps and the verifying assertions. It needs `ANTHROPIC_API_KEY` and spends LLM (large language model) round-trips.
- **Action-capture** authors from *demonstration* — real operations on a booted device, resolved to stable selectors at capture time. Best when the author already knows the flow, wants it offline and fast, or finds it easier to demonstrate than to describe. No API key; resolution is purely structural, and it emits steps only — it does not infer assertions.
- The **GUI editor** *edits* an existing scenario — pick an element on a screenshot, get the stablest selector with its `doctor` score, adjust steps and the assertion DSL (domain-specific language) field by field. Best for refining a draft any of the surfaces produced, or correcting a fragile selector, without dropping to raw YAML by hand.

The risk these three pose — divergent formats, triplicated plumbing, a confused author — is exactly what this proposal pre-empts.

## Detailed design

### One scenario, three inputs, one save path

The boundary is by authoring *input*, never by output. All three surfaces produce and consume the identical `Scenario` (steps + `expect`) and feed the same deterministic `run`, `codegen`, and report. Nothing downstream needs to know which surface produced a scenario, and none of the three is in the Tier-2 / CI gate.

What makes them interchangeable rather than three disconnected tools is that **all three write through one author-owned save path**: `bajutsu/serve/scenarios.py:ScenarioScope.save()` / `.authored()`. The AI loop already writes through it (`record` → `authored()`); capture appends each resolved step and re-saves through it (BE-0012); the editor reads and writes the same `*.yaml` through it (BE-0013). Because the save path is shared and the format is one, a hand-edit in `$EDITOR`, a captured step, an AI-authored draft, and a picker edit are all the same artifact — reviewable in a PR and never silently rewritten. The scope owns the write, so this holds identically for the local on-disk store and the server-side storage store (BE-0015) without any surface knowing the difference.

The division of roles, stated once so an author knows which to open:

| Authoring input | Surface | Emits | Needs a key | Picks |
|---|---|---|---|---|
| *Intent* (a goal) | AI record loop (`record.py`) | steps **and** assertions | yes | explore an app you don't yet know; state a goal rather than perform it |
| *Demonstration* (operations) | Action-capture (BE-0012) | steps only | no | replay a flow you know, offline and fast; demonstrate rather than describe |
| *Editing* (an existing file) | GUI editor (BE-0013) | edits steps/assertions | no | refine a draft; fix a fragile selector without hand-editing YAML |

### Capture / edit → assertion enrichment (the one conversion worth specifying)

The conversion between surfaces is asymmetric, and only one direction needs machinery.

Because all three produce the same `Scenario`, a **captured or edited scenario is already a first-class scenario** — it can be hand-edited, run, and fed to `codegen` with no further step. "Converting capture → scenario" is the identity, and "editor → scenario" is just the save. The reverse — taking an AI-authored scenario and re-capturing or re-editing it — is normal editing or re-recording; no special machinery is needed.

The one direction that needs design is **enriching a structural scenario with assertions**. Capture and the editor produce *steps*; they cannot infer the *intent* a `wait` / `expect` should verify, so a captured or freshly-edited scenario can have a complete step sequence but thin or absent assertions. Turning that into a *verified* scenario is intent inference, which is the AI loop's job. The hand-off:

1. The author has a scenario with steps but thin assertions — from capture, from the editor, or hand-written.
2. They invoke the AI loop in an **enrichment mode** over that existing step sequence: the loop replays/reads the steps, infers what each step is meant to establish, and proposes the verifying assertions (`expect` entries, settle `wait`s) for the steps that lack them.
3. The proposal arrives as a **reviewable diff** the author accepts or rejects — never a silent rewrite, consistent with "AI output is always a proposed diff." Accepting it writes back through the same `ScenarioScope.save()`, so the enriched scenario is the same artifact, now verified.

This keeps the surfaces composable end to end: demonstrate the flow with capture (offline, no key), tidy a selector in the editor, then spend a key once to let the AI loop add the assertions — without re-authoring from scratch. The demarcation is firm in the other direction: **capture never infers intent**. Inferring what a step should verify is the AI's authoring/investigation role; capture stays purely structural so its offline, no-API-key guarantee is unconditional. The AI proposes assertions; it never decides pass/fail.

### Unified authoring surface in `serve`

The three surfaces are not three separate tools bolted onto `serve` — they are three modes over **one open scenario**. The `serve` UI already hosts the scenario (BE-0011: the scenario view, the raw-YAML textarea, the run report with per-screen screenshots and element trees). The other two are enrichments of that same view, not new pages:

- The **editor** (BE-0013) turns the scenario view into structured, field-by-field editing with the screenshot as the source of truth and the element picker resolving selectors.
- **Capture** (BE-0012) is the same view in a "demonstrate" mode: the author marks actions on a live screenshot, Bajutsu resolves each to a selector and proxy-actuates it, streaming steps into the open scenario.
- **Enrichment** is an action on the open scenario ("propose assertions"), surfacing the AI loop's diff inline in the same view.

So the author opens one scenario and switches authoring mode over it — demonstrate to add steps, pick to fix a selector, propose to add assertions — rather than choosing a tool up front. The picker and `doctor`-score component are shared between capture and the editor (BE-0012 and BE-0013 both call this out), and all modes round-trip through the one `ScenarioScope`, so switching modes never reshapes the file. Capture's one architectural departure — a live `Driver` held across requests for the booted target — is confined to the capture mode's session and stays out of the pure resolver/emitter core (BE-0012); the editor and the raw textarea remain stateless over the run's captured artifacts.

### Prime directives, tiering, and the gate

The arrangement preserves every prime directive:

- **AI authors and investigates, never judges.** The AI loop's involvement is confined to Tier 1 authoring (intent → steps + assertions) and to *proposing* the enrichment diff. Capture and the editor never call a model. No surface puts an LLM call into the deterministic `run` / CI gate; pass/fail stays machine-only.
- **Determinism first.** Every surface selects by stable `id` (down the same stability ladder), uses condition waits not fixed sleeps, and surfaces an ambiguous selector rather than guessing — the same `resolve_unique` rule, applied at authoring time by capture and the editor and at run time by the runner.
- **App-agnostic.** One `Scenario` format and one save path keep the tool target-agnostic: all three read `targets.<name>` and write the app's scenarios dir the same way, through the same scope, so nothing per-app leaks into the surfaces.

Tiering is clean along the key boundary: the **AI parts need a key** (the `record` loop and the enrichment mode), the **structural parts do not** (capture, the editor, running the result). None of them is a path into the Tier-2 run/CI gate — they all author or edit a scenario that the deterministic runner then judges on its own.

## Alternatives considered

* **Split the surfaces into separate commands with separate scenario shapes.** Rejected: divergent formats would fragment `run` / `codegen` / the report and break the "one human-owned YAML" model. A single shared `Scenario` plus one `ScenarioScope.save()` path is what makes the surfaces complementary instead of competing, and composable instead of disconnected.
* **Let capture (or the editor) infer assertions on its own — heuristically or via an inline AI call.** Rejected: inferring intent is the AI's job and must arrive as a reviewable proposal, not be baked silently into a captured or edited file. Keeping capture and the editor purely structural preserves their offline, no-API-key guarantee, and routing all assertion inference through the AI loop's enrichment mode keeps the "AI proposes a diff, the human accepts" contract in one place.
* **Three separate tools in `serve` instead of modes over one scenario.** Rejected: separate tools would duplicate the picker / doctor-score component and the save plumbing, and force the author to pick a tool before they know which input they have. Modes over one open scenario share the component and let the author switch as the work demands.
* **Leave the division undocumented and let usage settle organically.** Rejected: with three authoring surfaces landing close together, the absence of a stated boundary is exactly what produces duplicate code and user confusion — the cost this proposal exists to pre-empt.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[recording.md](../../docs/recording.md), [scenarios.md](../../docs/scenarios.md); `bajutsu/record.py` (the AI loop: `record()`, `_plan_goal`, `_settle_step`, screenshot plumbing — the intent → steps + assertions path that also hosts the enrichment mode), `bajutsu/serve/scenarios.py` (`ScenarioScope.save()` / `.authored()` — the one author-owned save path all three surfaces write through).

**Dependencies / related items:** [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) (the `serve` host, `ScenarioScope`, screenshot + report plumbing the surfaces share), [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) (authoring from demonstration; emits steps only, defers the demarcation and the enrichment direction here), [BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) (editing an existing scenario; shares the element picker + doctor score with capture).
