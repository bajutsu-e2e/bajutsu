**English** · [日本語](BE-0049-determinism-flakiness-audit-ja.md)

# BE-0049 — Determinism / flakiness audit

* Proposal: [BE-0049](BE-0049-determinism-flakiness-audit.md)
* Author: [@0x0c](https://github.com/0x0c)
* Status: **Proposal**
* Track: [Proposals](../../README.md#proposals)
* Topic: Candidates from competitive research (Maestro)
* Origin: Maestro

## Introduction

A read-only diagnostic that *proves* a scenario's determinism instead of tolerating flakiness:
it detects non-deterministic outcomes by repeated execution under identical conditions, and
scores selector / wait stability statically. It is advisory and lives entirely outside the gate
— a divergence is reported as a finding, never silently passed and never used to decide a
verdict.

## Motivation

Bajutsu's central claim against competitors is that the gate is deterministic **by contract**,
not by absorption. Maestro markets the opposite stance — "built-in tolerance to flakiness", with
implicit auto-waits and tap retries that swallow instability, plus a `retry` command — so a flaky
test tends to be *hidden* rather than *fixed*. Bajutsu refuses that (no fixed `sleep`, an
ambiguous selector fails fast), but today the claim is **implicit**: nothing in the tool
quantifies or certifies determinism for a given suite, so a buyer has to take it on faith.

This item makes the differentiator tangible. It turns "we are deterministic" into a report a team
can run: execute a scenario K times and surface any step or assertion whose outcome or timing
varied; statically grade each selector against the stability ladder (a uniquely resolving `id`
beats `label` / `traits`, which beat `index` / raw coordinates) and flag waits with no timeout or
an over-loose condition. It is the natural extension of the `doctor` convention score
([BE-0024](../../proposals/BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)) from "are ids
well-named" to "is this suite reproducible".

**Boundary note (important).** This brushes against the roadmap's "Not adopting → Automatic retry
of failed tests" line, so the distinction must be explicit. That item is rejected because
retry-to-pass *hides* flakiness. This audit does the **opposite**: repeated runs are a *diagnostic
to detect and report* non-determinism. A divergent result is a finding to fix, not a retry that
turns red into green; the audit never changes a verdict and never feeds the run/CI gate. It
therefore strengthens determinism-first rather than straining it.

## Detailed design

Proposal altitude; the design constraint is that the audit is purely observational.

- **Repeat-and-diff (dynamic).** An `--audit` mode (or a `bajutsu audit` subcommand) runs a
  scenario `K` times under identical preconditions and compares outcomes: per-step pass/fail,
  the resolved selector target, and assertion results. Anything that varies across runs is
  reported as non-deterministic, with the diverging evidence (element trees / screenshots /
  network) attached. Classification is `deterministic` vs `flaky`, never a softened pass. This
  reuses the existing parallel-run and evidence machinery; it does not introduce a fixed sleep.
- **Stability score (static).** Parse the scenario and grade it without a device: score each
  selector on the stability ladder ([selectors.md](../../../docs/selectors.md)), flag `wait` steps
  missing a `timeout` or gated on an over-broad condition, and flag raw-coordinate gestures that
  a stable `id` could replace. Emit a per-scenario determinism score, parallel to `doctor`'s
  id-coverage score (`bajutsu/doctor.py`).
- **Output.** An advisory report (HTML / JSON) and an informational exit status. It is read-only
  with respect to the scenario and the verdict — it never edits a test and never gates CI.

Whether this lands as a new `audit` command or as flags on `doctor` / `run` is an
implementation choice deferred to adoption; the static half is a close cousin of `doctor` and
may simply extend it.

## Alternatives considered

* **Adopt flaky-test quarantine + automatic retry (the common industry answer / Maestro `retry`).**
  Rejected for the gate: it hides flakiness, which is exactly the property Bajutsu sells against.
  The audit deliberately inverts it — expose flake so it gets fixed.
* **Let an LLM judge whether a run "looks flaky".** Rejected: non-deterministic and unnecessary —
  divergence across identical runs is a precise, machine-detectable fact.
* **Do nothing (status quo).** Acceptable, but the strongest differentiator stays implicit and
  unprovable to a prospective adopter; a runnable certificate is cheap given the evidence and
  parallel-run machinery already exist.

## References

`bajutsu/doctor.py`, [selectors.md](../../../docs/selectors.md),
[BE-0024](../../proposals/BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md),
[roadmap → Not adopting](../../README.md#not-adopting-already-covered--out-of-scope),
[DESIGN §2 / §10](../../../DESIGN.md)
