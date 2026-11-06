**English** · [日本語](BE-0021-ai-triage-ja.md)

# BE-0021 — AI triage (root-cause summary, fix suggestions)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0021](BE-0021-ai-triage.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0021") |
| Implementing PR | predates the per-PR history (squashed into the initial import; no single PR) |
| Topic | Self-healing triage |
<!-- /BE-METADATA -->

## Introduction

AI reads the failure evidence and produces a root-cause summary and fix suggestions. A human reviews the result. The command has two modes: `bajutsu triage` (rule-based) and `--ai` (Claude, which also reads the failure screenshot). The deterministic [`trace`](../../docs/glossary.md#evidence-capturepolicy-trace-triage) command is the layer beneath.

## Motivation

When a deterministic run fails, the raw evidence is correct but tedious to read. That evidence includes a failure message, the failed step, an accessibility element tree, screenshots, and logs. Turning it into a root cause and a concrete next edit is judgement-free reading work, which an LLM does well. The work sits squarely on the investigator side of the boundary: triage explains a failure and never decides one.

Without this layer, every failure costs a human a manual dig through artifacts before the fix can even start. The goal is to shorten that loop while the deterministic runner keeps the whole pass/fail verdict.

## Detailed design

Triage is **advisory** and lives entirely outside the `run`/CI gate. Nothing here decides pass or fail. The flow has three pieces:

* **`assemble` (pure, no AI)** reads a saved run's `manifest.json` and reconstructs a `TriageContext` for the first failed scenario. The context holds these items:
  * the failure message
  * the failed step
  * the failed expectations
  * the accessibility element tree nearest the failure
  * the screenshot nearest the failure
  * the scenario definition
  * the selector id the failing step acted on

  `assemble` is deterministic file reading and works offline with no API key.
* **`TriageAgent` protocol with two implementations.** Both produce a summary, a category, and minimal suggestions.
  * `HeuristicTriageAgent` is the default. It is a rule-based, deterministic agent with no AI. It categorizes the failure as `selector`, `timing`, `assertion`, or `unknown`. When a target id is absent but a close id is on the captured screen, it emits a "did you mean" hint, the classic renamed-id self-heal.
  * `ClaudeTriageAgent` (`--ai`) sits behind the same protocol and asks Claude. Claude reads the same evidence, including the failure screenshot. Claude must call a single `diagnose` tool, which returns the same structured `Triage`. The AI agent reasons without hand-written rules.
* **`render`** prints the diagnosis for a human to read and act on.

The deterministic `trace` command is the layer beneath triage. `trace` lays out the evidence without interpretation, and triage interprets that evidence. The LLM is invoked only in triage, on the investigator path that a human runs after a failure. The LLM is never invoked inside `run`.

## Alternatives considered

* **An LLM that reads the run and issues the verdict directly.** Rejected outright. It would put a non-deterministic model in the pass/fail path, which violates the prime directive. Triage explains a failure that the deterministic runner has already declared.
* **Only the rule-based agent, with no AI option.** The heuristic catches the common shapes deterministically: a renamed id, an ambiguous selector, and a timed-out wait. It cannot read a screenshot or reason about an unfamiliar failure. Keeping both agents behind one protocol leaves the rule-based agent as the zero-cost, offline default. The `--ai` option stays available for a failure that needs richer reading.
* **Free-form LLM prose instead of a forced structured tool call.** Forcing the single `diagnose` tool maps the output cleanly onto the `Triage` dataclass and onto the structured fixes that BE-0022 applies. A parser need not recover structure from prose.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[DESIGN §3.1 / §12](../../DESIGN.md), `bajutsu/triage.py` · `bajutsu/claude_triage.py`
