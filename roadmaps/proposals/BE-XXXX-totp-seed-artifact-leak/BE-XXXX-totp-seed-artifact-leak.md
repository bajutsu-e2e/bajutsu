**English** · [日本語](BE-XXXX-totp-seed-artifact-leak-ja.md)

# BE-XXXX — Keep literal TOTP seeds out of run artifacts

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-totp-seed-artifact-leak.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

A scenario's `totp` step carries a literal TOTP seed in its `secret` field, and that scenario
file is copied verbatim into run artifacts. This proposal keeps the literal seed out of what gets
written to disk and shipped in an archive.

## Motivation

`Totp` (`bajutsu/scenario/models/actions.py:127-136`) models the `totp` step as `secret: str` —
"the shared base32 key (commonly `${secrets.*}`)". The step is deterministic and local
(`bajutsu/totp.py`), computing an RFC 6238 code from the secret and the clock with no network
call, which is exactly why it needs the raw secret in the scenario in the first place. The
scenario file a run executes is preserved as part of the run's evidence, and run artifacts
(`bajutsu/evidence.py`, the `report.html` / archive bundle) are the surface this repo already
treats as sensitive elsewhere — the sibling proposals for default network secret redaction and
screenshot secret warnings exist for the same class of leak. When `secret` is written as a
literal string in the YAML (rather than routed through `${secrets.*}`, BE-0032's mechanism for
keeping credentials out of the scenario file itself), that literal seed is copied into the run's
artifact bundle unredacted, so anyone with access to the archive (a shared CI artifact, a
downloaded report) can extract a durable TOTP seed rather than a one-time code.

Severity is Low: `${secrets.*}` (BE-0032) already gives authors a way to avoid a literal seed in
the scenario, and a literal seed committed to a scenario file is itself a pre-existing anti-
pattern independent of the copy-to-artifact step. But the tool doesn't warn or redact when it
happens, so a scenario written with a literal `secret` for convenience during authoring silently
carries the seed into every subsequent run's artifacts.

## Detailed design

1. **Redact `totp.secret` when a scenario file is copied into `run_dir`.** The artifact writer
   that snapshots the executed scenario (evidence-side, alongside `network.json` /
   `report.html`) replaces the literal value of any `totp.secret` field with a fixed placeholder
   (e.g. `"<redacted>"`) before writing the copy, mirroring how `mask_secret` already redacts the
   API key preview in `serve`.
2. **Extend to the resolved-value case.** If a scenario uses `${secrets.*}` for `totp.secret`,
   the interpolated (resolved) value must equally never be written to an artifact; this task
   confirms the existing secret-variable redaction (BE-0032) already covers a resolved `totp.secret`
   the same way it covers other `${secrets.*}` uses, and closes the gap if it doesn't.
3. **Add a regression test** asserting a scenario snapshot in `run_dir` never contains a literal
   TOTP seed value, whether it was written literally or resolved from `${secrets.*}`.

## Alternatives considered

- **Forbid a literal `secret` in `totp` steps (require `${secrets.*}`) at scenario validation
  time.** Considered as a complementary, stricter guard; kept as a possible follow-up rather than
  the primary fix here, since it changes accepted scenario syntax rather than just closing the
  artifact-copy leak, and existing scenarios with a literal seed would need migration.
  This proposal's redaction-on-copy step protects existing authoring styles without a breaking
  schema change.
- **Redact all `Totp.secret`-shaped strings project-wide via a generic secret scanner.** Rejected
  as broader than needed: the field is precisely known at the model level (`Totp.secret`), so a
  targeted redaction at the one field is simpler and has no false-positive risk on unrelated
  strings.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Redact `totp.secret` in the scenario snapshot copied into run artifacts.
- [ ] Confirm (or extend) BE-0032's secret-variable redaction covers a resolved `totp.secret`.
- [ ] Add a regression test covering both the literal and `${secrets.*}`-resolved cases.

No PR has landed yet.

## References

`bajutsu/scenario/models/actions.py:127-136` (`Totp`), `bajutsu/totp.py`, `bajutsu/evidence.py`.
Related: BE-0046 (OTP/email steps), BE-0032 (secret variables). Originates from the 2026-07-02
codebase-analysis report (security).
