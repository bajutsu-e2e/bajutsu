**English** · [日本語](BE-0023-self-healing-guards-ja.md)

# BE-0023 — Guards against "making tests laxer"

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0023](BE-0023-self-healing-guards.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0023") |
| Implementing PR | predates the per-PR history (squashed into the initial import; no single PR) |
| Topic | Self-healing triage (M4) |
<!-- /BE-METADATA -->

## Introduction

This proposal addresses the risk that self-healing could weaken pass/fail criteria. A fix is **always reviewed by a human as a diff and explicitly applied with `--write`** (never auto-applied); a fragment mismatch is a safe no-op.

## Motivation

Self-healing has a well-known failure mode: in trying to keep tests green it quietly makes them weaker. A renamed id gets "fixed" to whatever is on screen, a timeout creeps up until a real slowness is masked, an assertion is loosened until it always passes. Each step looks harmless; together they hollow out the suite so it no longer catches regressions. Because Bajutsu's whole premise is that the deterministic gate is trustworthy, an unguarded fix mechanism would undermine the one thing the tool exists to protect. This proposal is the set of guards that lets self-healing propose fixes without ever being able to silently relax what a test checks.

## Detailed design

The guards are structural, not advisory:

* **No auto-apply, ever.** A fix is always surfaced first as a unified diff a human reads. Writing the file requires an explicit `--write`; the default is a dry run that changes nothing. Verification is the deterministic `run` (`--rerun`), not the model's say-so.
* **Constrained fix kinds.** A fix is one of `renameId` / `addIndex` / `raiseTimeout`, expressed as `find` -> `replace` over the scenario source. The model cannot emit an arbitrary rewrite; it can only rename a token, disambiguate a selector, or lengthen a wait — and `find` must be an exact substring of the source.
* **Fragment mismatch is a safe no-op.** If the `find` fragment no longer matches the source (the file moved on, or the fix was stale), `apply_fix` returns a replacement count of 0 and leaves the text untouched. The CLI reports the no-op; nothing is silently half-applied.
* **AI stays on the investigator side.** The model proposes; it never decides pass/fail. The verdict comes only from `expect` assertions in a deterministic `run`. No guard here ever loosens an assertion, because triage does not emit assertion-relaxing fixes in the first place — `raiseTimeout` lengthens a wait but does not change what is asserted, and there is no "weaken expectation" fix kind.

Together these mean the worst a fix can do is be reviewed and rejected. It cannot reach the committed YAML without a human reading the diff and opting in.

## Alternatives considered

* **Auto-apply with a confidence threshold.** Tempting, but any automatic write — however confident — can relax a constraint without review, which is exactly the failure mode this proposal exists to prevent. The human-in-the-loop diff is the guard; a threshold only moves the line, it does not hold it.
* **Allow assertion-weakening fixes behind a stronger warning.** Rejected. Loosening an expectation is the most direct way to make a test laxer, so no fix kind produces one; assertion failures get suggestions for a human, never an applicable fix.
* **Trust review alone, drop the no-op safety.** Without the fragment-mismatch no-op, a stale fix could partially apply or corrupt the file in surprising ways. Making mismatch a visible no-op keeps even a rejected or outdated fix from doing damage.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[DESIGN §11](../../DESIGN.md)
