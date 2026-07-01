**English** · [日本語](BE-0021-ai-triage-ja.md)

# BE-0021 — AI triage (root-cause summary, fix suggestions)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0021](BE-0021-ai-triage.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Implementing PR | predates the per-PR history (squashed into the initial import; no single PR) |
| Topic | Self-healing triage (M4) |
<!-- /BE-METADATA -->

## Introduction

AI reads the failure evidence and produces a root-cause summary and fix suggestions (human review assumed). `bajutsu triage` (rule-based) plus `--ai` (Claude, including the failure screenshot). The deterministic `trace` command is the layer beneath it.

## Motivation

When a deterministic run fails, the raw evidence — a failure message, the failed step, an accessibility element tree, screenshots, logs — is correct but tedious to read. Turning it into a root cause and a concrete next edit is exactly the kind of judgement-free reading work an LLM is good at, and it is squarely on the investigator side of the boundary: it explains a failure, it never decides one. Without this layer every failure costs a human a manual dig through artifacts before they can even start fixing the scenario. The goal is to shorten that loop while keeping the pass/fail verdict entirely in the deterministic runner.

## Detailed design

Triage is **advisory** and lives entirely outside the `run`/CI gate — nothing here ever decides pass/fail. The flow has three pieces:

* **`assemble` (pure, no AI)** reads a saved run's `manifest.json` and reconstructs a `TriageContext` for the first failed scenario: the failure message, the failed step, the failed expectations, the accessibility element tree nearest the failure, the screenshot nearest the failure, the scenario definition, and the selector id the failing step acted on. This is deterministic file reading; it works offline with no API key.
* **`TriageAgent` protocol with two implementations.** `HeuristicTriageAgent` is the default: a rule-based, deterministic agent (no AI) that categorizes the failure as `selector` / `timing` / `assertion` / `unknown` and, when a target id is absent but a close id is on the captured screen, emits a "did you mean" hint — the classic renamed-id self-heal. `ClaudeTriageAgent` (`--ai`) sits behind the same protocol and asks Claude, which reads the same evidence (including the failure screenshot) and is forced to call a single `diagnose` tool that returns the same structured `Triage`. Both produce a summary, a category, and minimal suggestions; the AI one just reasons without hand-written rules.
* **`render`** prints the diagnosis for a human to read and act on.

The deterministic `trace` command is the layer beneath triage: it lays out the evidence without interpretation. Triage interprets that evidence. The LLM is invoked only here, on the investigator path the human runs after a failure — never inside `run`.

## Alternatives considered

* **An LLM that reads the run and verdicts it directly.** Rejected outright: it would put a non-deterministic model in the pass/fail path, violating the prime directive. The whole point is that triage explains a failure the deterministic runner already declared.
* **Only the rule-based agent, no AI option.** The heuristic catches the common shapes (renamed id, ambiguous selector, timed-out wait) deterministically, but it cannot read a screenshot or reason about an unfamiliar failure. Keeping both behind one protocol lets the rule-based agent stay the zero-cost, offline default while `--ai` is available when the failure needs richer reading.
* **Free-form LLM prose instead of a forced structured tool call.** Forcing the single `diagnose` tool keeps the output mapped cleanly onto the `Triage` dataclass and onto the structured fixes BE-0022 applies, rather than asking a parser to recover structure from prose.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[DESIGN §3.1 / §12](../../../DESIGN.md), `bajutsu/triage.py` · `bajutsu/claude_triage.py`
