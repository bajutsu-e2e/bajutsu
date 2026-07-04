**English** · [日本語](BE-0131-run-artifact-permissions-ja.md)

# BE-0131 — Restrict run-artifact file permissions

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0131](BE-0131-run-artifact-permissions.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0131") |
| Implementing PR | [#630](https://github.com/bajutsu-e2e/bajutsu/pull/630) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

Bajutsu writes a run's evidence — screenshots, the accessibility-element dump,
`network.json`, and a copy of the scenario — under a run directory created with
whatever the process's default umask happens to be. On a typical `022` umask that
means `0755` directories and `0644` files: readable by every other local account.

## Motivation

`bajutsu/evidence.py` creates the run's directory tree with plain `Path.mkdir`
calls (e.g. `step_dir.mkdir(parents=True, exist_ok=True)`, `scenario_dir.mkdir(parents=True,
exist_ok=True)`) and writes screenshots, element dumps, and interval recordings into
it with ordinary file writes; `bajutsu/network.py` dumps captured traffic to
`network.json` the same way. None of these call sites pass an explicit `mode`, so the
resulting permissions are whatever the process umask leaves — world-readable by
default on most systems.

This is a hardening gap rather than an exploited vulnerability: a run's artifacts can
carry sensitive data that Bajutsu deliberately protects elsewhere — `network.json` can
contain request/response bodies and headers (redacted by rule, but redaction is a
best-effort denylist, not a guarantee against every future header or body shape), and
a screenshot or the scenario's own evidence can capture whatever secret-masked or
unmasked text was on screen at capture time. Severity is medium: it requires another
local account on the same host to read the run directory, but a shared CI runner is
exactly the environment where that adjacency shows up, and it's the same threat model
BE-0032's secret masking already defends against for logs.

## Detailed design

Tighten the permissions the runner creates artifacts with, without touching what gets
written or how pass/fail is decided:

- Create the top-level run directory `0700` (owner read/write/execute only) at the
  point it is first created, so every path underneath it inherits a non-world-readable
  parent.
- Create files that may hold sensitive content — `network.json`, the copied scenario,
  the accessibility-element dump (`elements.json`), and screenshots — `0600` (owner
  read/write only) at write time, rather than relying on the directory permission alone
  to gate access.
- Apply this uniformly across backends (idb and Playwright both write through the same
  `evidence.py` / `network.py` paths), so the fix lives once in the shared runner code,
  not per driver.
- Keep this portable: `Path.mkdir(mode=...)` and `os.open` with an explicit mode (or
  `os.chmod` right after creation) work the same way on macOS and Linux, which is all
  the deterministic gate needs to support.

## Alternatives considered

- **Document the umask requirement instead of enforcing it in code.** Rejected: it
  depends on every operator/CI image setting a strict umask correctly, which is exactly
  the kind of environment-dependent assumption Bajutsu avoids elsewhere (the tool is
  meant to behave the same regardless of the host's ambient config).
  Enforcing the mode at creation time makes the guarantee unconditional.
- **Encrypt artifacts at rest.** Rejected as disproportionate: the threat here is
  another local account reading a world-readable file, not a stolen disk; file-mode
  restriction addresses that threat directly with no added complexity or key
  management.
- **Redact more aggressively instead of restricting permissions.** Rejected as
  complementary, not a substitute: redaction reduces what a leaked artifact reveals,
  but it cannot be complete against every possible sensitive value, so it doesn't
  remove the need to also restrict who can read the artifact.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Create the run directory `0700`. The top-level run dir is created owner-only up
      front (`artifact_perms.make_run_dir`), so every subdirectory beneath it inherits a
      non-world-readable parent without a per-subdir chmod.
- [x] Write `network.json`, the copied scenario, `elements.json`, and screenshots with
      `0600` permissions (`artifact_perms.restrict_file`, called at each write site).
- [x] Add a test asserting the created run directory and its sensitive files have the
      restricted mode on a fresh run (`tests/test_artifact_perms.py`,
      `tests/runner/test_pipeline.py`, `tests/test_evidence.py`).

- Introduced `bajutsu/artifact_perms.py` (`make_run_dir` / `restrict_file`, chmod after write so
  the mode is umask-independent) and called it from the shared runner code
  (`runner/pipeline.py` for the run dir + `scenario.yaml` + `network.json`, `evidence.py` for
  screenshots and `elements.json`), so idb and Playwright are covered once. Documented the
  behavior in `docs/evidence.md` and its Japanese mirror.
- Review follow-up (PR #630): also restrict `elements.json` (the accessibility dump is in the
  item's scope); create the run dir owner-only at CLI dispatch, before the device pool can create
  anything under it (e.g. Playwright's `_video_tmp`), closing any world-readable window; and refuse
  a symlinked run directory in `make_run_dir` (the run id is a predictable timestamp, so a
  world-writable runs dir could otherwise redirect the `chmod`).

## References

- `bajutsu/evidence.py` (run/scenario/step directory creation and artifact writes)
- `bajutsu/network.py` (`network.json` output)
- Related: [BE-0032](../../implemented/BE-0032-secret-variables/BE-0032-secret-variables.md)
  (secret variables)
- Originates from the 2026-07-02 codebase-analysis report (security).
