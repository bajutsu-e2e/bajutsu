**English** · [日本語](BE-0014-record-demarcation-ja.md)

# BE-0014 — Demarcation from the existing AI record

* Proposal: [BE-0014](BE-0014-record-demarcation.md)
* Status: **Proposal**
* Track: [Proposals](../README.md#proposals)
* Topic: Authoring experience (record / GUI editor)

## Introduction

Document the division of roles between AI-driven exploration and writing versus direct capture of human operations, and specify how to convert between the two forms.

## Motivation

Once action-capture (BE-0012) exists alongside the AI record loop, the tool has **two** ways to author a scenario, and without a clear story they will confuse users and accrete duplicate code. They genuinely solve different problems: the AI loop authors from *intent* — explore an app you do not yet know step by step toward a natural-language goal — while capture authors from *demonstration* — replay a flow you already know, fast and offline, with no API key. The risk is two divergent commands, two slightly different scenario shapes, and an author who does not know which to reach for. This proposal exists to settle the division of roles up front and to specify conversion between the two forms, so the second authoring path strengthens the first rather than fragmenting it.

## Detailed design

The boundary is by authoring *input*, not by output: both paths emit the identical `Scenario` (steps + `expect`) and feed the same deterministic `run`, `codegen`, and report. Nothing downstream needs to know which path produced a scenario, and neither path is in the Tier-2 / CI gate.

* **AI record (`record`)** — authoring from a natural-language goal. Best when the author does not yet know the exact steps, when the flow needs exploration, or when the goal is easier to state than to perform. Needs `ANTHROPIC_API_KEY`; spends LLM (large language model) round-trips. It already inserts settle waits and proposes the verifying assertions.
* **Action capture (`record` capture mode / BE-0012)** — authoring from real operations. Best when the author already knows the flow, wants it offline and fast, or finds it easier to demonstrate than to describe. No API key; resolution is purely structural.

Conversion is the part worth specifying, and it is asymmetric. Because both produce the same `Scenario`, a **captured scenario is already a first-class scenario** — it can be hand-edited, run, and fed to `codegen` with no further step; "converting capture → scenario" is the identity. The interesting direction is **enriching a captured scenario with assertions**: capture records actions but cannot infer the *intent* a `wait` / `expect` should verify, so a captured scenario may have steps but thin assertions. The conversion lets the AI loop (the investigator role) propose the verifying assertions for an existing captured step sequence, as a reviewable diff the author accepts — never a silent rewrite, consistent with "AI output is always a proposed diff." The reverse — taking an AI-authored scenario and re-capturing it by hand — is just normal editing or re-recording; no special machinery is needed.

The whole arrangement preserves the prime directives: one scenario format keeps the tool app-agnostic (both paths read `apps.<name>` and write the app's scenarios dir the same way), both keep determinism (selection by stable `id`, condition waits only, ambiguous selectors surfaced not guessed), and the AI involvement is confined to Tier 1 authoring and to *proposing* assertion diffs — pass/fail stays machine-only.

## Alternatives considered

* **Split the two paths into separate commands with separate scenario shapes.** Rejected: divergent formats would fragment `run` / `codegen` / the report and break the "one human-owned YAML" model. A single shared `Scenario` is what makes the two paths complementary instead of competing.
* **Let capture also infer assertions on its own (heuristically or via AI inline).** Rejected: inferring intent is the AI's job and must arrive as a reviewable proposal, not be baked silently into the captured file. Keeping capture purely structural preserves its offline, no-API-key guarantee.
* **Leave the division undocumented and let usage settle organically.** Rejected: with two authoring paths landing close together, the absence of a stated boundary is exactly what produces duplicate code and user confusion — the cost this proposal is meant to pre-empt.

## References

[recording.md](../../recording.md)
