**English** · [日本語](BE-0020-multi-backend-evidence-fallback-ja.md)

# BE-0020 — Multi-backend evidence fallback

* Proposal: [BE-0020](BE-0020-multi-backend-evidence-fallback.md)
* Author: [@0x0c](https://github.com/0x0c)
* Status: **Proposal**
* Track: [Proposals](../../README.md#proposals)
* Topic: Backend expansion (iOS actuators)

## Introduction

The actuator is currently single. Absorb capability gaps by routing only evidence capture to a different backend (designed in §9, not yet wired).

## Motivation

A single backend rarely provides every kind of evidence. idb, for instance, has no native network monitoring (`capabilities()` returns no `network`), so a capture that needs it must come from somewhere else. DESIGN §9 already designs for this: `backend` is an ordered list, the **actuator** (the first available backend) performs all actuation and resolution, and every *other* backend may serve as a **read-only evidence fallback** that supplies a capability the actuator lacks. Each artifact records which provider it came from, so the manifest stays honest about where each piece of evidence originated.

The design exists but is not wired. `docs/drivers.md` states it plainly: "the current execution path uses a single actuator; multi-backend evidence fallback is not yet wired up." So today, if the actuator cannot produce a requested capture, the evidence is simply skipped — even when another declared backend could have supplied it read-only. As iOS gains a second actuator (XCUITest, BE-0019) and the project moves toward other platforms, the gap widens: capabilities that one backend has and another lacks should be absorbed on the abstraction side, exactly as §9 intends, rather than silently dropped. This proposal connects the existing design to the run loop.

## Detailed design

The mechanism follows DESIGN §9 directly: actuation stays with one backend; evidence resolution consults the others read-only.

- **Per-capability provider resolution.** When the run loop needs a capture, it resolves a provider by walking the `backend` list and asking each backend's `capabilities()` for that token. The actuator is tried for capabilities it offers (`screenshot`, `elements`); backend-independent captures (`video`, `deviceLog`) come from `simctl` as they do now; a capability the actuator lacks (e.g. `network`) is sourced from the first *other* backend that advertises it, used strictly read-only. This is the resolution table §9 already specifies, now driving real behaviour.
- **Read-only is enforced.** Only the actuator may `tap` / `type` / `swipe` / `wait` / `query`. A fallback backend is used solely to pull evidence; it never actuates. This keeps the single-actuator guarantee (DESIGN §3.3 / §5) intact — two drivers never operate one device — so nothing about determinism changes. There are still no fixed sleeps and ambiguous selectors still fail; evidence capture sits outside the pass/fail path entirely.
- **Provenance and graceful skip.** Each `Artifact` already carries a `provider`; with fallback wired, that field records the backend (or `simctl` / mock server) that actually supplied the capture — e.g. `network: mockServer (idb has no native monitoring)`. If no backend in the list can provide a requested capability, the capture is skipped and the reason is recorded in the manifest with its capability flag, matching the existing degradation-disclosure rule, rather than failing the run.
- **Configured through the existing backend list.** No new config surface is needed: the ordered `backend` list (and any per-app backend settings under `apps.<name>`) is the same one used for actuator selection. The tool stays app-agnostic — which backends are available is environment/config, not baked into the runner. The Tier-2 gate remains LLM-free; this is plumbing for evidence, never for judging.

## Alternatives considered

- **Leave it skipped (status quo).** Skipping a capability the actuator lacks is simple, but it discards evidence that a declared backend could have produced, weakening exactly the failure investigation Bajutsu exists to support. Since §9 already designed the fallback and the `provider` field already exists, wiring it is a small, faithful step rather than new architecture.
- **Let a fallback backend actuate when it is "better" for a step.** This would route some actions to a second driver. That breaks the single-actuator rule and reintroduces the non-determinism of two drivers contending for one device. Actuation must stay with the one fixed actuator; capability gaps in *actuation* are addressed by the actuator ladder (BE-0019), and only *evidence* gaps are filled here, read-only.
- **Merge each backend's captures opportunistically (capture from all that can).** Capturing the same evidence from multiple backends at once adds cost and ambiguity about which copy is authoritative, and risks the observer effect §9 warns about. Resolving one provider per capability, in list order, keeps a single clear source and bounded cost.

## References

[drivers.md](../../../docs/drivers.md), [DESIGN §9](../../../DESIGN.md)
