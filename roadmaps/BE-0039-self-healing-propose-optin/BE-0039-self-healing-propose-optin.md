**English** · [日本語](BE-0039-self-healing-propose-optin-ja.md)

# BE-0039 — Self-healing limited to "propose + opt-in apply"

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0039](BE-0039-self-healing-propose-optin.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0039") |
| Implementing PR | predates the per-PR history (squashed into the initial import; no single PR) |
| Topic | Self-healing triage |
| Origin | Both |
<!-- /BE-METADATA -->

## Introduction

Both companies auto-correct selectors during a run. Bajutsu stays with the self-healing triage approach. It proposes a minimal diff, a human reviews it, and `--write` applies it explicitly. There is no implicit in-run correction. This guards against silently relaxing test constraints (see "making tests laxer" in [DESIGN §11](../../DESIGN.md)).

## Motivation

Competitive research raised an obvious question. MagicPod and Autify both auto-correct selectors while a test runs, so should Bajutsu do the same? The appeal is real: fewer red runs caused by cosmetic UI churn. In-run auto-correction, however, means that a test can quietly change what it targets without anyone reviewing the change. That is precisely the "making tests laxer" risk that Bajutsu treats as unacceptable (DESIGN §11).

This item records a deliberate decision. Bajutsu adopts self-healing but bounds it to *propose + opt-in apply*, so the value (a suggested minimal fix) is kept and the cost (silent in-run drift) is refused.

## Detailed design

The mechanism is the self-healing triage that is already built (BE-0021 / BE-0022 / BE-0023). This item frames it as a deliberate scoping choice against the competitors' approach:

* **Propose, never auto-correct mid-run.** Triage runs *after* a failure, on the saved evidence. It does not run inside the deterministic `run`. The run that failed stays failed, and nothing is silently retargeted while it executes.
* **Opt-in apply.** A proposed fix is shown as a unified diff. It reaches the scenario source only on an explicit `--write`. The ordinary deterministic `run` (`--rerun`) then verifies it by re-running. The committed YAML never changes without a human reading the diff.
* **Bounded fixes.** The proposal is one of `renameId` / `addIndex` / `raiseTimeout`, expressed as a `find` -> `replace` whose `find` must be an exact substring of the source. A mismatch is a safe no-op.

The contrast with MagicPod and Autify is the whole point. They optimize for never showing a red run by correcting in place. Bajutsu optimizes for a trustworthy gate by keeping every correction visible and reviewed.

## Alternatives considered

* **Implicit in-run selector correction (the MagicPod/Autify approach).** Rejected. It removes the human from the loop, and it can relax a test's target without review, which undermines the deterministic gate.
* **Auto-apply the proposed fix after the run.** Auto-applying after the run is less dangerous than in-run correction. It still writes to the committed scenario without a reviewer, so a wrong or laxer fix could land unnoticed. The opt-in `--write` keeps the human as the gate.
* **No self-healing at all.** This is safe, but it throws away the genuine value of the competitors' feature: a concrete minimal fix for cosmetic breakage. Propose + opt-in apply keeps that value and refuses the cost of silent drift.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[Self-healing triage](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html) (roadmap dashboard)
