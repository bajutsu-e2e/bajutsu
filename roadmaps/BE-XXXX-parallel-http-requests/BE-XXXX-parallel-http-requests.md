**English** · [日本語](BE-XXXX-parallel-http-requests-ja.md)

# BE-XXXX — Send several http requests at the same time from one scenario step

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-parallel-http-requests.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md), [BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md), [BE-0437](../BE-0437-multi-target-step-groups/BE-0437-multi-target-step-groups.md), [BE-0515](../BE-0515-http-status-wait/BE-0515-http-status-wait.md), [BE-0516](../BE-0516-parse-json-step/BE-0516-parse-json-step.md) |
<!-- /BE-METADATA -->

## Introduction

A scenario runs its steps one after another. The one form of parallelism today is `--workers`. It
runs whole scenarios side by side. Scenarios share no variables. No scenario can send two requests
together and compare the answers.

This item adds a `parallel` step. The step holds a list of `http` steps and sends them together.
It waits until every request has finished. Each request keeps its own result in a variable. The run
record keeps the moment each request started.

An optional `statusCounts` check names the expected set of statuses. It never says which request
receives which status.

## Motivation

Some defects appear when two requests meet. A backend must handle each of these cases:

- A double-submitted order creates one order. The other request gets a conflict status.
- Two simultaneous updates to one record leave one winner and one conflict (optimistic locking).
- A rate limiter turns away the requests that exceed its quota within a short window.
- Requests from two users, sent together, never receive each other's data.

A scenario cannot test any of these today. Two scenarios under `--workers` start at unrelated times.
They have no way to compare results. A lifecycle script can send the requests together.
Then the results and the checks live outside the scenario, away from its variables and its record.

After this item ships, a scenario posts one order twice in a single `parallel` step. The scenario
passes when the two responses are one 201 and one 409, in either order. The run record lists both
exchanges with their start offsets. A reader can then see that the requests overlapped.

## Detailed design

### The `parallel` step

```yaml
- parallel:
    steps:
      - http: { method: POST, url: "${secrets.API}/v1/orders", body: '{"key":"k1"}', saveStatus: first }
      - http: { method: POST, url: "${secrets.API}/v1/orders", body: '{"key":"k1"}', saveStatus: second }
    statusCounts: { 201: 1, 409: 1 }
```

`parallel` takes a `steps` list and an optional `statusCounts`. The load rules are:

| Case | Result |
|---|---|
| A child that is not an `http` step | Load error, because an interface action has no place in a race |
| A child with a step modifier besides `name` and `from`, such as `extract`, `capture`, or `target` | Load error, because `extract` and `capture` read the screen, and the children run against the `parallel` step's target |
| A child with `timeout` | Load error, because a repeating wait blurs when each request starts |
| Fewer than two children, or more than 32 | Load error, with 32 as a guard that a later item can raise |
| Two saves naming one variable, within one child or across children | Load error, because the final value would depend on timing |
| `statusCounts` that sum to a different number than the children | Load error, because the check must cover every child |

The `parallel` step itself follows the ordinary `target` rule. The target checks resolve its
children to that target. A target group
([BE-0437](../BE-0437-multi-target-step-groups/BE-0437-multi-target-step-groups.md)) resolves its steps to its own target in the same way.

A `parallel` step may sit wherever a step may sit, such as inside `if`, `forEach`, or a component.

### Saving the status

The `http` step gains an optional `saveStatus`. It stores the response status as decimal text in
`vars.<name>`, beside `saveBody`. A step without `status` accepts any status. With `saveStatus`, a
later step can read a status the scenario did not predict. The option works on a plain `http` step
too. `saveStatus` and `saveBody` on one step must name different variables. The model refuses the
same name at load time.

### Running the children together

When the step starts, the runner substitutes every `${...}` token in every child. A child never reads
a variable that a sibling saves. The runner then prepares all requests and releases them through one
start gate. The requests leave within a short time of each other. Each request keeps the 30-second
ceiling of the `http` step. The step waits until every child has finished.

The runner stores the results after the last child finishes, in the order the scenario lists them.
The final variables never depend on which request finished first. A value read from a `${secrets.*}`
token stays masked, as it does for a single `http` step.

The step passes when all three conditions hold:

- Every request received a response.
- Every child that sets `status` received that status.
- The received statuses match `statusCounts` as a multiset, when the step sets it.

Otherwise the step fails, and it fails after every child has finished. The reason lists each child
by position with its status or the kind of its error. The kinds are a disallowed protocol, a
request error, and a timeout. The reason adds the number of responses for each status.

