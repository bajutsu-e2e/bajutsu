**English** · [日本語](BE-0197-environment-protocol-shape-ja.md)

# BE-0197 — Even out the Environment protocol shape for a third platform

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0197](BE-0197-environment-protocol-shape.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0197") |
| Implementing PR | [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) Phase 0
introduced the `Environment` Protocol, which ended the runner's per-actuator branching. The Protocol is
one interface. Its `start` method runs a platform's whole per-run bring-up, and its lease-shaping
methods let the runner drive iOS and web through the same surface. That seam shipped, and it works.

The current implementers, however, satisfy the Protocol unevenly. A third platform (Android,
[BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)) will have to copy or guess at that
unevenness. This item does *not* change the seam's behavior. It evens out the Protocol's shape, so
that one self-evident answer exists before Android arrives: what must a new `Environment` provide, and
how does it decline what does not apply?

## Motivation

`bajutsu/platform_lifecycle.py` defines the `Environment` Protocol (`:47`) and three families of
implementer:

- the device-style `_DeviceEnvironment` base (`:124`);
- `IosEnvironment`, `FakeEnvironment`, and `XcuitestEnvironment`, which build on that base;
- the standalone `WebEnvironment` (`:250`), which implements the Protocol directly.

The seam is correct, but three inconsistencies make the next implementer harder to add than it should
be:

- **"Not applicable" is expressed three different ways.** A Protocol method that a platform has no use
  for takes one of three forms:
  - a silent `return None` (`WebEnvironment.controller`, `_DeviceEnvironment.crawl_recover` /
    `crawl_aliveness` / `crawl_dialog_clearer`);
  - a `return {}` / `return False` no-op (`_DeviceEnvironment.records_video_up_front`,
    `WebEnvironment.device_catalog`);
  - a `raise NotImplementedError` (`_DeviceEnvironment.hook_collector`, at
    `platform_lifecycle.py:146`).

  The three are not interchangeable. Raising from `hook_collector` is safe only because the runner is
  supposed to call the method *only when* `observes_network_via_driver()` is true. That contract lives
  in a one-line docstring, not in the types. From the Protocol alone, a new implementer cannot tell
  which methods it may leave to raise, which it must make a real no-op, and which the runner will
  actually call.

- **Two feature-flag methods gate three capability methods, by convention only.** The Protocol bundles
  predicate methods (`observes_network_via_driver`, `records_video_up_front`, `has_devices`) with the
  capability methods those predicates gate (`hook_collector`, `controller`, the crawl methods). Nothing
  in the type system ties `hook_collector` to `observes_network_via_driver() == True`. A reader must
  reconstruct the safe-only-if-you-checked-the-flag relationship from prose and from the runner's call
  sites. A fresh implementer can easily get this shape ("capability advertised by a boolean, honored by
  convention") subtly wrong.

- **The Protocol mixes `run`-lease methods with crawl-only methods.** The lower half of the Protocol
  (`has_devices`, `plan_lanes`, `crawl_reset`, `crawl_aliveness`, `crawl_recover`,
  `crawl_dialog_clearer`, marked off by the comment at `:97`) exists for the crawl command. The upper
  half (`start`, `relauncher`, `controller`, `teardown`, `hook_collector`, the video and network
  predicates) serves the `run` lease. With both in one Protocol, every implementer must answer crawl
  questions even when the reader cares only about `run`. A future consumer that needs only the run
  lease still depends on the whole surface. BE-0009 slices 2 and 3 folded the two concerns into one
  Protocol incrementally, and nothing about the concerns requires it.

None of this is a bug, because the seam behaves correctly today. But BE-0009 explicitly frames Android
as "slots in the same way" ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)), and the
current shape leaves "the same way" ambiguous. An Android author has to read `WebEnvironment` and
`_DeviceEnvironment` side by side and infer three things:

- which of the three "not applicable" idioms to use for each method;
- whether a given predicate/capability pair must stay consistent;
- which crawl methods a run-only first cut can defer.

