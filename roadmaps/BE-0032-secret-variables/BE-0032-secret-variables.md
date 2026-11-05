**English** · [日本語](BE-0032-secret-variables-ja.md)

# BE-0032 — Secret variables

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0032](BE-0032-secret-variables.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0032") |
| Implementing PR | [#6](https://github.com/bajutsu-e2e/bajutsu/pull/6) |
| Topic | Scenario authoring features |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

Resolve `${secrets.X}` from environment variables for use in input, and automatically mask their real values in evidence. The mask extends the existing `redact` down to input values. Config declares the secrets under `secrets:`.

## Motivation

Real flows need real credentials: an API token, a password, or a one-time login. Hard-coding them into a scenario file makes the secret reviewable by anyone with repo access and leaks it into committed history. Worse, the same value would then surface in evidence (logs, the element tree, network exchanges, the manifest), where it is copied to disk and shared in reports.

Authors need a way to feed a secret into a step at run time. The value must never appear in a scenario, and it must not survive in the captured evidence.

## Detailed design

Config declares secret variable names under `secrets:`, a list of environment-variable names (`bajutsu/config.py`). At the start of a run, the CLI resolves each declared name `X` from the process environment into a binding `secrets.X → <value>` (`bajutsu/cli.py`). The scenario file only ever holds the token `${secrets.X}`, never the value.

Unlike `${params.*}` / `${row.*}`, which are expanded at load time, the run loop resolves `${secrets.X}` at action time. `_interp_step` (`bajutsu/orchestrator/`) makes a copy of the step with the secret bindings substituted just before the action executes, so only the live driver sees the real value. The original step is kept for the manifest and report, which therefore record the token. A fast substring/token check skips the copy for steps that contain no tokens.

Masking reuses and extends the existing `redact` machinery (`bajutsu/redaction.py`). A `Redactor` built with the literal secret values (`values=secret_values`) masks those values wherever they appear, replacing each with `[REDACTED]`. It covers free text (device log, app trace), the element tree, and network exchanges. Longest values are masked first, so a value that is a substring of another cannot leak a partial. This is value-based masking, so it catches a secret that the app echoes back into a log or response, which key-name redaction alone would miss.

As a final safety net, `_scrub_secret_values` (`bajutsu/runner/`) runs after the report is written. It passes every literal secret value over the run-level artifacts (`manifest.json`, `junit.xml`, `report.html`, `scenario.yaml`). That catches any secret that reached result text, such as an assertion's expected/actual.

Images (screenshots/video) cannot be text-masked and are left as-is, so a secret rendered on screen is the author's responsibility to avoid capturing.

Throughout, no LLM is involved. Resolution and masking are deterministic, so secrets never change the run/CI verdict.

## Alternatives considered

**Inline secret values in the scenario (or a side file of literals).** Rejected. The value would still be committed, reviewable, and copied into evidence. Resolving from the environment keeps the value out of version control entirely and leaves only a token in the file.

**Key-name redaction only (mask by configured header/field/label).** Rejected on its own. It cannot catch a secret that the app surfaces under an unexpected key, or one embedded inside a larger string in a log line. Masking the literal value wherever it appears closes that gap. Key-name redaction still applies for the general (non-secret) case.

**An encrypted secret store / vault integration.** Rejected as out of scope. The environment is the conventional, CI-native channel for credentials and needs no extra dependency or daemon. A vault could plug in later by populating the same environment variables, with no change to the scenario surface.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`bajutsu/interp.py` · `bajutsu/redaction.py`, [evidence.md](../../docs/evidence.md),
[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md) — which reuses
this secret-masking machinery for the hosted serve's operational logs.
