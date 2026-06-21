**English** · [日本語](BE-0032-secret-variables-ja.md)

# BE-0032 — Secret variables

* Proposal: [BE-0032](BE-0032-secret-variables.md)
* Author: [@0x0c](https://github.com/0x0c)
* Status: **Implemented**
* Implementing PR: [#6](https://github.com/bajutsu-e2e/bajutsu/pull/6)
* Track: [Accepted](../../README.md#accepted)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: MagicPod

## Introduction

Resolve `${secrets.X}` from environment variables for use in input, and automatically mask their real values in evidence (extending the existing `redact` down to input values). Declared in config under `secrets:`.

## Motivation

Real flows need real credentials — an API token, a password, a one-time login. Hard-coding those into a scenario file makes the secret reviewable by anyone with repo access and leaks it into committed history; worse, the same value would then surface in evidence (logs, the element tree, network exchanges, the manifest) where it is copied to disk and shared in reports. Authors need a way to feed a secret into a step at run time without ever writing its value into a scenario or letting it survive in the captured evidence.

## Detailed design

Secret variable names are declared in config under `secrets:` (a list of environment-variable names; `bajutsu/config.py`). At the start of a run the CLI resolves each declared name `X` from the process environment into a binding `secrets.X → <value>` (`bajutsu/cli.py`); the scenario file only ever holds the token `${secrets.X}`, never the value.

Unlike `${params.*}` / `${row.*}`, which are expanded at load time, `${secrets.X}` is resolved by the run loop at action time. `_interp_step` (`bajutsu/orchestrator/`) makes a copy of the step with the secret bindings substituted just before the action executes, so only the live driver sees the real value; the original step is kept for the manifest and report, which therefore record the token. A fast substring/token check skips the copy for steps that contain no tokens.

Masking reuses and extends the existing `redact` machinery (`bajutsu/redaction.py`). A `Redactor` is built with the literal secret values (`values=secret_values`), which it masks wherever they appear — free text (device log, app trace), the element tree, and network exchanges — replacing each with `[REDACTED]`; longest values are masked first so a value that is a substring of another cannot leak a partial. This is value-based masking, so it catches a secret the app echoes back into a log or response that key-name redaction alone would miss. As a final safety net, after the report is written `_scrub_secret_values` (`bajutsu/runner/`) passes every literal secret value over the run-level artifacts (`manifest.json`, `junit.xml`, `report.html`, `scenario.yaml`), catching any secret that reached result text such as an assertion's expected/actual. Images (screenshots/video) cannot be text-masked and are left as-is, so a secret rendered on screen is the author's responsibility to avoid capturing. Throughout, no LLM is involved — resolution and masking are deterministic, so secrets never change the run/CI verdict.

## Alternatives considered

**Inline secret values in the scenario (or a side file of literals).** Rejected: the value would still be committed, reviewable, and copied into evidence. Resolving from the environment keeps the value out of version control entirely and leaves only a token in the file.

**Key-name redaction only (mask by configured header/field/label).** Rejected on its own: it cannot catch a secret the app surfaces under an unexpected key, or one embedded inside a larger string in a log line. Masking the literal value wherever it appears closes that gap; key-name redaction still applies for the general (non-secret) case.

**An encrypted secret store / vault integration.** Rejected as out of scope: the environment is the conventional, CI-native channel for credentials and needs no extra dependency or daemon. A vault could plug in later by populating the same environment variables, with no change to the scenario surface.

## References

`bajutsu/interp.py` · `bajutsu/redaction.py`, [evidence.md](../../../docs/evidence.md),
[BE-0055](../../proposals/BE-0055-operational-logging/BE-0055-operational-logging.md) — which reuses
this secret-masking machinery for the hosted serve's operational logs.
