**English** · [日本語](BE-XXXX-environment-protocol-shape-ja.md)

# BE-XXXX — Even out the Environment protocol shape for a third platform

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-environment-protocol-shape.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) Phase 0
introduced the `Environment` Protocol that ended the runner's per-actuator branching: one interface
whose `start` runs a platform's whole per-run bring-up and whose lease-shaping methods let the
runner drive iOS and web through the same surface. That seam shipped, and it works. But the way the
current implementers satisfy the Protocol is uneven, and the unevenness is exactly what a third
platform (Android, [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)) will have to
copy or guess at. This item does *not* change the seam's behavior; it evens out the Protocol's shape
so that "what must a new `Environment` provide, and how does it decline what does not apply" has a
single, self-evident answer before Android arrives.

## Motivation

`bajutsu/platform_lifecycle.py` defines the `Environment` Protocol (`:47`) and three families of
implementer: the device-style `_DeviceEnvironment` base (`:124`) with `IosEnvironment` /
`FakeEnvironment` / `XcuitestEnvironment` on top of it, and the standalone `WebEnvironment`
(`:250`) that implements the Protocol directly. The seam is correct, but three inconsistencies make
it harder than it should be to add the next implementer:

- **"Not applicable" is expressed three different ways.** A Protocol method that a given platform
  has no use for is variously a silent `return None` (`WebEnvironment.controller`,
  `_DeviceEnvironment.crawl_recover` / `crawl_aliveness` / `crawl_dialog_clearer`), a `return {}` /
  `return False` no-op (`_DeviceEnvironment.records_video_up_front`,
  `WebEnvironment.device_catalog`), or a `raise NotImplementedError`
  (`_DeviceEnvironment.hook_collector`, at `platform_lifecycle.py:146`). The three are not
  interchangeable: `hook_collector` raising is only safe because the runner is supposed to call it
  *only when* `observes_network_via_driver()` is true, but that contract lives in a one-line
  docstring, not in the types. A new implementer cannot tell from the Protocol which methods it may
  leave to raise, which it must make a real no-op, and which the runner will actually call on it.

- **Two feature-flag methods gate three capability methods, by convention only.** The Protocol
  bundles predicate methods (`observes_network_via_driver`, `records_video_up_front`,
  `has_devices`) with the capability methods those predicates gate (`hook_collector`,
  `controller`, the crawl methods). Nothing in the type system ties `hook_collector` to
  `observes_network_via_driver() == True`, so the safe-only-if-you-checked-the-flag relationship is
  a rule a reader has to reconstruct from prose and from reading the runner's call sites. This is
  the kind of "capability advertised by a boolean, honored by convention" shape that is easy to get
  subtly wrong from a fresh implementer.

- **The Protocol mixes `run`-lease methods with crawl-only methods.** The lower half of the
  Protocol (`has_devices`, `plan_lanes`, `crawl_reset`, `crawl_aliveness`, `crawl_recover`,
  `crawl_dialog_clearer`, marked off by the comment at `:97`) exists for the crawl command, while
  the upper half (`start`, `relauncher`, `controller`, `teardown`, `hook_collector`, the video/
  network predicates) serves the `run` lease. A single Protocol carrying both means every
  implementer must answer crawl questions even when the reader is only interested in `run`, and a
  future consumer that only needs the run lease still depends on the whole surface. The two
  concerns were folded into one Protocol incrementally (BE-0009 slices 2 and 3), which is how they
  ended up together; nothing about them requires it.

None of this is a bug — the seam behaves correctly today. But BE-0009 explicitly frames Android as
"slots in the same way" ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)), and the
current shape makes "the same way" ambiguous: an Android author has to read `WebEnvironment` and
`_DeviceEnvironment` side by side and infer which of the three "not applicable" idioms to use for
each method, whether a given predicate/capability pair must be kept consistent, and which crawl
methods a run-only first cut can defer. Evening out the shape now — while there are only two
implementer families to reconcile — is cheaper than doing it after a third lands and cements the
ambiguity. `platform_lifecycle.py` is in the deterministic core, so this is behavior-preserving by
necessity: it is a shape refactor, not a change to any platform's lifecycle. It is a size-M effort.

## Detailed design

Behavior is preserved for iOS, web, fake, and XCUITest: the same driver comes back from `start`,
the same lease shape is produced, and the runner and crawl command call the same methods with the
same results. The work is a set of mutually exclusive shape changes; each can land as its own small
PR, and each is independently valuable even if a later one is deferred.

