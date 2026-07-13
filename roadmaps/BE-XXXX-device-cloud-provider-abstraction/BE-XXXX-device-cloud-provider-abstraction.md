**English** · [日本語](BE-XXXX-device-cloud-provider-abstraction-ja.md)

# BE-XXXX — Device-cloud provider abstraction

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-device-cloud-provider-abstraction.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu drives devices that are always local: the Android backend shells out to `adb -s <serial>`,
the iOS backends to `simctl` / `idb` / `xcodebuild`, the web backend to a locally launched
browser. A run therefore assumes the device it drives lives on the same host. This item introduces
a **device provider** seam so a run can instead be handed a device that a cloud service provisions
— a reserved real device reachable over the network — without changing the deterministic core.

The seam is deliberately narrow and deliberately kept off the `run`/CI verdict path: a provider's
only job is to *acquire a reachable device and later release it*. Everything downstream — selector
resolution, the deterministic runner, assertions, evidence — is unchanged, because from the
runner's point of view a cloud device is just a serial (or endpoint) like any other. Concrete
providers (Firebase Device Streaming, AWS Device Farm, and later others) live as **optional,
separately-installable adapters** so the deterministic gate never depends on a cloud SDK.

## Motivation

Two things pull toward device clouds. First, real hardware: emulators and simulators miss
device-specific behaviour (real GPUs, sensors, OEM skins, actual iOS entitlement/signing paths),
and CI runners cannot hold a matrix of physical phones. Second, breadth: teams already pay for AWS
Device Farm or Firebase, and want their Bajutsu scenarios to run there rather than maintaining a
parallel test stack.

The roadmap has treated this as deferred rather than forbidden. The README's "Not adopting" list
currently reads *"Cloud device farm / real-device / cloud execution — out of scope"*, but
[DESIGN §1](../../DESIGN.md) is explicit that this is a **future task, not a permanent
constraint**: choosing the iOS Simulator first was "the choice of the first foothold," and the
deterministic core is backend-agnostic precisely so it can later reach real devices and clouds.
This item resolves that internal inconsistency in favour of DESIGN's framing, and updates the
scope statement accordingly (see *Detailed design*).

Crucially, device-cloud support does **not** conflict with any prime directive. Driving a real
device over adb is exactly as deterministic as driving an emulator; the provider adds no LLM to the
gate; and which provider a target uses is per-target config, so the tool, drivers, and runner stay
app-agnostic. What the seam must avoid is letting the *provider's* machinery — SDKs, credentials,
reservation, billing, network retries — leak into the deterministic core. Keeping that machinery in
optional adapters is the whole point of this item.

A design observation that shapes the seam: device clouds come in two execution topologies, and they
are not the same shape.

- **Live (remote device).** The run executes locally and drives a *remote* device over a network
  transport — Firebase's Android Device Streaming exposes reserved hardware as "adb over SSL," and
  commercial clouds expose an Appium/WebDriver endpoint. Here the provider's job is to *reserve a
  device and yield a connection* (a serial for `adb connect`, or an endpoint). This maps directly
  onto Bajutsu's existing lifecycle: acquire → the runner drives → release.
- **Batch (remote execution).** The run executes *on the cloud's host* — AWS Device Farm's custom
  test environment uploads a package and runs your commands there. Here Bajutsu is the payload, not
  the caller; the "provider" is a CI-side packager/submitter, not a runtime object.

This item scopes the runtime seam to the **live** topology, where a single cross-provider
abstraction genuinely pays off. The batch topology is handled as a separate CI-side submitter item
(the *aws-device-farm-submitter* sibling), because forcing both under one runtime interface would
make it leaky.

## Detailed design

### The provider seam

Add a narrow `DeviceProvider` protocol whose single responsibility is to lease a reachable device
for the duration of a run and release it afterwards. The natural shape mirrors the existing
lease-based `device_pool` (`bajutsu/runner/pool.py`), which already hands the runner a device
handle and reclaims it:

- `acquire(target) -> DeviceLease` — reserve a device and return a handle carrying the connection
  coordinates the relevant backend already understands: for Android, a `serial` in the `IP:port`
  form the driver already accepts (`bajutsu/device_id.py` validates `adb connect` targets); for an
  Appium-style provider, an endpoint URL.
- `DeviceLease.release()` — end the reservation (and stop billing).

The default, when a target names no provider, is the current **local** provider — behaviour is
unchanged for every existing target.

### Registry + optional adapters (the house pattern)

Concrete providers register under a `kind` key and are resolved through a registry, exactly as the
codebase already does for backends (BE-0042), mailbox transports (BE-0186), the evidence store URI
scheme (BE-0110), and AI providers. An unknown `kind` fails closed with a clear `ValueError`.

Each concrete provider (Firebase Device Streaming, and later commercial clouds) ships as an
**optional extra** (e.g. `pip install "bajutsu[firebase]"`) that wraps the provider's CLI/SDK. The
deterministic gate installs none of them, so `make check` never pulls a cloud SDK. This keeps the
non-deterministic surface (auth, network, retries) strictly out of the core, satisfying
determinism-first at the dependency level, not just the code path.

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