The reason never quotes an error message, a Uniform Resource Locator (URL), or a body. Any of them
may carry a secret or a server name.

A cancelled run leaves the wait at once. A request already in flight ends within its own ceiling.
The runner discards that result.

Like `sleep` and a `timeout` wait, the run loop dispatches the step itself, not an action handler.
The step thus receives the run's clock and its cancel source. The offsets in the run record come
from that clock.

### Evidence of the overlap

The run record gains one entry per child. The entry holds the child's position and its start offset
from the step's start. It also holds the duration, and the status or error.
The report shows the entries under the step. Nothing on the verdict path reads the offsets. They serve a reader who asks whether the requests
overlapped.

### What the documentation must say

The server decides which request wins, and the runner cannot influence that choice.

A scenario checks a property that holds in every order. One 201 and one 409 is such a property.
A scenario never names the winner. The documentation says this beside the first example.

The documentation also states two limits:

- Two requests sent together may reach a server that handles them one at a time. A passing run shows
  that the property held for the interleaving it saw. It does not prove that the race is impossible.
- A property that sometimes breaks under load points at the service, not at the scenario.

### Prime directives

| Directive | How the design keeps it |
|---|---|
| No model on the verdict path | The step compares status numbers and counts. |
| Determinism | The verdict depends on the set of results, never on arrival order. |
| App-agnostic | The scenario names addresses and statuses, and the runner stays unchanged per app. |

The determinism rule governs waits and selectors. It does not promise that a target acts the same
way on every run. A race test exists to probe that behavior. The step keeps its own part
deterministic. It uses no fixed pause, it bounds the wait, and its verdict ignores arrival order.

### Work breakdown

| # | Work | Main files | Done when | Depends on |
|---|---|---|---|---|
| 1 | Add `saveStatus` to the `http` step | `http_request.py`, `handlers/http.py` | A test shows the status in `vars.*` for 200, 404, and 500 | none |
| 2 | Add the `parallel` model and its load checks | `scenario/models/actions/`, `scenario/models/steps/step.py`, `scenario/models/scenario/_targets.py`, `scenario/expand.py`, `analysis/audit/_functions.py`, `capability/capability_preflight.py`, `orchestrator/evidence_rules.py`, `orchestrator/loop/_step_runner.py` | Each load rule has a scenario that fails to load, and every walker over nested steps visits `parallel.steps` | 1 |
| 3 | Run the children together and record the exchanges | `orchestrator/loop/_functions.py`, `orchestrator/actions/handlers/`, `orchestrator/types/step_outcome.py`, `report/manifest.py`, `report/load.py`, the report renderer | A stub server that holds each request until all arrive lets the step pass | 2 |
| 4 | Update the documents and the codegen label in both languages | `docs/scenarios.md`, `docs/dsl-grammar.md`, `docs/architecture.md`, `codegen/common/_functions.py`, `codegen/xcuitest.py`, `codegen/playwright.py`, `codegen/uiautomator.py` | The three pages describe the step, and each emitter writes a labeled `// TODO` for `parallel` | 3 |

The stub server in work item 3 proves the overlap without timing the runner. The stub holds each
request until all have arrived. When the release window ends first, the stub answers with 504 as
its status. The scenario does not expect that status, so a sequential runner fails. The release window is a generous fixed
bound. It stays well above the gap between two gated requests on a loaded machine. A slow runner
thus does not flake the test.

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Send the requests from a lifecycle script | The results and checks leave the scenario, so its variables and record never see them |
| Run multiple scenarios under `--workers` | The start times differ, and no step can compare the results |
| A `parallel` option on `forEach` | It would run interface actions side by side too, which no driver supports |
| Name the winner, as in "the first request gets 201" | The server picks the winner, so such a check would break at random |
| Keep this as an unsorted idea until demand appears | The design avoids the determinism conflict, so waiting adds nothing |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `saveStatus` on the `http` step
- [ ] The `parallel` model and load checks
- [ ] Concurrent runs and the exchange record
- [ ] Documents and codegen label in both languages

## References

| Item | Why it matters here |
|---|---|
| [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md) | The origin of the `http` step |
| [BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) | The scenario-level parallel runs that this item does not replace |
| [BE-0515](../BE-0515-http-status-wait/BE-0515-http-status-wait.md) | The `timeout` wait that a child may not use |
| [BE-0516](../BE-0516-parse-json-step/BE-0516-parse-json-step.md) | The step that reads a field from a saved body after the race |