Evening out the shape now, while only two implementer families need reconciling, is cheaper than doing
it after a third lands and cements the ambiguity. `platform_lifecycle.py` is in the deterministic core,
so the change must preserve behavior: it is a shape refactor, not a change to any platform's lifecycle.
It is a size-M effort.

## Detailed design

Behavior is preserved for iOS, web, fake, and XCUITest. `start` returns the same driver, the same lease
shape is produced, and the runner and crawl command call the same methods with the same results. The
work is a set of mutually exclusive shape changes. Each can land as its own small PR, and each stays
valuable even if a later one is deferred.

- **Give "not applicable" one canonical form, and document the contract in the Protocol.** For each
  method, decide the single idiom a non-participating platform uses, and state it in the Protocol
  method's docstring (the API-surface docstring rule, BE-0065):
  - For methods the runner calls *only when* a predicate is true (`hook_collector`, gated by
    `observes_network_via_driver`), keep `raise NotImplementedError`, and make the gating contract
    explicit in the docstring ("callers must check `observes_network_via_driver()` first; a platform
    that returns False here may raise"). The docstring then documents why raising is safe, so a reader
    need not infer it.
  - For methods the runner *always* calls and interprets a null answer from (`controller` → `None` =
    no device control; `crawl_recover` / `crawl_aliveness` / `crawl_dialog_clearer` → `None` = no such
    behavior on this platform; `device_catalog` → `{}` = no devices), keep the null or empty return.
    The docstring says that the null value is a first-class "this platform has none", not an
    unimplemented stub. These methods stay as they are, and the docstring now distinguishes them from
    the raise-if-unchecked group.
- **Tie each capability method to its gating predicate in one place.** A short table (in the module
  docstring or a doc comment) pairs each predicate with the method it gates and the runner call site
  that honors the pairing, so a reader need not reconstruct "which predicate gates which method". The
  pairings are:
  - `observes_network_via_driver` with `hook_collector`;
  - `records_video_up_front` with `start`'s `record_video_dir` handling;
  - `has_devices` with `plan_lanes` / `controller`.

  This is documentation, not a type change. Encoding the gate in the type system (e.g. splitting the
  Protocol by capability) is the heavier alternative weighed below. The goal here is to make the
  convention discoverable at the definition, not scattered across call sites.
- **Separate the crawl-lease surface from the run-lease surface.** The single `Environment` Protocol
  splits into two:
  - a `RunEnvironment` (or the base `Environment`) carrying `start`, `relauncher`, `controller`,
    `teardown`, `hook_collector`, and the run predicates;
  - a `CrawlEnvironment` extension carrying `has_devices`, `plan_lanes`, `crawl_reset`,
    `crawl_aliveness`, `crawl_recover`, and `crawl_dialog_clearer`.

  The concrete implementers still satisfy both, and nothing about the classes changes. The `run` runner
  declares that it needs only `RunEnvironment`, and the crawl command declares that it needs
  `CrawlEnvironment`. Each reader then sees exactly the surface its command uses, and a run-only new
  platform can be reasoned about without the crawl methods. Whether this is a Protocol split or a
  documented sectioning is the judgment call. The split makes the boundary type-checked, which is the
  stronger form.
- **Write down the "add a platform" checklist the seam now implies.** Once the shape is even, a short
  section (in the module docstring or `docs/architecture.md`) enumerates three things: what a new
  `Environment` must implement, which methods it may leave to the gated-raise idiom, and which crawl
  methods a run-first cut may defer. This is the concrete answer to BE-0009's "slots in the same way",
  so the Android author ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)) reads the
  answer instead of inferring it. The section also keeps `docs/architecture.md` in step with the seam
  (BE-0113).

The determinism guarantees are untouched: this item moves no lifecycle logic and adds no `sleep` or
selector semantics. It is app-agnostic by construction, because evening out the Protocol serves the
app-agnostic seam. No LLM enters any path (the seam is deterministic-core code).

