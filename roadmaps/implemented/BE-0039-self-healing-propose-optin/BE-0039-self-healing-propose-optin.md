**English** · [日本語](BE-0039-self-healing-propose-optin-ja.md)

# BE-0039 — Self-healing limited to "propose + opt-in apply"

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0039](BE-0039-self-healing-propose-optin.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Implementing PR | predates the per-PR history (squashed into the initial import; no single PR) |
| Topic | Candidates from competitive research (MagicPod / Autify) |
| Origin | Both |
<!-- /BE-METADATA -->

## Introduction

Both companies auto-correct selectors during a run. Bajutsu stays with the self-healing triage approach: propose a minimal diff, have a human review it, and apply it explicitly with `--write`. There is no implicit in-run correction, which guards against silently relaxing test constraints (see "making tests laxer" in [DESIGN §11](../../../DESIGN.md)).

## Motivation

Competitive research raised an obvious question: MagicPod and Autify both auto-correct selectors while a test runs, so should Bajutsu? The appeal is real — fewer red runs from cosmetic UI churn. But in-run auto-correction means a test can quietly change what it targets without anyone reviewing the change, which is precisely the "making tests laxer" risk Bajutsu treats as unacceptable (DESIGN §11). This item records the deliberate decision: Bajutsu adopts self-healing but bounds it to *propose + opt-in apply*, so the value (a suggested minimal fix) is kept and the cost (silent in-run drift) is refused.

## Detailed design

The mechanism is the self-healing triage already built (BE-0021/BE-0022/BE-0023), framed here as a deliberate scoping choice against the competitors' approach:

* **Propose, never auto-correct mid-run.** Triage runs *after* a failure, on the saved evidence — not inside the deterministic `run`. The run that failed stays failed; nothing is silently retargeted while it executes.
* **Opt-in apply.** A proposed fix is shown as a unified diff. It reaches the scenario source only on an explicit `--write`, and is verified by re-running the ordinary deterministic `run` (`--rerun`). The committed YAML never changes without a human reading the diff.
* **Bounded fixes.** The proposal is one of `renameId` / `addIndex` / `raiseTimeout`, a `find` -> `replace` whose `find` must be an exact substring of the source; a mismatch is a safe no-op.

The contrast with MagicPod/Autify is the whole point: they optimize for never showing a red run by correcting in place; Bajutsu optimizes for a trustworthy gate by keeping every correction visible and reviewed.

## Alternatives considered

* **Implicit in-run selector correction (the MagicPod/Autify approach).** Rejected. It removes the human from the loop and can relax a test's target without review, undermining the deterministic gate.
* **Auto-apply the proposed fix after the run.** Less dangerous than in-run correction but still writes to the committed scenario without a reviewer, so a wrong or laxer fix could land unnoticed. The opt-in `--write` keeps the human as the gate.
* **No self-healing at all.** Safe, but it throws away the genuine value of the competitors' feature — a concrete minimal fix for cosmetic breakage. Propose + opt-in apply keeps that value while refusing the silent-drift cost.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[Self-healing triage (M4)](../../README.md#self-healing-triage-m4)
