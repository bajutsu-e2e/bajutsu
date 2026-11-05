**English** · [日本語](BE-0236-device-cloud-provider-abstraction-ja.md)

# BE-0236 — Device-cloud provider abstraction

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0236](BE-0236-device-cloud-provider-abstraction.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0236") |
| Implementing PR | [#1189](https://github.com/bajutsu-e2e/bajutsu/pull/1189) |
| Topic | Device-cloud execution |
<!-- /BE-METADATA -->

## Introduction

Bajutsu drives devices that are always local. The Android backend shells out to `adb -s <serial>`.
The iOS backends call `simctl`, `idb`, and `xcodebuild`. The web backend drives a locally launched
browser. A run therefore assumes that the device it drives lives on the same host.

This item introduces a **device provider** seam. With the seam, a run can instead receive a device
that a cloud service provisions: a reserved real device reachable over the network. The deterministic
core stays unchanged.

The seam is deliberately narrow, and it stays off the `run`/CI verdict path. A provider has one job:
*acquire a reachable device and later release it*. Everything downstream is unchanged. That covers
selector resolution, the deterministic runner, assertions, and evidence. From the runner's point of
view, a cloud device is just a serial (or endpoint) like any other.

Concrete providers live as **optional, separately-installable adapters**. Firebase Device Streaming
and AWS Device Farm are the first candidates, and others may follow. The deterministic gate then
never depends on a cloud SDK.

## Motivation

Two forces pull toward device clouds.

- **Real hardware.** Emulators and simulators miss device-specific behaviour: real GPUs, sensors,
  OEM skins, and actual iOS entitlement and signing paths. CI runners cannot hold a matrix of
  physical phones.
- **Breadth.** Teams already pay for AWS Device Farm or Firebase. They want their Bajutsu scenarios
  to run there, instead of maintaining a parallel test stack.

The roadmap has treated device clouds as deferred, not forbidden. The README's "Not adopting" list
currently reads *"Cloud device farm / real-device / cloud execution — out of scope"*.
[DESIGN §1](../../DESIGN.md) is explicit, however, that this is a **future task, not a permanent
constraint**. Choosing the iOS Simulator first was "the choice of the first foothold". The
deterministic core is backend-agnostic precisely so that it can later reach real devices and clouds.

This item resolves the inconsistency in favour of DESIGN's framing. It updates the scope statement
accordingly (see *Detailed design*).

Device-cloud support does **not** conflict with any prime directive.

- Driving a real device over adb is exactly as deterministic as driving an emulator.
- The provider adds no LLM to the gate.
- The provider a target uses is per-target config, so the tool, drivers, and runner stay
  app-agnostic.

The seam must keep the *provider's* machinery out of the deterministic core. That machinery covers
SDKs, credentials, reservation, billing, and network retries. Keeping it in optional adapters is the
whole point of this item.

One design observation shapes the seam. Device clouds come in two execution topologies, and the two
differ in shape.

- **Live (remote device).** The run executes locally and drives a *remote* device over a network
  transport. Firebase's Android Device Streaming exposes reserved hardware as "adb over SSL".
  Commercial clouds expose an Appium/WebDriver endpoint. The provider *reserves a device and yields a
  connection*: a serial for `adb connect`, or an endpoint. This maps directly onto Bajutsu's
  existing lifecycle: acquire, the runner drives, release.
- **Batch (remote execution).** The run executes *on the cloud's host*. AWS Device Farm's custom test
  environment uploads a package and runs your commands there. Bajutsu is the payload, not the
  caller. The "provider" is a CI-side packager/submitter, not a runtime object.

This item scopes the runtime seam to the **live** topology. A single cross-provider abstraction
genuinely pays off there. The batch topology is handled as a separate CI-side submitter item (the
*aws-device-farm-submitter* sibling). Forcing both topologies under one runtime interface would make
the interface leaky.

## Detailed design

### The provider seam

Add a narrow `DeviceProvider` protocol. Its single responsibility is to lease a reachable device for
the duration of a run and release it afterwards. The natural shape mirrors the existing lease-based
`device_pool` (`bajutsu/runner/pool.py`). The pool already hands the runner a device handle and
reclaims it. The protocol has two operations:

- `acquire(target) -> DeviceLease` reserves a device. It returns a handle that carries the
  connection coordinates the relevant backend already understands. For Android, the handle carries a
  `serial` in the `IP:port` form that the driver already accepts (`bajutsu/device_id.py` validates
  `adb connect` targets). For an Appium-style provider, it carries an endpoint URL.
- `DeviceLease.release()` ends the reservation and stops billing.

When a target names no provider, the default is the current **local** provider. Behaviour is
unchanged for every existing target.

### Registry + optional adapters (the house pattern)

Concrete providers register under a `kind` key. A registry resolves them. The codebase already does
the same for backends (BE-0042), mailbox transports (BE-0186), the evidence store URI scheme
(BE-0110), and AI providers. An unknown `kind` fails closed with a clear `ValueError`.

Each concrete provider ships as an **optional extra** (e.g. `pip install "bajutsu[firebase]"`) that
wraps the provider's CLI/SDK. Firebase Device Streaming is the first one, and commercial clouds may
follow. The deterministic gate installs none of them, so `make check` never pulls a cloud SDK. The
non-deterministic surface (auth, network, retries) stays strictly out of the core. Determinism-first
therefore holds at the dependency level, not just in the code path.

### Config surface

A target selects a provider through the existing config layering (`bajutsu/config.py`), under
`targets.<name>`:

```yaml
targets:
  pixel-cloud:
    platform: android
    backend: [adb]
    deviceProvider:
      kind: firebase-streaming   # default when omitted: local
      # provider-specific fields (project, device model, api target…) validated by the adapter
```

The exact key names are an implementation detail for the PR. The constraint is that provider
selection is **target-level, app-agnostic config**. It is never per-scenario or per-step.

### Isolating cloud differences from the local path

A cloud-provisioned device differs from a freshly booted local emulator in a few ways. The seam must
isolate these differences, instead of smearing them through the backend. They surface through the
existing `RunEnvironment` protocol (`bajutsu/runner/platform_lifecycle.py`), so the driver itself is
unchanged:

- **Boot / readiness.** A cloud device is already booted. The local boot-wait (`_await_boot`) must be
  skippable when the provider reports the device ready.
- **App install.** When the provider installs the app package itself, the `appPath` install is
  skipped. Otherwise the existing `adb install` path runs unchanged.
- **Device control degradation.** Cloud devices may not support the emulator-only device-control
  primitives: set location, clipboard, and status bar. The provider declares reduced capabilities.
  The existing preflight (BE-0082) then cuts unsupported actions loudly *before* the run, instead of
  failing mid-run.

### Scope statement update

This change alters documented scope. The same change therefore updates [DESIGN §1](../../DESIGN.md)
and the README "Not adopting" entry. Both then state that device-cloud execution is a supported
direction (behind optional adapters), not a non-goal. DESIGN and the roadmap stay in step with
behaviour (BE-0113). This item flips the stance, and the sibling adapter and submitter items build
on the new stance.

### Work breakdown (MECE)

1. **`DeviceProvider` / `DeviceLease` protocol.** Define the narrow seam and its handle type.
   Document the invariant that the seam is off the run/CI verdict path.
2. **Local provider (default).** Refactor today's local device acquisition to sit behind the seam,
   with no behaviour change for existing targets.
3. **Provider registry.** Register and resolve providers by `kind`. Fail closed on an unknown
   `kind` (following BE-0042 / BE-0186).
4. **Config surface.** Add `deviceProvider` on `targets.<name>`. Resolve it lazily, so that config
   load never imports a cloud SDK. An unknown kind raises a loud error.
5. **`RunEnvironment` cloud hooks.** Add a skippable boot-wait, an optional app-install, and
   capability degradation for device control. Wire all three through the existing environment
   protocol.
6. **Tests.** Cover provider resolution, the local default (no behaviour change), fail-closed on an
   unknown kind, and the cloud-difference hooks. Use a fake provider, so that no live cloud enters
   the gate.
7. **Docs + scope update.** Document the provider model in `docs/` (both languages). Update DESIGN
   §1 and the README "Not adopting" entry.

### Prime-directive compliance

- **AI out of the gate.** A provider acquires and releases a device. No model is consulted. The
  deterministic runner and the CI verdict are untouched.
- **Determinism first.** Driving a real device over adb is as reproducible as driving an emulator.
  No fixed sleeps are introduced. Readiness stays a condition, which the provider reports.
- **App-agnostic.** Provider choice is `targets.<name>` config. The tool, drivers, and runner keep
  their shape. Provider SDKs live in optional extras, so the deterministic gate stays cloud-free.

## Alternatives considered

- **One client that abstracts both live and batch topologies.** Rejected. A single runtime interface
  covering both AWS Device Farm (batch) and Device Streaming (live) would be leaky. In the batch
  model, Bajutsu is the uploaded payload, not the caller. "Acquire a device handle" has no runtime
  meaning there. The runtime seam is scoped to the live topology. Batch is handled as a CI-side
  submitter (the *aws-device-farm-submitter* sibling).
- **Build the provider machinery into the core (no optional extras).** Rejected. It drags cloud
  SDKs, credentials, and network retries into the deterministic core and its dependency closure.
  Concrete providers are optional adapters, so the gate stays cloud-free.
- **A fully separate repository for the client from day one.** Rejected for now. A separate
  repository is attractive for decoupling, but it pays release and versioning overhead before the
  interface is proven. The client starts as in-repo optional extras behind a stable protocol, which
  keeps later extraction cheap.
- **Design the abstraction fully up front, before any provider.** Rejected, because it risks the
  wrong abstraction. The first concrete adapter (*firebase-device-streaming-adapter*) validates the
  seam instead. This item lands the seam informed by that need, which is the PoC-first sequencing.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `DeviceProvider` / `DeviceLease` protocol (`bajutsu/runner/device_provider.py`)
- [x] Local provider (default, no behaviour change) — the `--udid` string passes through unchanged
- [x] Provider registry (`kind`-keyed, fail-closed) — mirrors the mailbox registry (BE-0186)
- [x] Config surface (`deviceProvider` on `targets.<name>`, lazy resolution; unknown kind → runtime error)
- [x] `RunEnvironment` cloud hooks — boot-wait skip + optional install, via a `ProvisionProfile` on
  the lease threaded `run` → pool → `environment_for` → `AndroidEnvironment.start` (actual path
  `bajutsu/platform_lifecycle/`, not the proposal's older `runner/platform_lifecycle.py`)
- [x] Tests (fake provider) — registry resolution, local default, fail-closed, boot/install skip
- [x] Docs + scope update (DESIGN.md §1's 「やらないこと」 scope-exclusion list, `docs/` both languages)

> **Deferred within Unit 5 — device-control capability degradation.** The third cloud-difference
> bullet (a provider declaring a reduced device-control capability set so preflight cuts unsupported
> actions) is intentionally **not** wired in this seam PR. With only the `local` provider shipped,
> no code path produces a reduced set, so adding the field now would be untested speculative surface
> — against the house grain of shipping the seam plus one reference adapter (BE-0186). It lands with
> the first cloud adapter (**firebase-device-streaming-adapter**), where a real reduced capability
> set exists to drive and test the preflight cut. The seam is ready for it: `ProvisionProfile` is the
> natural carrier, and preflight (BE-0082) already consumes a capability set.

> **Scope — `run` only.** `acquire_device` is wired into `bajutsu run`; `record`, `crawl`, and
> `audit --repeat` still resolve devices the old way. A cloud `kind` on a target those commands
> drive is therefore ignored there rather than fail-closed. Harmless while only `local` ships (the
> sibling cloud adapters extend the seam to those commands), but noted so they pick it up.

### Progress log

- Seam + local provider + registry + config surface + boot/install skip hooks, all behind the
  `local` default so every existing target is byte-for-byte unchanged. Reviewer-driven hardening:
  the lease releases even on a setup-time error and its `release` is warn-only so a provider teardown
  can never flip the machine verdict (mirrors the post-verdict zip/upload rule). `make check` green
  (4414 tests, 92% coverage). PR: [#1189](https://github.com/bajutsu-e2e/bajutsu/pull/1189).

## References

- [DESIGN.md §1 — purpose and scope](../../DESIGN.md)
- [docs/architecture.md — implementation status](../../docs/architecture.md)
- [BE-0042 — Platform-aware backend registry & selection](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md)
- [BE-0186 — mailbox provider registry](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md)
- [BE-0110 — evidence store URI](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md)
- [BE-0082 — capability preflight check](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md)
- [BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md)
- Sibling items: **firebase-device-streaming-adapter** (first live adapter), **aws-device-farm-submitter** (batch, CI-side), **ios-device-cloud-execution** (iOS real-device path)
