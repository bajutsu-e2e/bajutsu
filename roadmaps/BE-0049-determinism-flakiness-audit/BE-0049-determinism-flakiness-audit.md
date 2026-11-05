**English** · [日本語](BE-0049-determinism-flakiness-audit-ja.md)

# BE-0049 — Determinism / flakiness audit

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0049](BE-0049-determinism-flakiness-audit.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0049") |
| Implementing PR | [#285](https://github.com/bajutsu-e2e/bajutsu/pull/285) |
| Topic | Verification & coverage |
| Origin | Maestro |
<!-- /BE-METADATA -->

## Introduction

This item adds a read-only diagnostic that *proves* a scenario's determinism instead of tolerating flakiness. It does three things:

- It detects non-deterministic outcomes by repeated execution under identical conditions.
- It scores selector and wait stability statically.
- Once runs accumulate in a system of record, it tracks the same scenario's stability across its run history.

The diagnostic is advisory and lives entirely outside the gate. A divergence is reported as a finding. It is never silently passed and never used to decide a verdict.

## Motivation

Bajutsu's central claim against competitors is that the gate is deterministic **by contract**, not by absorption. Maestro markets the opposite stance: "built-in tolerance to flakiness". It uses implicit auto-waits and tap retries that swallow instability, plus a `retry` command. A flaky test therefore tends to be *hidden* rather than *fixed*. Bajutsu refuses that stance (no fixed `sleep`, and an ambiguous selector fails fast). Today, however, the claim is **implicit**. Nothing in the tool quantifies or certifies determinism for a given suite, so a buyer has to take it on faith.

This item makes the differentiator tangible. It turns "we are deterministic" into a report that a team can run. The report has three parts:

- It executes a scenario K times and surfaces any step or assertion whose outcome or timing varied.
- It statically grades each selector against the stability ladder: a uniquely resolving `id` beats `label` / `traits`, which beat `index` / raw coordinates.
- It flags waits with no timeout or an over-loose condition.

The item is the natural extension of the `doctor` convention score ([BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)), from "are ids well-named" to "is this suite reproducible".

Repeat-and-diff proves determinism *at a point in time*. Runs now accumulate in a system of record (the DB-backed run records from [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)), and that makes a second, longitudinal signal available: the same scenario's verdict over its own history.

"The same scenario" is meaningful only if each run is stamped with a **stable scenario identity** and the **versions of the moving parts**. Otherwise a verdict that flips is indistinguishable from a scenario that was edited, or an app or tool that changed. Today a run records its outcome but **no provenance**: no content hash of the executed scenario, no git revision, and no tool or app version. Only the executed `scenario.yaml` text is saved verbatim, plus `device_runtime` for the environment tab. The history therefore cannot yet be mined for flakiness. Stamping each run with that provenance is the cheap prerequisite that unlocks it.

**Boundary note (important).** This item brushes against the roadmap's "Not adopting → Automatic retry of failed tests" line, so the distinction must be explicit. That item is rejected because retry-to-pass *hides* flakiness. This audit does the **opposite**: repeated runs are a *diagnostic to detect and report* non-determinism. A divergent result is a finding to fix, not a retry that turns red into green. The audit never changes a verdict and never feeds the run/CI gate. It strengthens determinism-first rather than straining it.

## Detailed design

This section works at proposal altitude. The design constraint is that the audit is purely observational.

- **Repeat-and-diff (dynamic).** An `--audit` mode (or a `bajutsu audit` subcommand) runs a scenario `K` times under identical preconditions and compares outcomes: per-step pass/fail, the resolved selector target, and assertion results. Anything that varies across runs is reported as non-deterministic, with the diverging evidence (element trees / screenshots / network) attached. Classification is `deterministic` vs `flaky`, never a softened pass. This mode reuses the existing parallel-run and evidence machinery and introduces no fixed sleep.
- **Stability score (static).** This part parses the scenario and grades it without a device. It scores each selector on the stability ladder ([selectors.md](../../docs/selectors.md)). It flags `wait` steps missing a `timeout` or gated on an over-broad condition. It also flags raw-coordinate gestures that a stable `id` could replace. The output is a per-scenario determinism score, parallel to `doctor`'s id-coverage score (`bajutsu/doctor.py`).
- **Run provenance & version stamping (longitudinal prerequisite).** Each run is stamped in `manifest.json` and in the serve run record ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) 7c-4). The stamp carries:
  - a **scenario content hash**, a stable fingerprint of the executed `scenario.yaml`, which is already saved verbatim;
  - the **tool version** (`bajutsu.__version__`);
  - when resolvable, the **scenario's git revision** and the **app build id**. Both are optional, because not every run is under git or has a known build.

  The device model / runtime is already captured. With those stamps, a longitudinal view groups accumulated runs by content hash and reports pass-rate over time. It flags a scenario whose verdict flips **while its content hash (and ideally app/tool version) is unchanged**. That is true flakiness, separated from a scenario that was edited or an app that moved. The view reuses the accumulated run records. It adds fields, not a verdict.
- **Output.** The output is an advisory report (HTML / JSON) and an informational exit status. The audit is read-only with respect to the scenario and the verdict. It never edits a test and never gates CI.

Whether this lands as a new `audit` command or as flags on `doctor` / `run` is an implementation choice deferred to adoption. The static half is a close cousin of `doctor` and may simply extend it.

## Alternatives considered

* **Adopt flaky-test quarantine + automatic retry (the common industry answer / Maestro `retry`).** Rejected for the gate. It hides flakiness, which is exactly the property Bajutsu sells against. The audit deliberately inverts it: expose flake so it gets fixed.
* **Let an LLM judge whether a run "looks flaky".** Rejected. It is non-deterministic and unnecessary, because divergence across identical runs is a precise, machine-detectable fact.
* **Do nothing (status quo).** Acceptable, but the strongest differentiator stays implicit and unprovable to a prospective adopter. A runnable certificate is cheap, because the evidence and parallel-run machinery already exist.

## Progress

- [x] Stability score (static) and repeat-and-diff (dynamic) — both under `bajutsu audit`; advisory and read-only, never gating CI.
- [x] Run provenance & version stamping — each `manifest.json` carries a `provenance` block (`scenarioHash`, `toolVersion`, `gitRevision`).
- [x] Longitudinal view — `bajutsu audit --history` classifies each scenario (`flaky` / `deterministic` / `unproven`) by `(scenarioHash, name)`.
- Follow-up: stamp the same provenance onto the serve DB run record (tracked under [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)).

## References

- `bajutsu/doctor.py`, [selectors.md](../../docs/selectors.md)
- [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md): the DB-backed run records that the longitudinal view mines (run provenance is stamped onto them)
- [BE-0044](../BE-0044-scenario-provenance/BE-0044-scenario-provenance.md): a *different* provenance axis (step ↔ natural-language origin), not run versioning
- [roadmap → Not adopting](../README.md#not-adopting-already-covered--out-of-scope), [DESIGN §2 / §10](../../DESIGN.md)
