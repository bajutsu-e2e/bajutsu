**English** · [日本語](BE-0022-update-structured-fixes-ja.md)

# BE-0022 — `update` (minimal-diff proposals = applying structured fixes)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0022](BE-0022-update-structured-fixes.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0022") |
| Implementing PR | predates the per-PR history (squashed into the initial import; no single PR) |
| Topic | Self-healing triage |
<!-- /BE-METADATA -->

## Introduction

Update a broken scenario with a minimal diff instead of re-recording the whole thing. Triage proposes a structured fix (`renameId`/`addIndex`/`raiseTimeout`) → `--apply` (dry-run diff) / `--write` applies it to the source, `--rerun` verifies by re-running. The rename and addIndex closed loops are proven on a real device.

## Motivation

A small UI change — a renamed identifier, a newly duplicated control, a screen that loads a little slower — can break a scenario without invalidating it. Re-recording the whole scenario to fix one line is wasteful and, worse, throws away the hand edits a human has made to the YAML since. What you actually want is the smallest diff that makes the scenario deterministic again, presented for review like any other code change. BE-0021's triage already names the root cause; this proposal turns that diagnosis into a mechanically-applicable edit so the fix loop ends in a reviewable patch, not a fresh recording.

## Detailed design

Triage may attach a structured `Fix` to its diagnosis — a `find` -> `replace` over the scenario source, of one of three kinds:

* **`renameId`** replaces a selector id as a whole token. Negative lookarounds keep `nav.setting` from matching inside `nav.settings`, so it is safe to apply at every occurrence — the classic self-heal for a renamed identifier.
* **`addIndex`** rewrites the exact selector fragment of the failing step to add `index:` (or `within:`), disambiguating a selector that matched several elements.
* **`raiseTimeout`** rewrites the exact `timeout: N` fragment of a failing wait to a larger value.

`bajutsu triage --apply <file>` applies the fix to that source file. `apply_fix` is pure and returns the patched text plus a replacement count; the CLI shows a unified diff (`diff_fix`). By default this is a **dry run** — the diff is printed and nothing is written. `--write` is required to actually write the file, and `--rerun` (after `--write`, with `--app` and a device) re-runs the patched scenario through the ordinary deterministic `run` to confirm the fix holds. When `--apply` is given, triage diagnoses against the file's own text rather than the run's normalized dump, so a fragment fix's `find` matches the bytes the patch edits.

Nothing here touches pass/fail. The fix is an edit to the scenario source that a human reviews; the verification is the deterministic `run`, unchanged. The rename and addIndex closed loops (break → triage → apply → rerun → green) are proven on a real device.

## Alternatives considered

* **Re-record the whole scenario on breakage.** The original M4 fallback, but it discards human edits and produces a large, noisy diff for a one-line cause. Minimal structured fixes keep the patch small and reviewable.
* **Auto-apply the fix during the run.** This is what some competitors do (see BE-0039) and it is rejected: silently editing the scenario mid-run can relax constraints without anyone seeing it. Here every fix is a dry-run diff first and is written only on explicit `--write` (the guards are BE-0023).
* **Free-form text patches from the model.** Allowing arbitrary edits would let the model rewrite anything. Constraining fixes to three exact `find`/`replace` kinds, where `find` must be a substring of the source, makes a fix either apply cleanly or be a visible no-op — never a silent, broad rewrite.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[DESIGN §6.5](../../DESIGN.md), `bajutsu triage --apply`
