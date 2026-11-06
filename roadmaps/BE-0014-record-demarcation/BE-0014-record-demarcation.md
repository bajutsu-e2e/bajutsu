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

This proposal defines the division of roles among the authoring surfaces Bajutsu is growing: the AI record loop, action-capture ([BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md)), and the scenario GUI (graphical user interface) editor ([BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)). It also specifies how a scenario moves between them.

Authoring from *intent*, authoring from *demonstration*, and *editing* an existing scenario answer three different questions. All three must still produce and consume one `Scenario`, so the author can mix them freely on the same file. This proposal settles which surface to reach for when. It also specifies the one conversion that needs machinery: enriching a structurally captured or hand-edited scenario with the assertions that only intent inference can supply.

## Motivation

The AI record loop was the only path from "something a human wants verified" to a scenario. Two more paths are landing close behind it:

- Action-capture (BE-0012) authors a flow from a live demonstration, offline and without an API key.
- The GUI editor (BE-0013) edits an existing scenario with a screenshot-driven element picker.

Three authoring surfaces arriving together is a real risk, not a hypothetical one. Without a stated boundary, the surfaces accrete divergent scenario shapes. They duplicate the same selector and save plumbing three times. They also leave the author guessing which surface to open. The three surfaces solve different problems, so merging them is the wrong answer. The answer is to draw the boundary explicitly and to make the scenario file the thing the surfaces share.

The boundaries follow the authoring *input*:

- The **AI loop** authors from *intent*, a natural-language goal. It fits when the author does not yet know the exact steps, when the flow needs exploration, or when the goal is easier to state than to perform. It infers both the steps and the verifying assertions. It needs `ANTHROPIC_API_KEY` and spends LLM (large language model) round-trips.
- **Action-capture** authors from *demonstration*: real operations on a booted device, resolved to stable selectors at capture time. It fits when the author already knows the flow, wants it offline and fast, or finds it easier to demonstrate than to describe. It needs no API key, and its resolution is purely structural. It emits steps only and does not infer assertions.
- The **GUI editor** *edits* an existing scenario. The author picks an element on a screenshot and gets the most stable selector with its `doctor` score. The author then adjusts steps and the assertion DSL (domain-specific language) field by field. It fits when the author refines a draft that any surface produced, or corrects a fragile selector, without dropping to raw YAML by hand.

This proposal pre-empts the risk the three surfaces pose: divergent formats, triplicated plumbing, and a confused author.

## Detailed design

### One scenario, three inputs, one save path

The boundary follows the authoring *input*, never the output. All three surfaces produce and consume the identical `Scenario` (steps + `expect`). All three feed the same deterministic `run`, `codegen`, and report. Nothing downstream needs to know which surface produced a scenario, and none of the three sits in the [Tier-2](../../docs/glossary.md#the-two-tiers) / CI gate.

What makes the surfaces interchangeable, instead of three disconnected tools, is that **all three write through one author-owned save path**: `bajutsu/serve/scenarios.py:ScenarioScope.save()` / `.authored()`.

- The AI loop already writes through the path (`record` → `authored()`).
- Capture appends each resolved step and re-saves through the path (BE-0012).
- The editor reads and writes the same `*.yaml` through the path (BE-0013).

The save path is shared and the format is one. A hand-edit in `$EDITOR`, a captured step, an AI-authored draft, and a picker edit are therefore the same artifact. A PR can review the artifact, and nothing rewrites it silently. The scope owns the write. This holds identically for the local on-disk store and the server-side storage store ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)), and no surface needs to know the difference.

The following table states the division of roles once, so an author knows which surface to open:

| Authoring input | Surface | Emits | Needs a key | Picks |
|---|---|---|---|---|
| *Intent* (a goal) | AI record loop (`record.py`) | steps **and** assertions | yes | explore an app you don't yet know; state a goal rather than perform it |
| *Demonstration* (operations) | Action-capture (BE-0012) | steps only | no | replay a flow you know, offline and fast; demonstrate rather than describe |
| *Editing* (an existing file) | GUI editor (BE-0013) | edits steps/assertions | no | refine a draft; fix a fragile selector without hand-editing YAML |

### Capture / edit → assertion enrichment (the one conversion worth specifying)

The conversion between surfaces is asymmetric, and only one direction needs machinery.

All three surfaces produce the same `Scenario`, so a **captured or edited scenario is already a first-class scenario**. The author can hand-edit it, run it, and feed it to `codegen` with no further step. "Converting capture → scenario" is the identity, and "editor → scenario" is just the save. The reverse direction takes an AI-authored scenario and re-captures or re-edits it. That is normal editing or re-recording and needs no special machinery.

The one direction that needs design is **enriching a structural scenario with assertions**. Capture and the editor produce *steps*. They cannot infer the *intent* that a `wait` or `expect` should verify. A captured or freshly edited scenario can therefore have a complete step sequence but thin or absent assertions. Turning that scenario into a *verified* scenario is intent inference, which is the AI loop's job. The hand-off runs in three steps:

