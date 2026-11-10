**English** · [日本語](BE-0120-recorded-scenario-secret-tokenization-ja.md)

# BE-0120 — Tokenize secrets in recorded scenario YAML

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0120](BE-0120-recorded-scenario-secret-tokenization.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0120") |
| Implementing PR | [#643](https://github.com/bajutsu-e2e/bajutsu/pull/643) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

`bajutsu record` writes the authored scenario verbatim. A secret that the authoring agent typed into a field during exploration thus ends up as a plaintext literal in the scenario file. This proposal tokenizes a recorded secret into `${secrets.*}` instead of writing the literal value.

## Motivation

`record` (`bajutsu/cli/commands/record.py:203`) writes the finished scenario with `out_path.write_text(dump_scenarios([scenario]), encoding="utf-8")`. The `Step` objects it writes come straight from the record loop (`bajutsu/record.py`). No redaction or substitution pass runs between the two.

The record loop appends a `type` step that holds the literal text the agent entered. `_describe_step` in `bajutsu/record.py:46` reads `step.type.text`. That text includes whatever an author typed into a password or token field while demonstrating a login or API-key flow.

`${secrets.*}` (BE-0032) already gives a *run-time* author a token-only scenario. The real value resolves from the environment and gets masked out of evidence. But `record` sits upstream of that mechanism. It captures the raw input as the human or the recording agent supplied it, before any notion of a secret binding exists.

A scenario recorded against a real login thus writes the password or API key into the YAML in plaintext. Anyone with repo access can read the value, and it lands in commit history if checked in. Any evidence or artifact that later includes the scenario file also copies the value unredacted.

The severity is Medium. Unlike the TOTP-seed and network-secret leaks elsewhere in this report, this leak sits on the *primary authoring path* for a scenario that touches a real credential. `record` creates most scenarios. The exposure is the common case, not an edge case that appears with an unusual step type.

## Detailed design

1. **Detect a bound secret value in the recorder's captured input.** Before `record` appends a `type` step (or another text-carrying step), it compares the step's literal value with the resolved values of the `secrets:` declared for the target.
   `run` already resolves these values from the environment for `${secrets.X}` (`bajutsu/cli/commands/run.py:335-338`). `record` has the same config available. It can resolve the same bindings without a new secret-declaration surface.
2. **Write the token, not the literal, on a match.** When a captured value equals a resolved secret binding, substitute `${secrets.X}` for the value in the step. Do this before the step enters `scenario.steps`. The file that `record` writes then carries the token form that BE-0032 expects. No separate post-processing pass over the finished YAML is needed.
3. **Leave an unmatched value untouched.** A typed value that matches no declared secret is recorded as today. Most recorded input falls here: taps and non-secret text. This proposal changes behavior for input that already corresponds to a `secrets:`-declared binding, and for nothing else. The common case gains no new manual step.
4. **Surface the substitution to the author.** `record`'s existing progress narration (the `say` callback in `bajutsu/cli/commands/record.py`) reports when it tokenized a step's text. An author reviewing the run's output then learns that a field became `${secrets.X}`, rather than being rewritten without notice.

## Alternatives considered

- **Redact recorded secrets after the fact, as a scrub pass over the written YAML.** Rejected as the wrong shape for this file. BE-0032's own post-write scrub (`_scrub_secret_values`) catches a secret value that leaked into *result* text (an assertion's actual or expected value) after the run had used the real value. A scenario file's `type.text` is different: it is the source of truth for what the step does on the *next* run.

  A scrub after the fact has two outcomes. If the scrub misses the value, a literal stays in place. If the scrub swaps in a placeholder, replay breaks, because a redacted scenario can no longer type a working credential. The scrub works if it rewrites the value as the working `${secrets.X}` token. Tokenizing at write time does that directly.
- **Require the author to edit the recorded scenario by hand to swap in `${secrets.*}`.** Rejected, because this is the status quo that the proposal fixes. The approach depends on every author remembering the edit for every recorded credential. A missed edit ships the plaintext secret in the file, as it does today.
- **Refuse to record a step whose captured value matches a declared secret, forcing the author to restart with a placeholder.** Rejected. The refusal breaks the recording flow, because the agent needs the real credential to reach the authenticated screen it demonstrates. It is also strictly worse for the author than transparent tokenizing of a value that was needed anyway.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Resolve the target's declared `secrets:` bindings inside `record`, matching `run`'s existing
      environment-variable resolution.
- [x] Detect a resolved secret binding occurring within a captured step's text and substitute
      `${secrets.X}` for each occurrence before the step is added to the scenario.
- [x] Leave non-matching captured input unchanged (no new behavior for the common case).
- [x] Narrate a tokenization to the author via `record`'s existing progress output.
- [x] Add a regression test covering a recorded secret ending up as `${secrets.X}`, not a literal,
      in the written scenario file.

Log:

- 2026-07-04: Tokenize a recorded secret at write time. `_secret_tokens`
  (`bajutsu/cli/commands/record.py`) resolves the target's declared `secrets:` from the
  environment (the reverse of `run`'s `_resolve_secrets`), and `record()` rewrites a matching
  `type` step's text to its `${secrets.X}` token before recording it, while still driving the app
  with the real value. The masking also covers the live progress narration — both the step line and
  the agent's free-text reasoning (`note`) — so no literal reaches the stream or the file.

## References

`bajutsu/cli/commands/record.py:203` (`out_path.write_text`), `bajutsu/record.py:46`
(`_describe_step` reading `step.type.text`), `bajutsu/cli/commands/run.py:335-338` (existing
`secrets:` environment resolution). Related: BE-0032 (secret variables), BE-0012 (action
capture / record), BE-0044 (scenario provenance). Originates from the 2026-07-02 codebase-analysis
report (security).
