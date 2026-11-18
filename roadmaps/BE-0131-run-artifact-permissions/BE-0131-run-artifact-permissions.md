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

Bajutsu writes a run's evidence under a run directory. The evidence covers screenshots, the
accessibility-element dump, `network.json`, and a copy of the scenario. The directory is created
with whatever the process's default umask happens to be. On a typical `022` umask, that means `0755`
directories and `0644` files, which every other local account can read.

## Motivation

`bajutsu/evidence.py` creates the run's directory tree with plain `Path.mkdir` calls (for example
`step_dir.mkdir(parents=True, exist_ok=True)` and `scenario_dir.mkdir(parents=True,
exist_ok=True)`). It writes screenshots, element dumps, and interval recordings into the tree with
ordinary file writes. `bajutsu/network.py` dumps captured traffic to `network.json` the same way.

None of these call sites passes an explicit `mode`, so the resulting permissions are whatever the
process umask leaves. On most systems, that means world-readable by default.

This is a hardening gap rather than an exploited vulnerability. A run's artifacts can carry
sensitive data that Bajutsu deliberately protects elsewhere:

- `network.json` can contain request/response bodies and headers. Redaction applies by rule, but
  redaction is a best-effort denylist, not a guarantee against every future header or body shape.
- A screenshot, or the scenario's own evidence, can capture whatever secret-masked or unmasked text
  was on screen at capture time.

Severity is medium. Reading the run directory requires another local account on the same host. A
shared CI runner is exactly the environment where that adjacency shows up, though, and it is the
same threat model that BE-0032's secret masking already defends against for logs.

## Detailed design

Tighten the permissions the runner creates artifacts with, without touching what gets written or how
pass/fail is decided:

- Create the top-level run directory with mode `0700` (owner read/write/execute only) at the point it is first
  created. Every path underneath it then inherits a non-world-readable parent.
- Create files that may hold sensitive content with mode `0600` (owner read/write only) at write time. Those
  files are `network.json`, the copied scenario, the accessibility-element dump (`elements.json`),
  and screenshots. Relying on the directory permission alone to gate access is not enough.
- Apply the change uniformly across backends. idb and Playwright both write through the same
  `evidence.py` / `network.py` paths, so the fix lives once in the shared runner code, not per
  driver.
- Keep the change portable. `Path.mkdir(mode=...)` and `os.open` with an explicit mode (or
  `os.chmod` right after creation) work the same way on macOS and Linux, which is all the
  deterministic gate needs to support.

## Alternatives considered

- **Document the umask requirement instead of enforcing it in code.** Rejected. The approach
  depends on every operator and CI image setting a strict umask correctly. Bajutsu avoids exactly
  this kind of environment-dependent assumption elsewhere, because the tool is meant to behave the
  same regardless of the host's ambient config. Enforcing the mode at creation time makes the
  guarantee unconditional.
- **Encrypt artifacts at rest.** Rejected as disproportionate. The threat here is another local
  account reading a world-readable file, not a stolen disk. File-mode restriction addresses that
  threat directly, with no added complexity or key management.
- **Redact more aggressively instead of restricting permissions.** Rejected as complementary, not a
  substitute. Redaction reduces what a leaked artifact reveals, but it cannot be complete against
  every possible sensitive value. It therefore does not remove the need to also restrict who can
  read the artifact.

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
- Related: [BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md)
  (secret variables)
- Originates from the 2026-07-02 codebase-analysis report (security).
