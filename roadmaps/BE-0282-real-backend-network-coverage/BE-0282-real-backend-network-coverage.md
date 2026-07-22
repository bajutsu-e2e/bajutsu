**English** · [日本語](BE-0282-real-backend-network-coverage-ja.md)

# BE-0282 — Real-backend network capture, mock, and assertion coverage in CI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0282](BE-0282-real-backend-network-coverage.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0282") |
| Implementing PR | [#1183](https://github.com/bajutsu-e2e/bajutsu/pull/1183) (web slice), [#1267](https://github.com/bajutsu-e2e/bajutsu/pull/1267) (promote web job to gate) |
| Topic | Verification & coverage |
| Related | [BE-0020](../BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback.md), [BE-0027](../BE-0027-mock-server-external/BE-0027-mock-server-external.md), [BE-0003](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md) |
<!-- /BE-METADATA -->

## Introduction

The network path — capture, request/response mocking, and the request / event / sequence /
response-schema assertions — is fully modeled and unit-tested as pure logic, and its runtime is
wired for two backends: iOS captures out-of-process (the app POSTs each exchange to the
`NetworkCollector` on loopback), and web captures in-process (the Playwright driver hooks
`requestfinished` and fulfils mocks via `page.route`). No CI lane drives that runtime against a
real device or browser. Both web jobs pass `--no-network`, and the iOS network demo scenarios
(`network_live.yaml`, `network_mock.yaml`) are referenced by no Makefile target and no workflow.
This item wires the real network path into CI, web first (cheap on Linux) then iOS.

## Motivation

The network path is the project's single largest untested real-path surface. The pure matchers, the
one-to-one request assignment, the response-schema validation, and the sequence matching are
covered by unit tests, and the collector's HTTP receiver plus token authentication are exercised
as an in-process self-loop. What no test observes is the real boundary: the app-side sender
(the BajutsuKit POST), the `page.route` interception, the `requestfinished` timing relative to a
step, and the `mocked` provenance flag as recorded from a real stub rather than a model default.

Redaction of real network evidence is the sharpest instance. The redaction algorithms are
thoroughly unit-tested, but every input is a hand-built exchange dict or a hand-written log file.
Whether a secret in a *really captured* header or body is masked in the persisted evidence is
proven nowhere, because no lane produces such evidence. A regression that leaked a captured
credential would pass the whole suite.

The scenarios that would exercise the real path already exist for iOS and are orphaned; the web side needs
only that the existing jobs stop opting out with `--no-network`. So the cost is mostly wiring, not
new authoring. Android is a separate matter and stays out of scope: it has no native network
monitor (`observes_network_via_driver` is false and the adb driver declares no `NETWORK`
capability), so there is nothing to actuate there yet — this item records the Android gap
explicitly rather than skipping it silently.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Web network smoke.** Author (or adapt) a `demos/web` scenario with a request mock and a request
  assertion, run without `--no-network`, asserting that `page.route` intercepts the request, that
  `requestfinished` capture records the exchange, and that the mocked exchange is recorded with
  `mocked` set.
- **Web redaction of real evidence.** Extend that scenario so a captured exchange carries a secret
  in a header and body, and assert the persisted evidence masks it — closing the "redaction of
  real network evidence" gap on the cheap Linux lane.
- **iOS collector real path.** Connect `network_mock.yaml` / `network_live.yaml` as a non-gating
  `ios-e2e` job so the BajutsuKit → loopback POST → assertion + redaction chain runs on a real
  Simulator.
- **Record the Android gap.** Note explicitly, in the workflow and the coverage narrative, that
  Android network capture is out of scope pending a native monitor, so the absence reads as a
  deliberate boundary, not an oversight.
- **Start non-gating.** Land the new jobs as signal (not required checks) first, following the
  golden / visual precedent, and promote to required only once they prove stable.

## Alternatives considered

- **Keep `--no-network` everywhere and rely on unit tests.** The unit tests prove the pure logic
  and the collector's receiver, but never the app-side sender, the browser interception, or
  redaction of really captured evidence. The largest real-path gap would stay unobserved.
- **Use an external mock server ([BE-0027](../BE-0027-mock-server-external/BE-0027-mock-server-external.md))
  instead of driver-level interception.** That approach is a different layer (a standalone stub the
  app talks to) and complements this item rather than replacing it; it would not exercise the
  `page.route` / collector path this item is about.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Web network smoke: mock + request assert without `--no-network`, asserting interception, capture, and the `mocked` flag.
- [x] Web redaction of real captured evidence (secret in header/body masked in persisted evidence).
- [ ] iOS collector real path: connect `network_mock.yaml` / `network_live.yaml` as a non-gating job.
- [x] Record the Android network gap explicitly (out of scope pending a native monitor).
- [x] Land as signal first; promote to required only after it proves stable. (Web `network (playwright)` job promoted into the `E2E (web)` gate after proving stable in CI; iOS lane still to come.)

Log:

- [#1183](https://github.com/bajutsu-e2e/bajutsu/pull/1183) — web slice: added a Sync request to the demo app, `demos/web/scenarios/network.yaml` (mocked, captured `POST /api/sync` carrying a secret), a `fields: [password]` redact policy, `demos/web/network/assert_redaction.py`, the `make -C demos/web e2e-network` target, and the non-gating `network (playwright)` CI job. Android gap recorded in the workflow and `docs/architecture.md`. iOS collector real path deferred to a follow-up.
- [#1267](https://github.com/bajutsu-e2e/bajutsu/pull/1267) — promoted the `network (playwright)` job from signal into the required `E2E (web)` gate after it proved stable in CI (0 failures over the last 50 runs), the web twin of android-e2e.yml's already-gating `network (adb)`. Updated `docs/architecture.md` and its Japanese mirror in step.

## References

- [BE-0020 — Multi-backend evidence fallback](../BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback.md)
- [BE-0027 — External mock server](../BE-0027-mock-server-external/BE-0027-mock-server-external.md)
- [BE-0003 — codegen, traces, network & CI (M3)](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md)
- `bajutsu/network.py`, `bajutsu/web_network.py`, `demos/showcase/scenarios/network_live.yaml`, `demos/showcase/scenarios/network_mock.yaml`
