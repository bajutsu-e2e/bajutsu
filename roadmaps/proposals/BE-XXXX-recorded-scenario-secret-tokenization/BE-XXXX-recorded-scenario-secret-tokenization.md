**English** · [日本語](BE-XXXX-recorded-scenario-secret-tokenization-ja.md)

# BE-XXXX — Tokenize secrets in recorded scenario YAML

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-recorded-scenario-secret-tokenization.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

`bajutsu record` writes the authored scenario verbatim, so any secret the authoring agent typed
into a field during exploration ends up as a plaintext literal in the scenario file. This proposal
tokenizes a recorded secret into `${secrets.*}` instead of writing the literal value.

## Motivation

`record` (`bajutsu/cli/commands/record.py:203`) writes the finished scenario with
`out_path.write_text(dump_scenarios([scenario]), encoding="utf-8")` — the exact `Step` objects the
record loop (`bajutsu/record.py`) accumulated, with no redaction or substitution pass in between.
The record loop appends a `type` step holding the literal text the agent entered
(`_describe_step`, `bajutsu/record.py:46`, reads `step.type.text`), including whatever an author
typed into a password or token field while demonstrating a login or API-key flow. `${secrets.*}`
(BE-0032) already gives a *run-time* author a token-only scenario, with the real value resolved
from the environment and masked out of evidence — but `record` sits upstream of that mechanism:
it captures the raw input as the human or the recording agent supplied it, before there is any
notion of a secret binding. So a scenario recorded against a real login writes the password or API
key into the YAML in plaintext, where it is reviewable by anyone with repo access, lands in commit
history if checked in, and is copied unredacted into any evidence or artifact that later includes
the scenario file.

Severity is Medium: unlike the TOTP-seed and network-secret leaks elsewhere in this report, this
is the *primary authoring path* for a scenario touching a real credential — `record` is how most
scenarios are created in the first place — so the exposure is the common case, not an edge case
that only shows up with an unusual step type.

## Detailed design

1. **Detect a bound secret value in the recorder's captured input.** Before a `type` (or other
   text-carrying) step is appended, compare its literal value against the resolved values of the
   `secrets:` declared for the target (the same environment-variable resolution `run` already does
   for `${secrets.X}`, `bajutsu/cli/commands/run.py:335-338`) — record has the same config
   available, so it can resolve the same bindings without adding a new secret-declaration surface.
2. **Write the token, not the literal, on a match.** When a captured value equals a resolved
   secret binding, substitute `${secrets.X}` for that value in the step before it is added to
   `scenario.steps`, so the file `record` writes already carries the token form BE-0032 expects —
   no separate post-processing pass over the finished YAML.
3. **Leave an unmatched value untouched.** A typed value that doesn't match any declared secret
   (most recorded input — taps, non-secret text) is recorded exactly as today; this proposal only
   changes behavior for input that already corresponds to a `secrets:`-declared binding, so it adds
   no new manual step for the common case.
4. **Surface the substitution to the author.** `record`'s existing progress narration (the `say`
   callback in `bajutsu/cli/commands/record.py`) reports when a step's text was tokenized, so an
   author reviewing the run's output knows a field was swapped for `${secrets.X}` rather than being
   silently rewritten.

## Alternatives considered

- **Redact recorded secrets after the fact, as a scrub pass over the written YAML.** Rejected as
  the wrong shape for this file: BE-0032's own post-write scrub (`_scrub_secret_values`) exists to
  catch a secret value that leaked into *result* text (an assertion's actual/expected) after the
  run already used the real value — but a scenario file's `type.text` is itself the source of
  truth for what the step does on the *next* run. Scrubbing it after the fact would either leave a
  literal in place (if not caught) or replace it with a placeholder that breaks replay (a redacted
  scenario can no longer type a working credential) unless it is rewritten as the working
  `${secrets.X}` token — which is what tokenizing at write time does directly.
- **Require the author to manually edit the recorded scenario to swap in `${secrets.*}`.**
  Rejected as the status quo being fixed: it depends on every author remembering to do this for
  every recorded credential, and a missed edit means the plaintext secret ships in the file exactly
  as it does today.
- **Refuse to record a step whose captured value matches a declared secret, forcing the author to
  restart with a placeholder.** Rejected: it breaks the recording flow (the agent needs the real
  credential to actually reach the authenticated screen it's demonstrating), and is strictly worse
  for the author than transparently tokenizing the value that was needed anyway.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Resolve the target's declared `secrets:` bindings inside `record`, matching `run`'s existing
      environment-variable resolution.
- [ ] Detect a captured step value that equals a resolved secret binding and substitute
      `${secrets.X}` for it before the step is added to the scenario.
- [ ] Leave non-matching captured input unchanged (no new behavior for the common case).
- [ ] Narrate a tokenization to the author via `record`'s existing progress output.
- [ ] Add a regression test covering a recorded secret ending up as `${secrets.X}`, not a literal,
      in the written scenario file.

No PR has landed yet.

## References

`bajutsu/cli/commands/record.py:203` (`out_path.write_text`), `bajutsu/record.py:46`
(`_describe_step` reading `step.type.text`), `bajutsu/cli/commands/run.py:335-338` (existing
`secrets:` environment resolution). Related: BE-0032 (secret variables), BE-0012 (action
capture / record), BE-0044 (scenario provenance). Originates from the 2026-07-02 codebase-analysis
report (security).