## Alternatives considered

- **Leave the Protocol as one surface and rely on the existing docstrings.** Rejected as the whole
  fix, though the docstring improvements above are kept. The seam is correct, but the bar is "correct
  and copyable by the next author without guessing". Today the three "not applicable" idioms and the
  by-convention predicate/capability pairing are exactly what a fresh implementer most easily gets
  wrong. Documentation alone (without the run/crawl split) narrows the gap but does not make the
  boundary type-checked.
- **Encode each capability gate in the type system.** For example, a separate `NetworkObserving`
  Protocol would alone carry `hook_collector`. A platform that does not observe network via the driver
  would simply not implement it, and the runner would narrow the type before calling. This is
  attractive because it removes the gated-raise idiom entirely. It is rejected for this item's scope,
  because it changes more about how the runner holds and narrows the `Environment`, and it risks
  over-fragmenting the Protocol into many single-method interfaces. It is worth reconsidering if the
  run/crawl split shows that the finer-grained direction reads well. This item takes the one clear
  split (run vs. crawl) and documents the rest.
- **Defer all of this until Android is actually being built**
  ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)). Rejected on the logic BE-0009
  used to justify doing Phase 0 before any second platform: the ambiguity is cheapest to remove while
  only two implementer families need reconciling. Doing it during the Android work would force the
  Android author to both learn the ambiguous shape and fix it. That would couple a cleanup to a feature
  and make the Android PR larger and harder to review.
- **Fold this into BE-0009.** Rejected, because BE-0009 is Implemented: its Phase 0 job (creating the
  seam) is done and its PRs are merged. Reopening a shipped item to add follow-on shape work would blur
  what "Implemented" means. This item is a distinct, later refinement of the seam BE-0009 created, so
  it stands alone and references BE-0009 instead of editing it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Give "not applicable" one canonical form per method and document each method's contract
      (gated-raise vs. first-class null) on the Protocol
- [x] Pair each capability method with its gating predicate and its runner call site in one place
- [x] Split the `Environment` Protocol into a run-lease surface and a crawl-lease surface, and have
      each command declare the narrower type it needs
- [x] Write the "add a platform" checklist (module docstring or `docs/architecture.md`), keeping the
      doc in step with the seam

Log:

- [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816) — Split `Environment` into `RunEnvironment` / `CrawlEnvironment` / combined `Environment`
  in `platform_lifecycle.py`; the `run` pipeline (`runner/pool.py`, `runner/launch.py`) now holds a
  `RunEnvironment` and the `crawl` command (`cli/commands/crawl.py`) a `CrawlEnvironment`. Documented
  the two-idiom "not applicable" contract per method, the predicate→capability pairing table, and the
  "adding a platform" checklist in the module docstring. Behavior preserved; a seam test locks in that
  every concrete environment satisfies both surfaces.

## References

- `bajutsu/platform_lifecycle.py:47`–`121` (the `Environment` Protocol; the run/crawl comment
  boundary at `:97`)
- `bajutsu/platform_lifecycle.py:124`–`195` (`_DeviceEnvironment`: the `raise NotImplementedError`
  at `:146`, the `None`-returning crawl methods at `:187`–`194`)
- `bajutsu/platform_lifecycle.py:250`–`359` (`WebEnvironment`: the `{}` / `False` / `None` no-ops
  and the `observes_network_via_driver` → `hook_collector` gate)
- `bajutsu/platform_lifecycle.py:542` (`environment_for`, the factory a new platform extends)
- Extends [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)
  Phase 0 (the seam this evens out), in service of
  [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) (the Android backend that will be
  the first new implementer); see also
  [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) (the web backend
  whose v1 shortcut this shape descends from) and
  [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) (the XCUITest environment on the
  device-style base)
- [docs/architecture.md](../../docs/architecture.md) (kept in step with the seam per BE-0113)
