**English** · [日本語](BE-0028-evidence-rule-overmatch-guard-ja.md)

# BE-0028 — Guard against over-matching evidence rules

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0028](BE-0028-evidence-rule-overmatch-guard.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0028") |
| Implementing PR | [#90](https://github.com/bajutsu-e2e/bajutsu/pull/90) · [#91](https://github.com/bajutsu-e2e/bajutsu/pull/91) |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

Prevent artifacts from bloating when a `capturePolicy` over-matches. The proposal adds an `--explain` dry run and a lighter default policy.

## Motivation

A [`capturePolicy`](../../docs/glossary.md#evidence-capturepolicy-trace-triage) rule fires "every time" its trigger matches (DESIGN §9 A, `docs/evidence.md`). That is exactly the point: the evidence is repeatable and deterministic. But it also makes a too-broad rule expensive in a way that stays invisible until after a run.

A trigger like `{ action: tap, idMatches: "*.submit" }` reads as "the submit button". A loose glob can match far more steps than the author intended, and so can an `event: screenChanged` rule on a busy flow. Attach a heavy capture (`video`, `network`) to such a rule and a run quietly produces gigabytes of artifacts and slows down. It also risks the observer effect that §9 warns about.

DESIGN §11 lists this directly as an open risk: "evidence rules over-matching, bloating artifacts → an `--explain` dry run and a lighter default policy." The cost-management note in DESIGN §9 reinforces both halves. Rules should narrow their match conditions. The default should be the lightweight trio of `screenshot` + `elements` + `actionLog`, with `video` / `network` opt-in.

The problem today has two parts. Nothing makes over-matching *visible before* a run. Nothing stops a heavy capture from silently becoming the effective default. This proposal addresses both with a way to preview firing and a default that is cheap unless you ask for more.

## Detailed design

The design has two complementary guards. Neither touches pass/fail.

- **A pre-run `--explain` dry run.** A command (for example `bajutsu trace --explain <scenario>`, as §11 names it) statically walks the scenario against its `capturePolicy`. For each rule, it reports how many times the rule would fire and on which steps. It also flags heavy captures (`video` / `network`) on broadly matching rules. The command is the *pre-run* counterpart to the existing post-hoc `trace`. The existing `trace` reads a completed run's manifest, while the dry run previews what a run *would* capture. An author can then tighten a glob before paying for it. The dry run is read-only and deterministic. It resolves selectors and matches triggers the same way the run loop will, so its count is the real one, with no LLM involved.
- **A lighter default policy.** The implicit default capture stays the lightweight trio of `screenshot` + `elements` + `actionLog`. It also keeps the `result: error` safety net, which captures the maximum only when a step actually fails. Interval-heavy kinds (`video`, `deviceLog`, `network`) remain strictly opt-in. An author attaches them deliberately through an inline `capture:` or a narrowly scoped rule. The costly captures then run only where an author asked for them, and the common case is cheap by construction.
- **Determinism and app-agnosticism preserved.** Neither guard changes what a run asserts. `--explain` is advisory output, and the default-policy choice affects which evidence is gathered, never pass/fail. There are no fixed sleeps, and selector strictness is unchanged. Any per-app capture preferences live under `apps.<name>` alongside other per-app settings. The tool and runner stay app-agnostic, and the Tier-2 gate stays LLM-free.

## Alternatives considered

- **A hard cap on artifact size or count.** A run could abort or truncate once it exceeds a byte or file budget. That bounds the damage but discards evidence non-deterministically, because the same scenario could capture different artifacts depending on timing. This conflicts with determinism-first. Previewing the firing count and defaulting to cheap captures prevents the bloat instead of reacting to it.
- **Warn at run time when a rule fires "too often."** A run-time heuristic is reactive, because the artifacts are already written by the time the threshold trips. A magic "too often" number is also arbitrary across scenarios. A pre-run dry run gives the author the exact count before any cost is paid, which is both earlier and more precise.
- **Make all captures opt-in (no default evidence).** This is maximally cheap, but it removes the minimum safety net that DESIGN §10 requires: a failure must always leave a screenshot and an element dump. Keeping the lightweight trio plus the `result: error` net as the default preserves that guarantee while keeping the heavy kinds opt-in.

## Progress

- [x] `--explain` dry run (`bajutsu trace --explain`) — a static, deterministic preview of capture cost that flags a heavy capture on a broadly-matching rule ([#90](https://github.com/bajutsu-e2e/bajutsu/pull/90)).
- [x] Lighter default capture policy — the implicit `screenshot` + `elements` + `actionLog` trio, with interval-heavy kinds strictly opt-in ([#91](https://github.com/bajutsu-e2e/bajutsu/pull/91)).

## References

[DESIGN §11](../../DESIGN.md)