- **Give "not applicable" one canonical form, and document the contract in the Protocol.** Decide,
  per method, the single idiom a non-participating platform uses, and state it on the Protocol
  method's docstring (the API-surface docstring rule, BE-0065):
  - For methods the runner calls *only when* a predicate is true (`hook_collector` gated by
    `observes_network_via_driver`), keep `raise NotImplementedError` but make the gating contract
    explicit in the docstring ("callers must check `observes_network_via_driver()` first; a
    platform that returns False here may raise"). This documents why raising is safe rather than
    leaving it to a reader to infer.
  - For methods the runner *always* calls and interprets a null answer from (`controller` →
    `None` = no device control; `crawl_recover` / `crawl_aliveness` / `crawl_dialog_clearer` →
    `None` = no such behavior on this platform; `device_catalog` → `{}` = no devices), keep the
    null/empty return but say in the docstring that the null value is a first-class "this platform
    has none," not an unimplemented stub. These stay as they are; the win is that the docstring now
    distinguishes them from the raise-if-unchecked group.
- **Tie each capability method to its gating predicate in one place.** Rather than the reader
  reconstructing "which predicate gates which method," add a short table (in the module docstring or
  a doc comment) that pairs each predicate with the method it gates and the runner call site that
  honors the pairing: `observes_network_via_driver` with `hook_collector`, `records_video_up_front`
  with `start`'s `record_video_dir` handling, `has_devices` with `plan_lanes` / `controller`. This
  is documentation, not a type change, because encoding the gate in the type system (e.g. splitting
  the Protocol by capability) is the heavier alternative weighed below; the goal here is to make the
  convention discoverable at the definition, not scattered across call sites.
- **Separate the crawl-lease surface from the run-lease surface.** Split the single `Environment`
  Protocol into two: a `RunEnvironment` (or the base `Environment`) carrying `start`, `relauncher`,
  `controller`, `teardown`, `hook_collector`, and the run predicates; and a `CrawlEnvironment`
  extension carrying `has_devices`, `plan_lanes`, `crawl_reset`, `crawl_aliveness`,
  `crawl_recover`, `crawl_dialog_clearer`. The concrete implementers still satisfy both (nothing
  about the classes changes), but the `run` runner declares it needs only `RunEnvironment` and the
  crawl command declares it needs `CrawlEnvironment`, so each reader sees exactly the surface its
  command uses and a run-only new platform can be reasoned about without the crawl methods. Whether
  this is a Protocol split or a documented sectioning is the judgment call; the split makes the
  boundary type-checked, which is the stronger form.
- **Write down the "add a platform" checklist the seam now implies.** Once the shape is even, add a
  short section (in the module docstring or `docs/architecture.md`) enumerating what a new
  `Environment` must implement, which methods it may leave to the gated-raise idiom, and which crawl
  methods a run-first cut may defer — the concrete answer to BE-0009's "slots in the same way," so
  the Android author ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)) reads it
  instead of inferring it. This keeps `docs/architecture.md` in step with the seam (BE-0113).

The determinism guarantees are untouched: this item moves no lifecycle logic and adds no `sleep` or
selector semantics. It is app-agnostic by construction — evening out the Protocol is precisely in
service of the app-agnostic seam. No LLM enters any path (the seam is deterministic-core code).

## Alternatives considered

- **Leave the Protocol as one surface and rely on the existing docstrings.** Rejected as the whole
  fix, though the docstring improvements above are kept: the seam is correct, but "correct and
  copyable by the next author without guessing" is the bar, and today the three "not applicable"
  idioms and the by-convention predicate/capability pairing are exactly what a fresh implementer
  most easily gets wrong. Documentation alone (without the run/crawl split) narrows the gap but does
  not make the boundary type-checked.
- **Encode each capability gate in the type system** — e.g. a separate `NetworkObserving` Protocol
  that alone carries `hook_collector`, so a platform that does not observe network via the driver
  simply does not implement it, and the runner narrows the type before calling. Attractive because
  it removes the gated-raise idiom entirely, but rejected for this item's scope: it is a larger
  change to how the runner holds and narrows the `Environment`, and risks over-fragmenting the
  Protocol into many single-method interfaces. Worth reconsidering if the run/crawl split proves the
  finer-grained direction reads well; this item takes the one clear split (run vs. crawl) and
  documents the rest.
- **Defer all of this until Android is actually being built** ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)).
  Rejected on the same logic BE-0009 used to justify doing Phase 0 before any second platform: the
  ambiguity is cheapest to remove while only two implementer families exist to reconcile. Doing it
  during the Android work would force the Android author to both learn the ambiguous shape and fix
  it, coupling a cleanup to a feature and making the Android PR larger and harder to review.
- **Fold this into BE-0009.** Rejected because BE-0009 is Implemented (its Phase 0 job — creating
  the seam — is done and its PRs are merged); reopening a shipped item to add follow-on shape work
  would blur what "Implemented" means. This is a distinct, later refinement of the seam BE-0009
  created, so it is its own item that references BE-0009 rather than an edit to it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Give "not applicable" one canonical form per method and document each method's contract
      (gated-raise vs. first-class null) on the Protocol
- [ ] Pair each capability method with its gating predicate and its runner call site in one place
- [ ] Split the `Environment` Protocol into a run-lease surface and a crawl-lease surface, and have
      each command declare the narrower type it needs
- [ ] Write the "add a platform" checklist (module docstring or `docs/architecture.md`), keeping the
      doc in step with the seam

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