1. The author has a scenario with steps but thin assertions. The scenario comes from capture, from the editor, or from hand-writing.
2. The author invokes the AI loop in an **enrichment mode** over that existing step sequence. The loop replays or reads the steps and infers what each step is meant to establish. It proposes the verifying assertions (`expect` entries, settle `wait`s) for the steps that lack them.
3. The proposal arrives as a **reviewable diff** that the author accepts or rejects. It is never a silent rewrite, consistent with "AI output is always a proposed diff". Accepting the diff writes back through the same `ScenarioScope.save()`. The enriched scenario is the same artifact, now verified.

This keeps the surfaces composable end to end. The author demonstrates the flow with capture (offline, no key) and tidies a selector in the editor. The author then spends a key once to let the AI loop add the assertions, without re-authoring from scratch.

The demarcation is firm in the other direction: **capture never infers intent**. Inferring what a step should verify is the AI's authoring and investigation role. Capture stays purely structural, so its offline, no-API-key guarantee is unconditional. The AI proposes assertions and never decides pass/fail.

### Unified authoring surface in `serve`

The three surfaces are not three separate tools bolted onto `serve`. They are three modes over **one open scenario**. The `serve` UI already hosts the scenario ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)): the scenario view, the raw-YAML textarea, and the run report with per-screen screenshots and element trees. The other two surfaces enrich that same view and add no new pages:

- The **editor** (BE-0013) turns the scenario view into structured, field-by-field editing. The screenshot is the source of truth, and the element picker resolves selectors.
- **Capture** (BE-0012) is the same view in a "demonstrate" mode. The author marks actions on a live screenshot. Bajutsu resolves each action to a selector and proxy-actuates it, streaming steps into the open scenario.
- **Enrichment** is an action on the open scenario ("propose assertions"). It surfaces the AI loop's diff inline in the same view.

The author opens one scenario and switches authoring mode over it, instead of choosing a tool up front. The author demonstrates to add steps, picks to fix a selector, and proposes to add assertions.

Capture and the editor share the picker and the `doctor`-score component, and BE-0012 and BE-0013 both call this out. All modes round-trip through the one `ScenarioScope`, so switching modes never reshapes the file.

Capture has one architectural departure: a live `Driver` held across requests for the booted target. That departure is confined to the capture mode's session and stays out of the pure resolver/emitter core (BE-0012). The editor and the raw textarea remain stateless over the run's captured artifacts.

### Prime directives, tiering, and the gate

The arrangement preserves every prime directive:

- **AI authors and investigates, never judges.** The AI loop's involvement is confined to [Tier 1](../../docs/glossary.md#the-two-tiers) authoring (intent → steps + assertions) and to *proposing* the enrichment diff. Capture and the editor never call a model. No surface puts an LLM call into the deterministic `run` / CI gate. Pass/fail stays machine-only.
- **Determinism first.** Every surface selects by stable `id`, down the same stability ladder. Every surface uses condition waits, not fixed sleeps, and surfaces an ambiguous selector instead of guessing. The `resolve_unique` rule is the same everywhere: capture and the editor apply it at authoring time, and the runner applies it at run time.
- **App-agnostic.** One `Scenario` format and one save path keep the tool target-agnostic. All three surfaces read `targets.<name>` and write the app's scenarios dir the same way, through the same scope. Nothing per-app leaks into the surfaces.

Tiering splits cleanly along the key boundary. The **AI parts need a key**: the `record` loop and the enrichment mode. The **structural parts do not**: capture, the editor, and running the result. None of the surfaces is a path into the Tier-2 run/CI gate. Each surface authors or edits a scenario that the deterministic runner then judges on its own.

## Alternatives considered

* **Split the surfaces into separate commands with separate scenario shapes.** Rejected. Divergent formats would fragment `run`, `codegen`, and the report, and would break the "one human-owned YAML" model. A single shared `Scenario` plus one `ScenarioScope.save()` path makes the surfaces complementary instead of competing, and composable instead of disconnected.
* **Let capture (or the editor) infer assertions on its own, heuristically or via an inline AI call.** Rejected. Inferring intent is the AI's job, and the result must arrive as a reviewable proposal. It must not be baked silently into a captured or edited file. Keeping capture and the editor purely structural preserves their offline, no-API-key guarantee. Routing all assertion inference through the AI loop's enrichment mode keeps the "AI proposes a diff, the human accepts" contract in one place.
* **Three separate tools in `serve` instead of modes over one scenario.** Rejected. Separate tools would duplicate the picker and doctor-score component and the save plumbing. They would also force the author to pick a tool before knowing which input the author has. Modes over one open scenario share the component and let the author switch as the work demands.
* **Leave the division undocumented and let usage settle organically.** Rejected. Three authoring surfaces land close together, and the absence of a stated boundary is exactly what produces duplicate code and user confusion. This proposal exists to pre-empt that cost.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[recording.md](../../docs/recording.md), [scenarios.md](../../docs/scenarios.md); `bajutsu/record.py` (the AI loop: `record()`, `_plan_goal`, `_settle_step`, screenshot plumbing — the intent → steps + assertions path that also hosts the enrichment mode), `bajutsu/serve/scenarios.py` (`ScenarioScope.save()` / `.authored()` — the one author-owned save path all three surfaces write through).

**Dependencies / related items:** [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) (the `serve` host, `ScenarioScope`, screenshot + report plumbing the surfaces share), [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) (authoring from demonstration; emits steps only, defers the demarcation and the enrichment direction here), [BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) (editing an existing scenario; shares the element picker + doctor score with capture).
