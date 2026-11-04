**English** · [日本語](BE-0020-multi-backend-evidence-fallback-ja.md)

# BE-0020 — Multi-backend evidence fallback

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0020](BE-0020-multi-backend-evidence-fallback.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0020") |
| Implementing PR | [#357](https://github.com/bajutsu-e2e/bajutsu/pull/357) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

The run currently has a single actuator. Capability gaps are absorbed by routing only evidence capture to a different backend. DESIGN §9 designs this routing, but it is not yet wired.

## Motivation

A single backend rarely provides every kind of evidence. For example, idb has no native network monitoring: `capabilities()` returns no `network`. A capture that needs network monitoring must come from somewhere else.

DESIGN §9 already designs for this:

- `backend` is an ordered list.
- The **actuator** (the first available backend) performs all actuation and resolution.
- Every *other* backend may serve as a **read-only evidence fallback**, which supplies a capability that the actuator lacks.
- Each artifact records which provider it came from, so the manifest stays honest about where each piece of evidence originated.

The design exists but is not wired. `docs/drivers.md` states it plainly: "the current execution path uses a single actuator; multi-backend evidence fallback is not yet wired up." Today, if the actuator cannot produce a requested capture, the evidence is simply skipped. The skip happens even when another declared backend could have supplied it read-only.

The gap widens as iOS gains a second actuator (XCUITest, BE-0019) and the project moves toward other platforms. The abstraction side should absorb capabilities that one backend has and another lacks, as §9 intends, instead of silently dropping them. This proposal connects the existing design to the run loop.

## Detailed design

The mechanism follows DESIGN §9 directly: actuation stays with one backend; evidence resolution consults the others read-only. The single-actuator half is already wired; this fleshes out the *fallback* half.

### Current state (grounding)

`backends.py:select_actuator()` expands the ordered `backend` list and returns the **first available** actuator. It then discards every other token, so nothing in the runner instantiates a second backend.

`capabilities_for(actuator)` already returns a backend's *static* capability set without constructing a driver. BE-0082 added it for the preflight. It is exactly the primitive that a gap detector needs.

`Capability.NETWORK` means **native** observation. idb does not advertise it, because idb captures traffic through the app-side collector (`BAJUTSU_COLLECTOR` → `NetworkCollector`). Playwright does advertise it (`WebNetworkCollector`).

`evidence.py:Artifact` already carries a `provider` field. `runner/pool.py:device_pool()` is the single seam. It selects the one actuator, pre-starts per-device collectors, and bundles `driver`, `FileSink`, and `collector` into each `Lease`. `pipeline.py:run_all` clears the collector per scenario. It writes `network.json` through `_write_network`, with the provider hard-coded as `"collector"`.

### Two roles, derived from one list

Keep `select_actuator` unchanged. Add a resolver `evidence_backends(backends, actuator, available)`. It returns the *remaining* available backends in list order, **filtered to those on the actuator's own platform**. These are the read-only evidence providers.

**Eligibility is "same system under test"** (decision 1 below). A provider is eligible only if it resolves to an actuator on the actuator's platform. The resolver finds that platform by reverse lookup in `backends.PLATFORMS`, the platform-to-actuators registry that BE-0042 added and [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) builds on. Only a same-platform backend observes the same running app. A cross-platform list such as `[ios, web]` therefore yields no web provider for an idb actuator.

**Capability-gap detection** maps each evidence *kind* to the capability that supplies it natively (today `network → Capability.NETWORK`). The gap set holds the kinds whose capability the actuator's static `capabilities_for(actuator)` lacks.

**Dispatch** picks, for each gap kind, the first eligible evidence backend whose `capabilities_for` advertises it. That assigns one provider per capability, in list order. Two cases fall outside dispatch:

- `screenshot` and `elements` always come from the actuator.
- `video`, `deviceLog`, and `appTrace` are backend-independent `simctl` captures, orthogonal to the list.

If no backend supplies a gap kind, the capture is **skipped with a recorded reason**. This is graceful degradation and never a run failure.

Each platform has only one implemented actuator today (`ios → idb`, `web → playwright`). In production, the same-platform filter therefore resolves to *no* provider until a platform gains a second actuator (iOS with XCUITest, [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)). That makes the filter a safe no-op, and current runs do not change. The first slice is exercised with a same-platform fake that supports network capture (below).

### Read-only enforced structurally, not by convention

Building the fallback's full `Driver` would mean trusting callers not to actuate. Instead, introduce a narrow `EvidenceProvider` Protocol in `drivers/base.py`. It exposes only `capabilities()` and read-only observation surfaces (for example, `network_collector()`). It has no `tap`, `type`, `swipe`, `wait`, or `query`.

The fallback is referenced *only* through `EvidenceProvider`. "The fallback never actuates" therefore becomes a compile-time fact under mypy strict. This is the strongest form of the single-actuator guarantee in §3.3 and §5.

Selector resolution (`resolve_unique`, the determinism core) is reached only through the actuator's `Driver`, and this design does not touch it. There are still no fixed sleeps, and ambiguous selectors still fail. Evidence capture sits entirely outside the pass/fail path.

### Lifecycle (reuse the collector machinery)

Mirror the existing collector lifecycle in `pool.py`, so parallelism, teardown, and per-scenario scoping come for free.

After `select_actuator`, compute the gap kinds and resolve a provider per gap. Pre-start a process-level provider per device alongside the existing `NetworkCollector` loop. Use the same cleanup, which stops the already-started providers on failure. If a provider cannot launch in this environment, **degrade with a recorded reason**.

Per lease, hand the provider's `Collector` into the **existing `lz.collector` slot**. The `request` assertions, the per-scenario `clear()`, and the `network.json` writing then need *zero* changes. The fallback is just another `Collector` implementation behind the existing Protocol (`network.py:Collector`).

Coordination is already solved. The collector runs on a background thread or event hooks, and the main thread reads snapshots. `scenario_start` (monotonic, taken after launch) is the shared timeline origin. `shutdown()` and `lease.release()` tear the providers down exactly as they stop collectors today.

### Provenance (keep the manifest honest)

`Artifact.provider` must name the backend that actually supplied each artifact:

- Web native: `"playwright"`.
- idb app collector: `"collector"` (unchanged).
- A fallback: `"<backend> (fallback)"`.

For the first slice, the field stays a **plain string** (decision 2). A plain string is human-readable, and the gap reason rides the `SkippedCapture` list, not the provider string.

Add a per-scenario `SkippedCapture(kind, reason)` list to the scenario's result (decision 3). A gap is then *disclosed*, not silently empty. The list rides `asdict` into `manifest.json`. It surfaces in `report/panels.py` beside the existing degradation disclosures.

Three items are **deferred to a follow-up slice**, so the first slice changes no schema:

- the structured provider form (`{provider, role, reason}`)
- a top-level `evidenceBackends: [...]`
- the matching `SCHEMA_VERSION` bump (currently 3; the versioning of BE-0068 degrades older runs gracefully)

### First slice (most value, lowest risk, gate-testable)

Wire **`network`** as the single fallback kind, and exercise it entirely with fakes, with no Simulator. The whole downstream network path already exists and is backend-agnostic: the `Collector` Protocol, the per-scenario `clear()`, the `request` assertion, and `network.json` with provenance. The slice therefore adds only the *resolution and instantiation* of a read-only `Collector`, and drops it into the existing slot.

The pure resolution layer (`evidence_backends`, gap detection, dispatch) is unit-testable, like `select_actuator` is today. The test injects `available`.

The end-to-end test works as follows:

1. Add a network-capable `FakeDriver` variant. It returns a deterministic `NetworkExchange` list, in-process, not as a behavior mock.
2. Run a scenario whose list holds an actuator that is a no-network fake and one network-capable fake.
3. Assert that `network.json` is written, that its `provider` names the fallback, and that `request` assertions read the exchanges.

The whole slice stays inside the Linux `make check` gate.

### Scope and non-goals

**In scope:**

- Read-only evidence providers from non-actuator backends.
- Gap detection against `capabilities_for(actuator)`.
- Provider-per-capability dispatch in list order.
- Honest provenance (artifact `provider` plus skip records).
- First slice: `network`.

**Non-goals:**

- Any fallback *actuation*. Capability gaps in actuation are the job of the actuator ladder (BE-0019).
- Opportunistic capture from all backends (rejected below).
- Changing the `video`, `deviceLog`, and `appTrace` resolution (backend-independent `simctl`).
- Any LLM. This is evidence plumbing and never judges.
- A new config surface. The design reuses the ordered `backend` list.

### Decisions

The four open questions are resolved as follows. Each resolution is folded into the design above.

1. **Provider eligibility is "observes the same system under test".** A provider is eligible only if it resolves to an actuator on the **actuator's own platform** (reverse lookup in `backends.PLATFORMS`). A cross-platform list therefore never has one platform's backend observe another's app. This is the most important constraint, and it shapes `evidence_backends`. A consequence is that the fallback is a no-op until a platform has a second actuator (BE-0019). That is why the first slice is proven with same-platform fakes.
2. **Provider string format: a plain string for the first slice** (`"<backend> (fallback)"`). The structured `{provider, role, reason}` form is deferred to the follow-up that also adds `evidenceBackends` to the manifest and bumps `SCHEMA_VERSION`. That keeps the first slice schema-stable.
3. **Skip records are per-scenario.** A scenario may request `network` only sometimes. `SkippedCapture(kind, reason)` therefore lives on the scenario's result, not the run's.
4. **Providers are strictly derived from the `backend` list in the first slice.** A target-independent source (the mock server that §9 names) sidesteps the platform constraint in decision 1. It is, however, a different kind of source. Wiring it as a `network` provider is a documented follow-up, not part of this slice.

### Implementation sketch (small PR-sized slices)

1. **Pure resolution layer.** In `backends.py`, add `evidence_backends`, `KIND_CAPABILITY`, and `resolve_evidence_providers(...) -> (provider_per_kind, skipped)`. Unit-test it fully. The runner does not change.
2. **Read-only provider interface.** Add the `drivers/base.py:EvidenceProvider` Protocol. `PlaywrightDriver` already satisfies the network part. Add a network-capable `FakeDriver` variant for tests.
3. **Wire the network fallback into the pool.** `pool.py` resolves the provider and instantiates its `Collector` into `lz.collector`. `pipeline._write_network` stamps the honest provider. `RunResult` gains `skipped_captures`.
4. **Surface provenance and update docs.** Optionally add `evidenceBackends` to the manifest (with a `SCHEMA_VERSION` bump). `report/panels.py` shows skips. Update `docs/drivers.md`, `docs/ja/drivers.md`, and `docs/evidence.md` (Japanese per the `japanese-tech-writing` skill).

## Alternatives considered

- **Leave it skipped (status quo).** Skipping a capability that the actuator lacks is simple. It also discards evidence that a declared backend could have produced, which weakens the failure investigation that Bajutsu exists to support. DESIGN §9 already designs the fallback, and the `provider` field already exists. Wiring the fallback is therefore a small, faithful step, not new architecture.
- **Let a fallback backend actuate when it is "better" for a step.** This routes some actions to a second driver. It breaks the single-actuator rule and reintroduces the non-determinism of two drivers contending for one device. Actuation must stay with the one fixed actuator. The actuator ladder (BE-0019) addresses capability gaps in *actuation*. This proposal fills only *evidence* gaps, read-only.
- **Merge each backend's captures opportunistically (capture from all that can).** Capturing the same evidence from several backends at once adds cost. It also leaves it unclear which copy is authoritative, and it risks the observer effect that §9 warns about. Resolving one provider per capability, in list order, keeps a single clear source and a bounded cost.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- Docs: [drivers.md](../../docs/drivers.md) (the "not yet wired up" note), [evidence.md](../../docs/evidence.md), [DESIGN §9](../../DESIGN.md).
- `bajutsu/backends.py`: `select_actuator`, `resolve_actuators`, `capabilities_for`.
- `bajutsu/drivers/base.py`: `Capability`, `Driver`, `resolve_unique`.
- `bajutsu/capability_preflight.py`: the idb `network` note.
- `bajutsu/evidence.py`: `Artifact`, `FileSink`.
- `bajutsu/network.py`: `Collector`, `NetworkCollector`.
- `bajutsu/runner/pool.py`: `device_pool`, the seam.
- `bajutsu/runner/pipeline.py`: `_write_network`, `run_all`.
- `bajutsu/report/manifest.py`: `SCHEMA_VERSION`.
- `bajutsu/drivers/playwright.py`: native `network`, `network_collector`.
- `bajutsu/drivers/fake.py`: `CAPABILITIES`.

**Dependencies and related items:**

- [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md): a second iOS actuator sharply increases the value, because one iOS actuator covers the other's gaps. This item can land first with fakes.
- [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md): provides `capabilities_for` and the pure-preflight pattern reused here.
- The web (Playwright) backend already has native `network`. It is the reference read-only `Collector`.
