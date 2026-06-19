**English** · [日本語](BE-0028-evidence-rule-overmatch-guard-ja.md)

# BE-0028 — Guard against over-matching evidence rules

* Proposal: [BE-0028](BE-0028-evidence-rule-overmatch-guard.md)
* Status: **Proposal**
* Track: [Proposals](../../README.md#proposals)
* Topic: Miscellaneous / on hold

## Introduction

Prevent artifacts from bloating due to over-matching capturePolicy (an `--explain` dry run, a lighter default policy).

## Motivation

A `capturePolicy` rule fires "every time" its trigger matches (DESIGN §9 A, `docs/evidence.md`). That is exactly the point — repeatable, deterministic evidence — but it also makes a too-broad rule expensive in a way that is invisible until after a run. A trigger like `{ action: tap, idMatches: "*.submit" }` reads as "the submit button," yet a loose glob, or an `event: screenChanged` rule on a busy flow, can match far more steps than the author intended. Attach a heavy capture (`video`, `network`) to such a rule and a run quietly produces gigabytes of artifacts, slows down, and risks the observer effect §9 warns about.

DESIGN §11 lists this directly as an open risk: "evidence rules over-matching, bloating artifacts → an `--explain` dry run and a lighter default policy." DESIGN §9's own cost-management note reinforces both halves: rules should narrow their match conditions, and the default should be the lightweight trio of `screenshot` + `elements` + `actionLog`, with `video` / `network` opt-in. The problem today is that nothing makes over-matching *visible before* a run, and nothing stops a heavy capture from silently becoming the effective default. This proposal addresses both: a way to preview firing, and a default that is cheap unless you ask for more.

## Detailed design

Two complementary guards, neither of which touches pass/fail.

- **A pre-run `--explain` dry run.** A command (e.g. `bajutsu trace --explain <scenario>`, as §11 names it) statically walks the scenario against its `capturePolicy`, reports for each rule how many times it would fire and on which steps, and flags heavy captures (`video` / `network`) on broadly-matching rules. This is the *pre-run* counterpart to the existing post-hoc `trace`: that one reads a completed run's manifest, this one previews what a run *would* capture so an author can tighten a glob before paying for it. It is read-only and deterministic — it resolves selectors and matches triggers the same way the run loop will, so its count is the real one, with no LLM involved.
- **A lighter default policy.** The implicit default capture stays the lightweight trio — `screenshot` + `elements` + `actionLog` — plus the `result: error` safety net that captures the maximum only when a step actually fails. Interval-heavy kinds (`video`, `deviceLog`, `network`) remain strictly opt-in, attached deliberately via an inline `capture:` or a narrowly-scoped rule. So the costly captures only ever run where an author asked for them, and the common case is cheap by construction.
- **Determinism and app-agnosticism preserved.** Neither guard changes what a run asserts: `--explain` is advisory output, and the default-policy choice only affects which evidence is gathered, never pass/fail. There are no fixed sleeps and selector strictness is unchanged. Any per-app capture preferences live under `apps.<name>` alongside other per-app settings, so the tool and runner stay app-agnostic, and the Tier-2 gate stays LLM-free.

## Alternatives considered

- **A hard cap on artifact size or count.** Aborting or truncating once a run exceeds a byte/file budget bounds the damage but discards evidence non-deterministically (the same scenario could capture different artifacts depending on timing), which conflicts with determinism-first. Previewing the firing count and defaulting to cheap captures prevents the bloat instead of reacting to it.
- **Warn at run time when a rule fires "too often."** A run-time heuristic is reactive — the artifacts are already written by the time the threshold trips — and a magic "too often" number is arbitrary across scenarios. A pre-run dry run gives the author the exact count before any cost is paid, which is both earlier and more precise.
- **Make all captures opt-in (no default evidence).** Maximally cheap, but it removes the minimum safety net DESIGN §10 requires — a failure must always leave a screenshot and element dump. Keeping the lightweight trio plus the `result: error` net as the default preserves that guarantee while keeping the heavy kinds opt-in.

## References

[DESIGN §11](../../../DESIGN.md)
