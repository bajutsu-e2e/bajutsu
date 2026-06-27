**English** · [日本語](BE-0020-multi-backend-evidence-fallback-ja.md)

# BE-0020 — Multi-backend evidence fallback

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0020](BE-0020-multi-backend-evidence-fallback.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Backend expansion (iOS actuators) |
<!-- /BE-METADATA -->

## Introduction

The actuator is currently single. Absorb capability gaps by routing only evidence capture to a different backend (designed in §9, not yet wired).

## Motivation

A single backend rarely provides every kind of evidence. idb, for instance, has no native network monitoring (`capabilities()` returns no `network`), so a capture that needs it must come from somewhere else. DESIGN §9 already designs for this: `backend` is an ordered list, the **actuator** (the first available backend) performs all actuation and resolution, and every *other* backend may serve as a **read-only evidence fallback** that supplies a capability the actuator lacks. Each artifact records which provider it came from, so the manifest stays honest about where each piece of evidence originated.

The design exists but is not wired. `docs/drivers.md` states it plainly: "the current execution path uses a single actuator; multi-backend evidence fallback is not yet wired up." So today, if the actuator cannot produce a requested capture, the evidence is simply skipped — even when another declared backend could have supplied it read-only. As iOS gains a second actuator (XCUITest, BE-0019) and the project moves toward other platforms, the gap widens: capabilities that one backend has and another lacks should be absorbed on the abstraction side, exactly as §9 intends, rather than silently dropped. This proposal connects the existing design to the run loop.

## Detailed design

The mechanism follows DESIGN §9 directly: actuation stays with one backend; evidence resolution consults the others read-only. The single-actuator half is already wired; this fleshes out the *fallback* half.

### Current state (grounding)

`backends.py:select_actuator()` expands the ordered `backend` list and returns the **first available** actuator; every other token is then discarded — nothing in the runner instantiates a second backend. `capabilities_for(actuator)` already returns a backend's *static* capability set without constructing a driver (added for the BE-0082 preflight) — exactly the primitive a gap-detector needs. `Capability.network` means **native** observation: idb does not advertise it (it captures traffic via the app-side collector, `BAJUTSU_COLLECTOR` → `NetworkCollector`), while Playwright does (`WebNetworkCollector`). `evidence.py:Artifact` already carries a `provider` field, and `runner/pool.py:device_pool()` is the single seam — it selects the one actuator, pre-starts per-device collectors, and bundles `driver` + `FileSink` + `collector` into each `Lease`; `pipeline.py:run_all` clears the collector per scenario and writes `network.json` via `_write_network` (provider hard-coded `"collector"`).

### Two roles, derived from one list

Keep `select_actuator` unchanged. Add a resolver `evidence_backends(backends, available)` that returns the *remaining* available backends in list order — the read-only evidence providers. **Capability-gap detection**: map each evidence *kind* to the capability that supplies it natively (today `network → Capability.NETWORK`); the gap set is the kinds whose capability the actuator's static `capabilities_for(actuator)` lacks. **Dispatch**: for each gap kind, pick the first evidence backend whose `capabilities_for` advertises it — one provider per capability, in list order. (`screenshot` / `elements` always come from the actuator; `video` / `deviceLog` / `appTrace` are backend-independent `simctl` captures, orthogonal to the list.) If no backend supplies a gap kind, it is **skipped with a recorded reason** — graceful degradation, never a run failure.

### Read-only enforced structurally, not by convention

Rather than build the fallback's full `Driver` and trust callers not to actuate, introduce a narrow `EvidenceProvider` Protocol in `drivers/base.py` exposing only `capabilities()` plus read-only observation surfaces (e.g. `network_collector()`), with no `tap` / `type` / `swipe` / `wait` / `query`. The fallback is referenced *only* through `EvidenceProvider`, so "the fallback never actuates" becomes a mypy-strict compile-time fact — the strongest form of the §3.3 / §5 single-actuator guarantee. Selector resolution (`resolve_unique`, the determinism core) is reached only through the actuator's `Driver`, untouched. There are still no fixed sleeps; ambiguous selectors still fail; evidence capture sits entirely outside the pass/fail path.

### Lifecycle (reuse the collector machinery)

Mirror the existing collector lifecycle in `pool.py` so parallelism, teardown, and per-scenario scoping come for free. After `select_actuator`, compute the gap kinds and resolve a provider per gap; pre-start a process-level provider per device alongside the existing `NetworkCollector` loop, with the same "stop the already-started ones on failure" cleanup, and **degrade with a recorded reason** if a provider cannot launch in this environment. Per lease, hand the provider's `Collector` into the **existing `lz.collector` slot** — so `request` assertions, the per-scenario `clear()`, and `network.json` writing need *zero* changes; the fallback is just another `Collector` implementation behind the existing Protocol (`network.py:Collector`). Coordination is already solved: the collector runs on a background thread / event hooks, snapshots are read on the main thread, and `scenario_start` (monotonic, taken after launch) is the shared timeline origin. `shutdown()` / `lease.release()` tear the providers down exactly as they stop collectors today.

### Provenance (keep the manifest honest)

`Artifact.provider` must name the backend that actually supplied each artifact: web native = `"playwright"`; idb app collector = `"collector"` (unchanged); a fallback = e.g. `"<backend> (fallback; idb has no native network)"`. Add a per-scenario `SkippedCapture(kind, reason)` list on `RunResult` so a gap is *disclosed* rather than silently empty — it rides `asdict` into `manifest.json` and surfaces in `report/panels.py` beside the existing degradation disclosures. Optionally add a top-level `evidenceBackends: [...]` to the manifest and bump `SCHEMA_VERSION` (currently 3; BE-0068 versioning degrades older runs gracefully).

### First slice (most value, lowest risk, gate-testable)

Wire **`network`** as the single fallback kind, exercised entirely with fakes — no Simulator. The whole downstream network path (the `Collector` Protocol, per-scenario `clear()`, the `request` assertion, `network.json` + provenance) already exists and is backend-agnostic, so the slice only adds *resolution + instantiation* of a read-only `Collector` and drops it into the existing slot. The pure resolution layer (`evidence_backends`, gap detection, dispatch) is unit-testable like `select_actuator` is today (inject `available`). End-to-end, add a network-capable `FakeDriver` variant returning a deterministic `NetworkExchange` list (in-process, not a behavior mock), run a scenario with actuator = a no-network fake and one network-capable fake in the list, and assert `network.json` is written, its `provider` names the fallback, and `request` assertions read the exchanges. The whole slice stays inside the Linux `make check` gate.

### Scope & non-goals

**In scope:** read-only evidence providers from non-actuator backends; gap detection against `capabilities_for(actuator)`; provider-per-capability dispatch in list order; honest provenance (artifact `provider` + skip records); first slice = `network`.

**Non-goals:** any fallback *actuation* (capability gaps in actuation are the actuator ladder's job, BE-0019); opportunistic capture-from-all (rejected below); changing `video` / `deviceLog` / `appTrace` resolution (backend-independent `simctl`); any LLM (this is evidence plumbing, never judging); a new config surface (reuse the ordered `backend` list).

### Open questions

1. **Provider eligibility = "observes the same system under test", not merely "advertises the capability".** A cross-platform list like `[ios, web]` cannot have the web backend observe an *iOS* app's traffic, so fallback realistically only makes sense *within a platform* (e.g. two iOS actuators) or for a truly target-independent source (a mock server). The resolver must constrain eligibility accordingly — this is the most important open question.
2. **Provider string format** — free-form (`"<backend> (fallback; …)"`) vs. structured (`{provider, role, reason}`). Structured is more honest for tooling if the schema is bumped anyway.
3. **Skip records** — per-scenario (precise; a scenario may request `network` only sometimes) vs. per-run.
4. **Mock server as a provider.** §9 names the mock server as the `network` source; decide whether providers are strictly `backend`-list-derived or can include non-actuator sources like the mock server.

### Implementation sketch (small PR-sized slices)

1. **Pure resolution layer** — `backends.py`: `evidence_backends`, `KIND_CAPABILITY`, `resolve_evidence_providers(...) -> (provider_per_kind, skipped)`; fully unit-tested, no runner change.
2. **Read-only provider interface** — `drivers/base.py:EvidenceProvider` Protocol; `PlaywrightDriver` already satisfies the network part; add a network-capable `FakeDriver` variant for tests.
3. **Wire network fallback into the pool** — `pool.py` resolves and instantiates the provider's `Collector` into `lz.collector`; `pipeline._write_network` stamps the honest provider; `RunResult` gains `skipped_captures`.
4. **Provenance surfacing + docs** — optional manifest `evidenceBackends` (+ `SCHEMA_VERSION` bump); `report/panels.py` shows skips; update `docs/drivers.md` + `docs/ja/drivers.md` and `docs/evidence.md` (Japanese per the `japanese-tech-writing` skill).

## Alternatives considered

- **Leave it skipped (status quo).** Skipping a capability the actuator lacks is simple, but it discards evidence that a declared backend could have produced, weakening exactly the failure investigation Bajutsu exists to support. Since §9 already designed the fallback and the `provider` field already exists, wiring it is a small, faithful step rather than new architecture.
- **Let a fallback backend actuate when it is "better" for a step.** This would route some actions to a second driver. That breaks the single-actuator rule and reintroduces the non-determinism of two drivers contending for one device. Actuation must stay with the one fixed actuator; capability gaps in *actuation* are addressed by the actuator ladder (BE-0019), and only *evidence* gaps are filled here, read-only.
- **Merge each backend's captures opportunistically (capture from all that can).** Capturing the same evidence from multiple backends at once adds cost and ambiguity about which copy is authoritative, and risks the observer effect §9 warns about. Resolving one provider per capability, in list order, keeps a single clear source and bounded cost.

## References

[drivers.md](../../../docs/drivers.md) (the "not yet wired up" note), [evidence.md](../../../docs/evidence.md), [DESIGN §9](../../../DESIGN.md); `bajutsu/backends.py` (`select_actuator`, `resolve_actuators`, `capabilities_for`), `bajutsu/drivers/base.py` (`Capability`, `Driver`, `resolve_unique`), `bajutsu/capability_preflight.py` (the idb-`network` note), `bajutsu/evidence.py` (`Artifact`, `FileSink`), `bajutsu/network.py` (`Collector`, `NetworkCollector`), `bajutsu/runner/pool.py` (`device_pool` — the seam), `bajutsu/runner/pipeline.py` (`_write_network`, `run_all`), `bajutsu/report/manifest.py` (`SCHEMA_VERSION`), `bajutsu/drivers/playwright.py` (native `network`, `network_collector`), `bajutsu/drivers/fake.py` (`CAPABILITIES`).

**Dependencies / related items:** [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) (a second iOS actuator sharply increases the value — one iOS actuator covers the other's gaps — but this can land first with fakes), [BE-0082](../../implemented/BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md) (gives `capabilities_for` and the pure-preflight pattern reused here). The web (Playwright) backend already has native `network` and is the reference read-only `Collector`.
