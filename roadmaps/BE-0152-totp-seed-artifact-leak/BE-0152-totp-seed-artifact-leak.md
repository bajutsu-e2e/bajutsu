**English** · [日本語](BE-0152-totp-seed-artifact-leak-ja.md)

# BE-0152 — Keep literal TOTP seeds out of run artifacts

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0152](BE-0152-totp-seed-artifact-leak.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0152") |
| Implementing PR | [#607](https://github.com/bajutsu-e2e/bajutsu/pull/607) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

A scenario's `totp` step carries a literal TOTP seed in its `secret` field. Bajutsu copies that
scenario file verbatim into run artifacts. This proposal keeps the literal seed out of what the run
writes to disk and ships in an archive.

## Motivation

`Totp` (`bajutsu/scenario/models/actions.py:127-136`) models the `totp` step with `secret: str`, the
shared base32 key (commonly `${secrets.*}`). The step is deterministic and local (`bajutsu/totp.py`).
It computes an RFC 6238 code from the secret and the clock, with no network call. That is why the
scenario must hold the raw secret in the first place.

A run preserves the scenario file it executed as part of its evidence. Run artifacts
(`bajutsu/evidence.py`, the `report.html` / archive bundle) are a surface this repo already treats
as sensitive elsewhere. The sibling proposals for default network secret redaction and screenshot
secret warnings exist for the same class of leak.

The leak happens when `secret` is a literal string in the YAML. The alternative is to route it
through `${secrets.*}`, BE-0032's mechanism for keeping credentials out of the scenario file. The
run copies a literal seed into its artifact bundle unredacted. Anyone with access to the archive (a
shared CI artifact, a downloaded report) can then extract a durable TOTP seed, not a one-time code.

Severity is Low. `${secrets.*}` (BE-0032) already lets authors avoid a literal seed in the
scenario. A literal seed committed to a scenario file is itself a pre-existing anti-pattern,
independent of the copy-to-artifact step. But the tool neither warns nor redacts when it happens. A
scenario written with a literal `secret` for convenience during authoring silently carries the seed
into the artifacts of every later run.

## Detailed design

1. **Redact `totp.secret` when a scenario file is copied into `run_dir`.** The artifact writer
   snapshots the executed scenario (evidence-side, alongside `network.json` / `report.html`). Before
   writing the copy, it replaces the literal value of any `totp.secret` field with a fixed
   placeholder (e.g. `"<redacted>"`). This mirrors how `mask_secret` already redacts the API key
   preview in `serve`.
2. **Extend to the resolved-value case.** A scenario can use `${secrets.*}` for `totp.secret`. The
   interpolated (resolved) value must equally never reach an artifact. This task confirms that the
   existing secret-variable redaction (BE-0032) covers a resolved `totp.secret` the same way it
   covers other `${secrets.*}` uses, and closes the gap if it does not.
3. **Add a regression test.** The test asserts that a scenario snapshot in `run_dir` never contains
   a literal TOTP seed value, whether the author wrote it literally or it was resolved from
   `${secrets.*}`.

## Alternatives considered

- **Forbid a literal `secret` in `totp` steps (require `${secrets.*}`) at scenario validation
  time.** This is a complementary, stricter guard. It stays a possible follow-up, not the primary
  fix here. It changes accepted scenario syntax instead of just closing the artifact-copy leak, and
  existing scenarios with a literal seed would need migration. The redaction-on-copy step in this
  proposal protects existing authoring styles without a breaking schema change.
- **Redact all `Totp.secret`-shaped strings project-wide via a generic secret scanner.** Rejected
  as broader than needed. The model already identifies the field precisely (`Totp.secret`), so a
  targeted redaction at that one field is simpler. It also carries no false-positive risk on
  unrelated strings.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Redact `totp.secret` in the scenario snapshot copied into run artifacts.
- [x] Confirm (or extend) BE-0032's secret-variable redaction covers a resolved `totp.secret`.
- [x] Add a regression test covering both the literal and `${secrets.*}`-resolved cases.

- 2026-07-03: `redact_totp_secrets` masks a literal `totp.secret` seed in the evidence snapshot
  (`scenario.yaml` and the report's raw-YAML view / `manifest.json`), keeping a `${secrets.*}`
  reference intact; confirmed the run-level secret-value scrub (BE-0032) already covers the
  resolved value; added regression tests (unit + `run_and_report`) and bilingual evidence docs.

## References

`bajutsu/scenario/models/actions.py:127-136` (`Totp`), `bajutsu/totp.py`, `bajutsu/evidence.py`.
Related: BE-0046 (OTP/email steps), BE-0032 (secret variables). Originates from the 2026-07-02
codebase-analysis report (security).
