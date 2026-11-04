**English** · [日本語](BE-0264-serve-uncaught-exception-json-ja.md)

# BE-0264 — Return JSON errors for uncaught serve handler exceptions

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0264](BE-0264-serve-uncaught-exception-json.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0264") |
| Implementing PR | [#1089](https://github.com/bajutsu-e2e/bajutsu/pull/1089) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

The serve HTTP handler (`bajutsu/serve/handler.py`) dispatches each request through a `match path:`
statement. The statement calls an operation and writes the operation's `(payload, status)` as JSON.
No top-level `try/except` wraps the dispatch.

When an operation raises an exception, as opposed to returning an error tuple, the exception
propagates to Python's `socketserver`. The `socketserver` logs a traceback to the server console and
closes the connection **with no response body**. The browser then fails at `await response.json()`
with an opaque `Unexpected end of JSON input`. The user sees a cryptic message with no clue to the
real cause.

This item adds a single top-level exception boundary. Any uncaught operation error then becomes a
well-formed JSON 500 with a readable message. That matches how the operations already return their
own handled errors.

## Motivation

Every serve operation follows the convention "return `({"error": …}, code)` for expected failures".
Device-backed operations, however, reach code paths that *raise* instead of returning. For example,
serve Capture on a `backend: [ios]` target raises `ValueError: xcuitest backend requires a
runner_port`. The sibling actuator-selection proposal fixes that specific cause.

Whatever the cause, the current behavior is the worst possible. The client receives an empty
response. The status line is unusable. The only record of the failure is a traceback in the
operator's terminal.

The project treats this silent-failure pattern as a defect elsewhere: BE-0150 made the CLI fail
cleanly on malformed scenarios. The Web UI deserves the same guarantee. A failing request must
produce a response that a human can read and a client can branch on.

The boundary is one wrapper around the existing dispatch. It is therefore behavior-preserving for
every request that already returns normally. It changes the outcome of the paths that currently
crash, and nothing else.

No prime directive is affected. The change is transport-layer error hygiene, with no bearing on the
`run` verdict, determinism, or app-specific configuration.

## Detailed design

1. **Wrap the POST dispatch.** Enclose the `match path:` body in `do_POST` in a `try/except`. On any
   unhandled exception, the handler responds `({"error": "<message>"}, 500)` through the existing
   `_json` helper. The existing `json.JSONDecodeError` → 400 and non-dict-body → 400 guards stay, as
   the more specific cases they already are.
2. **Wrap the GET dispatch.** Apply the same boundary to `do_GET`. A read route that raises, such as
   a scenario read or a capture screenshot, then returns JSON 500 instead of an empty body. The
   streaming and binary routes (SSE, file and zip serving) are excluded. Those routes own their own
   response lifecycle and must not be double-written.
3. **Preserve the server-side traceback.** Keep logging the exception, with the request id already
   bound through `oplog`. Operators keep the diagnostic, while the client gets the sanitized message.
   Decide deliberately how much of the exception text the client sees: enough to be actionable,
   without leaking internal paths where that matters.
4. **Tests.** Add serve-handler tests for two behaviors:
   - An operation that raises an exception yields a 500 with a JSON `error` body, not an empty
     response. The test covers both a POST route and a GET route.
   - The streaming routes are left untouched.

## Alternatives considered

- **Fix each raising call site individually.** This is necessary where the raise is itself a bug,
  and the actuator-selection proposal does exactly that. It cannot guarantee that the *next* raising
  path is handled, though. A single boundary is the backstop that makes "an unexpected raise is
  still a readable 500" a structural property, not a per-site promise.
- **Let the framework's default handling stand.** The `socketserver` default (log and drop) is
  acceptable for a crash in a batch process. This is an interactive API whose sole client is a
  browser that parses every response as JSON, and an empty body is a dead end for the UI.
- **Return HTML error pages.** The serve API is JSON end to end. An HTML 500 would break the client's
  uniform `response.json()` handling.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — top-level `try/except` around the POST dispatch → JSON 500.
- [x] Unit 2 — same boundary for GET, excluding streaming/binary routes.
- [x] Unit 3 — server-side traceback preserved; deliberate client message.
- [x] Unit 4 — handler tests for POST and GET raise-paths, streaming untouched.

Log:

- [#1089](https://github.com/bajutsu-e2e/bajutsu/pull/1089) changed `bajutsu/serve/handler.py`.
  - `do_POST` and `do_GET` now dispatch through `_dispatch_post` and `_dispatch_get`. A top-level
    `try/except` wraps both. It turns any uncaught operation exception into a JSON 500 through
    `_respond_uncaught`. The full traceback is logged with the `oplog` request id, and the client
    gets the exception message alone.
  - The streaming and binary GET routes (SSE, run file, zip, screenshot) are split into
    `_serve_streaming_get`. They dispatch outside the boundary, so a fallback `_json` cannot
    double-write a response that is already on the wire.
  - `_respond_uncaught`'s own write is guarded, so a client disconnect cannot re-propagate the
    empty-body drop.
  - Tests are in `tests/serve/test_http_uncaught.py`.
- [#1089](https://github.com/bajutsu-e2e/bajutsu/pull/1089) (review follow-up) extended the boundary
  to the raw-body upload and artifact routes (`_handle_upload` and `_handle_artifact_upload`). Those
  routes dispatch before `do_POST`'s `try/except`. A raise from `ops.bind_upload_config` or
  `ops.bind_artifact` now goes through `_respond_uncaught` too. These routes no longer hit the
  empty-body drop that this item exists to eliminate.

## References

- [BE-0150 — Fail cleanly on a malformed scenario in `trace --explain` and `audit`](../BE-0150-scenario-load-yaml-error-handling/BE-0150-scenario-load-yaml-error-handling.md) (the same clean-failure norm, for the CLI)
- [BE-0051 — Serve hardening for hosting](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) (the request gate this boundary sits inside)
- `bajutsu/serve/handler.py` (`do_POST` / `do_GET` dispatch)