The exact key names are an implementation detail for the PR; the constraint is that provider
selection is **target-level, app-agnostic config**, never per-scenario or per-step.

### Isolating cloud differences from the local path

Cloud-provisioned devices differ from a freshly booted local emulator in a few ways that must be
isolated behind the seam rather than smeared through the backend. These are surfaced through the
existing `RunEnvironment` protocol (`bajutsu/runner/platform_lifecycle.py`) so the driver itself is
unchanged:

- **Boot / readiness.** A cloud device is already booted; the local boot-wait (`_await_boot`) must
  be skippable when the provider reports the device ready.
- **App install.** When the provider installs the app package itself, `appPath` install is skipped;
  when it does not, the existing `adb install` path runs unchanged.
- **Device control degradation.** Cloud devices may not support the emulator-only device-control
  primitives (set location, clipboard, status bar). The provider declares reduced capabilities so
  the existing preflight (BE-0082) cuts unsupported actions loudly *before* the run, rather than
  failing mid-run.

### Scope statement update

Because this changes documented scope, the same change updates [DESIGN §1](../../DESIGN.md) and the
README "Not adopting" entry to reflect that device-cloud execution is now a supported direction
(behind optional adapters), not a non-goal — keeping DESIGN and the roadmap in step with behaviour
(BE-0113). This is the item that flips the stance; the sibling adapter/submitter items build on the
new stance.

### Work breakdown (MECE)

1. **`DeviceProvider` / `DeviceLease` protocol** — define the narrow seam and its handle type;
   document the invariant that it is off the run/CI verdict path.
2. **Local provider (default)** — refactor today's local device acquisition to sit behind the seam,
   with no behaviour change for existing targets.
3. **Provider registry** — `kind`-keyed registration + resolution, fail-closed on unknown `kind`
   (following BE-0042 / BE-0186).
4. **Config surface** — `deviceProvider` on `targets.<name>`, lazily resolved so config load never
   imports a cloud SDK; unknown kind → loud error.
5. **`RunEnvironment` cloud hooks** — skippable boot-wait, optional app-install, capability
   degradation for device control, wired through the existing environment protocol.
6. **Tests** — provider resolution, the local default (no behaviour change), fail-closed on unknown
   kind, and the cloud-difference hooks, using a fake provider (no live cloud in the gate).
7. **Docs + scope update** — document the provider model in `docs/` (both languages) and update
   DESIGN §1 and the README "Not adopting" entry.

### Prime-directive compliance

- **AI out of the gate.** A provider only acquires/releases a device; no model is consulted, and the
  deterministic runner and CI verdict are untouched.
- **Determinism first.** Driving a real device over adb is as reproducible as driving an emulator;
  no fixed sleeps are introduced (readiness stays a condition, reported by the provider).
- **App-agnostic.** Provider choice is `targets.<name>` config; the tool, drivers, and runner are
  unchanged in shape. Provider SDKs live in optional extras, so the deterministic gate stays
  cloud-free.

## Alternatives considered

- **One client that abstracts both live and batch topologies.** A single runtime interface covering
  both AWS Device Farm (batch) and Device Streaming (live) would be leaky: in the batch model
  Bajutsu is the uploaded payload, not the caller, so "acquire a device handle" has no runtime
  meaning there. Rejected — scope the runtime seam to the live topology; handle batch as a
  CI-side submitter (the *aws-device-farm-submitter* sibling).
- **Build the provider machinery into the core (no optional extras).** This drags cloud SDKs,
  credentials, and network retries into the deterministic core and its dependency closure. Rejected
  — concrete providers are optional adapters so the gate stays cloud-free.
- **A fully separate repository for the client from day one.** Attractive for decoupling, but pays
  release/versioning overhead before the interface is proven. Rejected for now: start as in-repo
  optional extras behind a stable protocol, keeping later extraction cheap.
- **Design the abstraction fully up front, before any provider.** Risks the wrong abstraction.
  Instead, the first concrete adapter (*firebase-device-streaming-adapter*) validates the seam, and
  this item lands the seam informed by that need — the PoC-first sequencing.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `DeviceProvider` / `DeviceLease` protocol
- [ ] Local provider (default, no behaviour change)
- [ ] Provider registry (`kind`-keyed, fail-closed)
- [ ] Config surface (`deviceProvider` on `targets.<name>`, lazy resolution)
- [ ] `RunEnvironment` cloud hooks (boot-wait skip / optional install / capability degradation)
- [ ] Tests (fake provider)
- [ ] Docs + scope update (DESIGN §1, README "Not adopting")

## References

- [DESIGN.md §1 — purpose and scope](../../DESIGN.md)
- [docs/architecture.md — implementation status](../../docs/architecture.md)
- [BE-0042 — Platform-aware backend registry & selection](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md)
- [BE-0186 — mailbox provider registry](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md)
- [BE-0110 — evidence store URI](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md)
- [BE-0082 — capability preflight check](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md)
- [BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md)
- Sibling items: **firebase-device-streaming-adapter** (first live adapter), **aws-device-farm-submitter** (batch, CI-side), **ios-device-cloud-execution** (iOS real-device path)
