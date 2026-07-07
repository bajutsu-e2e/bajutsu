**English** · [日本語](BE-0155-idb-input-text-via-stdin-ja.md)

# BE-0155 — Pass idb input text via stdin to keep secrets out of argv

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0155](BE-0155-idb-input-text-via-stdin.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0155") |
| Implementing PR | [#615](https://github.com/bajutsu-e2e/bajutsu/pull/615), [#737](https://github.com/bajutsu-e2e/bajutsu/pull/737) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

The idb backend types text by putting the literal string on the `idb ui text` command
line. When that string is a secret variable (`${secrets.*}`) or a one-time password
resolved during authoring or a run, the value ends up in process argv — readable by
any other process on the same host for as long as the `idb` process lives.

## Motivation

`text_cmd` in `bajutsu/drivers/idb.py:78` builds the command as:

```python
def text_cmd(udid: str, text: str) -> list[str]:
    """The `idb` argv that types text into the focused field."""
    return ["idb", "ui", "text", "--udid", udid, text]
```

`text` is passed through unresolved from the step's `type_text` action, so a scenario
that types `${secrets.password}` or an OTP captured mid-run hands that value to
`subprocess.run` as a plain argv element. On Linux and macOS, process argv is visible
to any local user via `ps aux`, `/proc/<pid>/cmdline`, or equivalent — including other
jobs on a shared CI runner. Severity is medium: exploitation requires local access to
the same host while the `idb` process is alive, but CI runners are exactly the kind of
multi-tenant environment where "shared host, different job" is a realistic adjacency,
and secrets are the asset Bajutsu already goes out of its way to protect
(BE-0032 masks them in logs and evidence — this is the one path that still leaks the
raw value).

## Detailed design

Change how `type_text` gets the value to `idb`, not what the value is or how it is
resolved:

- Pass the text to `idb ui text` over stdin instead of argv, so the literal value
  never appears in the process's command line. This design assumes `idb`'s `ui text`
  subcommand reads from stdin when no positional text argument is given (falling back to
  argv only when stdin is empty), so this should be a drop-in change to how the
  subprocess is invoked, not a new idb capability.
- Confine the change to the idb backend: `text_cmd` stops taking `text` as an argv
  element, and the driver's `_run` call (or a small idb-specific wrapper) supplies the
  text as the subprocess's `input=` instead. The `Driver.type_text` interface (used by
  the runner and every backend) is unchanged — only idb's implementation of it changes.
- No change to secret resolution, masking, or the scenario schema — this closes the
  argv leak, it does not touch how `${secrets.*}` values are substituted or logged.

## Alternatives considered

- **Leave it on argv and rely on CI isolation.** Rejected: it depends on every CI
  environment being single-tenant, which Bajutsu cannot guarantee or verify from the
  driver layer, and the fix is cheap and idb-backend-local.
- **Redact the value in Bajutsu's own subprocess logging only.** Rejected: Bajutsu
  already avoids logging raw secrets; the leak here is the OS-level argv table, which
  application-side log redaction cannot mask.
- **Push the concern to the config/secrets layer (e.g. warn if a target types
  secrets).** Rejected: it would not fix the underlying exposure, only add friction;
  the direct fix (stdin) removes the leak outright with no behavior change for authors.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Keep the input text off `idb`'s argv. Done via the fb-idb gRPC client, not the CLI.
- [x] Add/adjust a driver-level test asserting the typed value never reaches a subprocess
      argv.

- First attempted via stdin (#615), then found broken. The design assumed `idb ui text`
  reads stdin when given no positional text argument. It does not: fb-idb 1.1.7 declares
  `text` as a required positional (`type=str`) with no stdin path, so `idb ui text --udid
  <udid>` with the value on stdin fails with `error: the following arguments are required:
  text` (exit 2) — meaning the stdin change silently broke *all* idb text input, surfacing
  as a `CalledProcessError` on any `type` action (found during a crawl demo). The
  deterministic gate missed it because the test injected a fake `_run_text` and never
  exercised the real `idb ui text` stdin behavior.
- Fixed by dropping the CLI for text entirely and calling the fb-idb gRPC client directly:
  `idb ui text` is only a thin wrapper over `client.text()`, so `IdbDriver.type_text` now
  routes through `_type_text_via_companion`, which opens a `ClientManager.from_udid` session
  and `await client.text(text=…)`. The value travels to `idb_companion` over gRPC and never
  lands on any command line — the original goal, now actually achieved. Verified on-device
  (booted simulator). `Driver.type_text` is unchanged; the driver test patches the
  `_type_text` class attribute and asserts the value reaches the companion path while no
  subprocess argv is built for it.

## References

- `bajutsu/drivers/idb.py` (`_type_text_via_companion`, `IdbDriver.type_text`)
- Related: [BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md)
  (secret variables), [BE-0035](../BE-0035-device-control-primitives/BE-0035-device-control-primitives.md)
  (device-control primitives)
- Originates from the 2026-07-02 codebase-analysis report (security).
