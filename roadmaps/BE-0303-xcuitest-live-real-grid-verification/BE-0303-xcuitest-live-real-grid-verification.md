**English** · [日本語](BE-0303-xcuitest-live-real-grid-verification-ja.md)

# BE-0303 — Real-grid verification of the XCUITest live device-cloud route

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0303](BE-0303-xcuitest-live-real-grid-verification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0303") |
| Topic | Device-cloud execution |
<!-- /BE-METADATA -->

## Introduction

`drivers/xcuitest_live.py`'s `WebDriverClient` and `XcuitestLiveDriver`
([BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md)) drive a
device-cloud route over the World Wide Web Consortium (W3C) WebDriver protocol against Appium, for the
[BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md)
device provider seam. The driver module's own docstring says both are "faked at the network boundary
so no grid is needed on the gate" — every test drives a synthetic transport, never a real Appium grid.
No CI lane exercises the real wire format, session lifecycle, or `mobile:` command semantics this
backend depends on. This item adds one.

## Motivation

A synthetic transport proves `XcuitestLiveDriver` correctly builds and parses whatever WebDriver
JSON its own tests hand it — real coverage of the driver's internal logic. It proves nothing about
whether a real Appium server actually accepts that JSON, whether session creation/teardown behaves as
the driver assumes under a real grid's timing, or whether the `mobile:` extension commands the driver
relies on are supported the way the client code expects on the grid provider actually in use. Because
this route exists specifically as an opt-in path to real device-cloud hardware
([BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md)),
a mock of the very protocol it exists to speak cannot validate the one property that matters: that it
actually works against a real grid.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A real Appium target for CI.** Stand up (or connect to an existing) real Appium server — a local
  instance against a Simulator/emulator is enough to validate the WebDriver wire contract; a real
  cloud grid is a further, optional step once the local path is proven.
- **A key-gated or environment-gated live session test.** Drive `XcuitestLiveDriver` through a real
  session create → actuate → teardown cycle against that Appium target, skipped when no target is
  configured.
- **Cover the `mobile:` extension commands the driver actually uses**, not just basic session
  lifecycle, since those are the commands most likely to diverge between Appium driver versions.
- **Non-gating first.** Land the new job as CI signal, following the precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md),
  before considering it required — a real Appium/grid dependency carries more setup and flakiness
  risk than the existing conformance suite.

## Alternatives considered

- **Trust the synthetic-transport tests, since the WebDriver JSON construction is unit-tested.**
  Correct JSON construction says nothing about whether a real Appium server accepts it, which is the
  actual claim this backend makes.
- **Defer real verification until a concrete device-cloud provider adopts this route.** The route
  already exists and is reachable through the device provider seam today; leaving it unverified until
  a specific provider integration lands means any interim regression ships silently, with no lane to
  catch it.

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
