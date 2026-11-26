**English** · [日本語](BE-XXXX-http-status-wait-ja.md)

# BE-XXXX — Wait for a status on the http step

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-http-status-wait.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md), [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) |
<!-- /BE-METADATA -->

## Introduction

The `http` step sends one request through the Hyper Text Transfer Protocol (HTTP) and moves on. This
item adds an optional `timeout`. With it, the step repeats the request until the response carries the
expected status.

## Motivation

A test often depends on work that a service finishes later. One example is a code that arrives
seconds after a button press. The `http` step of
[BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md) cannot wait for such work. A single request
sent too soon does not pass the check. A fixed pause goes against the determinism rule.

The `email` step of [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) does wait, but it
reads mail alone.

A scenario that waits for a slow service with no `sleep` step shows that the change arrived.

## Detailed design

### The `timeout` option

The `http` step gains an optional `timeout`, in seconds:

```yaml
- http:
    url: "${secrets.API}/codes/latest"
    status: 200
    timeout: 30
    saveBody: response
```

With `timeout`, the step repeats the request about once a second. It stops when the response
status equals `status`.

The rules are:

- `timeout` without `status` is a scenario load error.
- `timeout` requires the `GET` or `HEAD` method, because repeating another method can create data
  twice.
- A different status leads to the next attempt. A connection error fails the step at once.
- Each request may take the remaining time, up to 30 seconds.
- `saveBody` stores the body of the successful response.
- At the deadline the step fails and reports the last status it saw.
- A cancelled run leaves the wait, like every other condition wait.

Without `timeout`, the step behaves as it does today. The status is the sole success condition. A
service that answers 200 with an empty body before the data exists cannot use this wait.

The wait also has no notion of new data, unlike `email`.

A scenario avoids a stale value by asking
for something unique to the run. The `generate` step can build such an address.

### Prime directives

| Directive | How the design keeps it |
|---|---|
| No model on the verdict path | The step compares status numbers. |
| Determinism | The wait ends on the expected status or the required deadline. |
| App-agnostic | The scenario names the address and status, and the runner stays unchanged per app. |

### Work breakdown

| # | Work | Main files | Done when | Depends on |
|---|---|---|---|---|
| 1 | Add `timeout` to the model with its load checks | `http_request.py` | A scenario that breaks a rule stops loading | none |
| 2 | Add the wait to the step dispatch | `orchestrator/loop/`, `handlers/http.py` | Tests with a stub server and clock cover success, deadline, and cancellation | 1 |
| 3 | Update the documents in both languages | `docs/scenarios.md`, `docs/dsl-grammar.md` | Both pages describe the option and its limits | 2 |

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| A general retry block around several steps | The block could repeat interface actions, which widens the language far past the need. |
| Wait until the body matches a pattern | The request asked for the status alone. A later item can add the condition. |
| Require a server that holds the request until data exists | Many services offer no such call. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Model and load checks
- [ ] The wait itself
- [ ] Documents in both languages

## References

| Item | Why it matters here |
|---|---|
| [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md) | The origin of the `http` step |
| [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) | The existing condition wait with a required deadline |
