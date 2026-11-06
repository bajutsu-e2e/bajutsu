**English** · [日本語](BE-0303-xcuitest-live-real-grid-verification-ja.md)

# BE-0303 — Real-grid verification of the XCUITest live device-cloud route

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0303](BE-0303-xcuitest-live-real-grid-verification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0303") |
| Topic | Device-cloud execution |
<!-- /BE-METADATA -->

## Introduction

`WebDriverClient` and `XcuitestLiveDriver` in `drivers/xcuitest_live.py`
([BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md)) drive a
device-cloud route against Appium. The route speaks the World Wide Web Consortium (W3C) WebDriver
protocol and serves the
[BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md)
device provider seam. The [driver](../../docs/glossary.md#driver-backend-actuator-platform) module's
own docstring says both classes are "faked at the network boundary so no grid is needed on the gate".
Every test therefore drives a synthetic transport and never a real Appium grid.

No CI lane exercises the real wire format, the session lifecycle, or the `mobile:` command semantics
that this backend depends on. This item adds one.

## Motivation

A synthetic transport proves that `XcuitestLiveDriver` builds and parses whatever WebDriver JSON its
own tests hand it. That is real coverage of the driver's internal logic.

The synthetic transport proves nothing about three questions:

- Whether a real Appium server accepts that JSON.
- Whether session creation and teardown behave as the driver assumes under a real grid's timing.
- Whether the grid provider actually in use supports the `mobile:` extension commands the way the
  client code expects.

This route exists specifically as an opt-in path to real device-cloud hardware
([BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md)).
A mock of the very protocol it exists to speak cannot validate the one property that matters: that the
route works against a real grid.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A real Appium target for CI.** Stand up a real Appium server, or connect to an existing one. A
  local instance against a Simulator or emulator is enough to validate the WebDriver wire contract. A
  real cloud grid is a further, optional step once the local path is proven.
- **A key-gated or environment-gated live session test.** Drive `XcuitestLiveDriver` through a real
  session create → actuate → teardown cycle against that Appium target. Skip the test when no target is
  configured.
- **Cover the `mobile:` extension commands the driver actually uses**, not just the basic session
  lifecycle. Those commands are the ones most likely to diverge between Appium driver versions.
- **Non-gating first.** Land the new job as CI signal, following the precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md), before
  considering it required. A real Appium or grid dependency carries more setup and flakiness risk than
  the existing conformance suite.

## Alternatives considered

- **Trust the synthetic-transport tests, since the WebDriver JSON construction is unit-tested.**
  Correct JSON construction says nothing about whether a real Appium server accepts the JSON. That
  acceptance is the actual claim this backend makes.
- **Defer real verification until a concrete device-cloud provider adopts this route.** The route
  already exists and is reachable through the device provider seam today. If it stays unverified until
  a specific provider integration lands, any interim regression ships silently, because no lane exists
  to catch it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Stand up or connect to a real local Appium target for CI.
- [ ] Add a gated live session test (create → actuate → teardown) against it.
- [ ] Cover the `mobile:` extension commands the driver relies on.
- [ ] Wire it into CI as a non-gating signal.

## References

- [BE-0238 — iOS device-cloud execution](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md)
- [BE-0236 — Device-cloud provider abstraction](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/drivers/xcuitest_live.py` (`WebDriverClient`, `XcuitestLiveDriver`),
  `tests/test_xcuitest_live.py`
