**English** · [日本語](BE-XXXX-serve-uncaught-exception-json-ja.md)

# BE-XXXX — Return JSON errors for uncaught serve handler exceptions

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-uncaught-exception-json.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

The serve HTTP handler (`bajutsu/serve/handler.py`) dispatches each request through a `match path:`
statement that calls an operation and writes its `(payload, status)` as JSON. The dispatch is not
wrapped in a top-level `try/except`. When an operation raises — as opposed to returning an error
tuple — the exception propagates to Python's `socketserver`, which logs a traceback to the server
console and closes the connection **with no response body**. The browser then fails at
`await response.json()` with an opaque `Unexpected end of JSON input`, and the user sees a cryptic
message with no clue as to the real cause.

This item adds a single top-level exception boundary so any uncaught operation error becomes a
well-formed JSON 500 with a readable message, matching how the operations already return their own
handled errors.

## Motivation

Every serve operation follows the convention "return `({"error": …}, code)` for expected failures".
But device-backed operations reach code paths that *raise* rather than return: for example serve
Capture on a `backend: [ios]` target raises `ValueError: xcuitest backend requires a runner_port`
(the sibling actuator-selection proposal fixes that specific cause). Whatever the cause, the current
behavior is the worst possible: the client receives an empty response, the status line is unusable,
and the only record of the failure is a traceback in the operator's terminal.

This is a silent-failure pattern the project treats as a defect elsewhere (BE-0150 made the CLI fail
cleanly on malformed scenarios). The Web UI deserves the same guarantee: a failing request must
produce a response a human can read and a client can branch on. Because the boundary is one wrapper
around the existing dispatch, it is behavior-preserving for every request that already returns
normally — it only changes the outcome of the paths that currently crash.

No prime directive is affected: this is transport-layer error hygiene, with no bearing on the `run`
verdict, determinism, or app-specific configuration.

## Detailed design

1. **Wrap the POST dispatch.** Enclose the `match path:` body in `do_POST` in a `try/except` that, on
   any unhandled exception, responds `({"error": "<message>"}, 500)` via the existing `_json` helper.
   Keep the existing `json.JSONDecodeError` → 400 and the non-dict-body → 400 guards as the
   more-specific cases they already are.
2. **Wrap the GET dispatch.** Apply the same boundary to `do_GET`, so a read route (e.g. a scenario
   read or a capture screenshot) that raises also returns JSON 500 rather than an empty body — except
   for the streaming/binary routes (SSE, file/zip serving) that own their own response lifecycle and
   must not be double-written.
3. **Preserve the server-side traceback.** Still log the exception (with the request id already bound
   via `oplog`) so operators keep the diagnostic, while the client gets the sanitized message. Decide
   deliberately how much of the exception text is echoed to the client — enough to be actionable,
   without leaking internal paths where that matters.
4. **Tests.** A serve-handler test that an operation raising an exception yields a 500 with a JSON
   `error` body (not an empty response), for both a POST and a GET route, and that the streaming
   routes are left untouched.

## Alternatives considered

- **Fix each raising call site individually.** Necessary where the raise is itself a bug (the
  actuator-selection proposal does exactly that), but it cannot guarantee the *next* raising path is
  handled. A single boundary is the backstop that makes "an unexpected raise is still a readable 500"
  a structural property, not a per-site promise.
- **Let the framework's default handling stand.** `socketserver`'s default (log + drop) is acceptable
  for a crash in a batch process, but this is an interactive API whose sole client is a browser that
  parses every response as JSON; an empty body is a dead end for the UI.
- **Return HTML error pages.** The serve API is JSON end-to-end; an HTML 500 would break the client's
  uniform `response.json()` handling.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — top-level `try/except` around the POST dispatch → JSON 500.
- [ ] Unit 2 — same boundary for GET, excluding streaming/binary routes.
- [ ] Unit 3 — server-side traceback preserved; deliberate client message.
- [ ] Unit 4 — handler tests for POST and GET raise-paths, streaming untouched.

## References

- [BE-0150 — Fail cleanly on a malformed scenario in `trace --explain` and `audit`](../BE-0150-scenario-load-yaml-error-handling/BE-0150-scenario-load-yaml-error-handling.md) (the same clean-failure norm, for the CLI)
- [BE-0051 — Serve hardening for hosting](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) (the request gate this boundary sits inside)
- `bajutsu/serve/handler.py` (`do_POST` / `do_GET` dispatch)
