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

A broken scenario is updated with a minimal diff, not re-recorded whole. Triage proposes a structured fix (`renameId`, `addIndex`, or `raiseTimeout`). `--apply` shows the dry-run diff, and `--write` applies the fix to the source. `--rerun` verifies the result by running the scenario again. The rename and addIndex closed loops are proven on a real device.

## Motivation

A small UI change can break a scenario without invalidating it. Examples are a renamed identifier, a newly duplicated control, and a screen that loads a little slower. Re-recording the whole scenario to fix one line is wasteful. Worse, it throws away the hand edits that a human has made to the YAML since.

The goal is the smallest diff that makes the scenario deterministic again, presented for review like any other code change. The triage of BE-0021 already names the root cause. This proposal turns that diagnosis into a mechanically applicable edit. The fix loop then ends in a reviewable patch, not a fresh recording.

## Detailed design

Triage may attach a structured `Fix` to its diagnosis. A `Fix` is a `find` -> `replace` over the scenario source, of one of three kinds:

* **`renameId`** replaces a selector id as a whole token. Negative lookarounds keep `nav.setting` from matching inside `nav.settings`, so the fix is safe at every occurrence. It is the classic self-heal for a renamed identifier.
* **`addIndex`** rewrites the exact selector fragment of the failing step to add `index:` (or `within:`). The added field disambiguates a selector that matched several elements.
* **`raiseTimeout`** rewrites the exact `timeout: N` fragment of a failing wait to a larger value.

`bajutsu triage --apply <file>` applies the fix to that source file. `apply_fix` is pure and returns the patched text plus a replacement count. The CLI shows a unified diff (`diff_fix`).

By default `--apply` is a **dry run**: the diff is printed and nothing is written. Writing the file requires `--write`. After `--write`, `--rerun` (with `--app` and a device) runs the patched scenario through the ordinary deterministic `run`, which confirms the fix holds.

When `--apply` is given, triage diagnoses against the file's own text, not the run's normalized dump. A fragment fix's `find` then matches the bytes that the patch edits.

Nothing here touches pass or fail. The fix is an edit to the scenario source that a human reviews. The verification is the deterministic `run`, unchanged. The rename and addIndex closed loops (break, triage, apply, rerun, green) are proven on a real device.

## Alternatives considered

* **Re-record the whole scenario on breakage.** This was the original M4 fallback. It discards human edits and produces a large, noisy diff for a one-line cause. Minimal structured fixes keep the patch small and reviewable.
* **Auto-apply the fix during the run.** Some competitors do this (see BE-0039). Rejected: silently editing the scenario mid-run can relax constraints without anyone seeing it. Here every fix is a dry-run diff first, and it is written only on an explicit `--write` (the guards are BE-0023).
* **Free-form text patches from the model.** Arbitrary edits would let the model rewrite anything. Fixes are constrained to three exact `find`/`replace` kinds, and `find` must be a substring of the source. A fix therefore either applies cleanly or is a visible no-op, never a silent, broad rewrite.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[DESIGN §6.5](../../DESIGN.md), `bajutsu triage --apply`
