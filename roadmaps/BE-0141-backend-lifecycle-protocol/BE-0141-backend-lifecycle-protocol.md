**English** · [日本語](BE-0141-backend-lifecycle-protocol-ja.md)

# BE-0141 — Bring backend lifecycle into the type system

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0141](BE-0141-backend-lifecycle-protocol.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0141") |
| Implementing PR | [#671](https://github.com/bajutsu-e2e/bajutsu/pull/671) |
| Topic | Driver & backend architecture |
| Related | [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md), [BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/drivers/base.py` defines `Driver` (`base.py:91`), the `Protocol` that every backend
implements. The runner drives iOS, web, and (eventually) Android through that one interface. But a
backend needs four lifecycle operations around a run, and the `Driver` Protocol does not include
them: `navigate`, `close`, `reset_context`, and `await_ready`. The operations live on the concrete
backend classes (`PlaywrightDriver`, `XcuitestDriver`) alone. `bajutsu/environment.py` reaches them
through `# type: ignore[attr-defined]` escapes.

This proposal brings the four operations into the type system. It does so either with a second
`Protocol` or by making the environment generic over the driver type.

## Motivation

`environment.py` calls four lifecycle methods that `Driver` (`base.py:91-116`) does not declare:

- `driver.navigate()` — `environment.py:276` (web start) and `environment.py:589` (web relaunch).
  Both carry `# type: ignore[attr-defined]  # web-only lifecycle`.
- `driver.close()` — `environment.py:309`, with `# type: ignore[attr-defined]  # web-only
  lifecycle, confined to this env`.
- `driver.reset_context()` — `environment.py:323`, with `# type: ignore[attr-defined]  # web-only
  lifecycle (fresh context)`.
- `driver.await_ready()` — `environment.py:486`, with `# type: ignore[attr-defined]  # xcuitest-only
  lifecycle`.

These five call sites match the implementations exactly. `navigate`, `close`, and `reset_context`
are implemented on `PlaywrightDriver` (`bajutsu/drivers/playwright.py:383,388,434`). `await_ready`
is implemented on `XcuitestDriver` (`bajutsu/drivers/xcuitest.py:259`).

The repository has 16 `type: ignore` comments in `bajutsu/` source, excluding tests. **Five of
them, nearly a third, are these four lifecycle calls concentrated in one file.** The
codebase-analysis report separately flagged them as tech-debt finding #5.

The concentration is itself the signal. It is not scattered incidental type-checking noise. It is
one recurring shape: a lifecycle method is absent from the interface that the caller is typed
against, and the shape recurs across four different methods. Mypy is strict project-wide, so each
escape is deliberate and repeated, not an oversight.

The pattern the escape works around will recur verbatim for Android's own startup and teardown
sequence. The pattern is a lifecycle call that the `Driver` Protocol does not know about, reached
through a comment instead of a type.

The severity is **medium**. None of the five sites is wrong today. Each carries a narrow comment
saying which platform it belongs to. The same file also uses `cast(PlaywrightDriver, driver)`
correctly for read-style calls (for example `environment.py:293,334,344,354`).

The risk is regression safety and onboarding cost. A `# type: ignore` silences mypy for that line
and does not prove that the call is safe. Suppose a refactor moves a lifecycle call to a driver that
does not implement it. The failure then appears at runtime (`AttributeError`) instead of at
`make check` time. That is the opposite of what "mypy is strict" is meant to guarantee for the rest
of the codebase.

## Detailed design

Two complementary changes close the gap. Each targets a different half of the problem: the missing
type, and the awkward call-site cast. Both are needed for the fix to be complete.

1. **Introduce a `Lifecycle` Protocol** in `bajutsu/drivers/base.py`, alongside `Driver` and
   `EvidenceProvider`. The Protocol declares the four operations in use: `navigate() -> None`,
   `close() -> None`, `reset_context() -> None`, and `await_ready(timeout: float = ..., poll: float
   = ...) -> None`.

   The Protocol follows the precedent of `EvidenceProvider` (`base.py:119-133`). It is a narrow,
   `@runtime_checkable` Protocol that a backend opts into, not a mandatory extension of `Driver`.
   A backend without a lifecycle need is then not forced to implement no-op methods. The idb
   backend is one such case: its `simctl` sequence drives boot, erase, and install outside the
   driver entirely.

   The name must not collide with the sibling `module-naming-debt` proposal (environment and config
   module naming debt). Suppose that item's `environment.py` rename lands first. This Protocol then
   needs a name other than `Lifecycle`, for example `BackendLifecycle` or `DriverLifecycle`.
2. **Type the call sites through the Protocol, not through `attr-defined` suppression.** Each of the
   five sites in `environment.py` (276, 309, 323, 486, 589) changes from
   `driver.navigate()  # type: ignore[attr-defined]` to `cast(Lifecycle, driver).navigate()`. Where
   the concrete type is already known in scope, the site calls that type directly instead.

   The change mirrors the `cast(PlaywrightDriver, driver)` pattern that the same file already uses
   correctly for other web-specific calls. The *existence* of `navigate`, `close`, and the other
   methods on the target becomes a type-checked fact instead of an assertion in a comment. Suppose a
   backend stops implementing one of these methods. The failure now appears in `make check` (mypy
   strict), not merely at run time.
3. **Scope an alternative before committing: `Environment[D]`, generic over the driver type.**
   During implementation, most `Lifecycle` calls may turn out to be scoped to one environment class
   each. `WebEnvironment` would only ever lifecycle-call `PlaywrightDriver`. The XCUITest-driving
   environment would only ever call `XcuitestDriver`. In that case, the environment class itself
   can become generic over its driver type: `class WebEnvironment(Generic[D])`, with `D` bound to
   `PlaywrightDriver`. The driver's concrete type would then already be known within the class, and
   the cast at each call site may become unnecessary.

   The implementer picks between the `Lifecycle` Protocol and the generic-environment approach.
   Using both is also possible, if some calls are cross-cutting and others are not. The deciding
   criterion is which approach produces fewer casts once the actual call sites are mapped out. This
   is a design decision to make with the code in front of the implementer, not to prescribe up
   front.
4. **Verify that behavior does not regress.** This is a typing-only change. The prime-directive
   constraints are untouched, and no runtime branching changes. `make check` (mypy strict plus the
   existing test suite) is the complete verification. No new test scenarios are needed beyond
   confirming that the five call sites still resolve to the same concrete methods.

## Alternatives considered

- **Leave the `type: ignore`s as documentation.** Each is already commented with the platform it
  belongs to, so a reader is not confused about intent. But mypy does not check a comment. The
  comment survives a refactor that breaks the assumption it documents. That is exactly the failure
  mode that `make check` exists to catch everywhere else in the codebase.
- **Fold `navigate`, `close`, `reset_context`, and `await_ready` directly into the `Driver`
  Protocol, so that every backend implements them.** The idb `Driver` implementation would gain
  no-op stubs. Rejected. The change forces platform-specific lifecycle concerns onto backends (like
  idb) that do not have them. The sibling `per-platform-effective-config` proposal (splitting
  `Effective` into per-platform configs) addresses the same problem for config: one flat interface
  accumulates every platform's concerns. Consistency across the two items favors a narrower,
  opt-in Protocol here too.
- **Use `hasattr` / `getattr` at each call site instead of `cast`.** This removes the `type: ignore`
  but trades a static guarantee for a runtime check. The runtime check duck-types past the type
  system entirely. That is worse than today's narrowly scoped `type: ignore`, not better.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Introduce a `BackendLifecycle` Protocol in `bajutsu/drivers/base.py`. Named `BackendLifecycle`, not `Lifecycle`: the sibling module-naming-debt item already landed, renaming `environment.py` to `platform_lifecycle.py`, so `Lifecycle` would read ambiguously next to that module.
- [x] Migrate the five `type: ignore[attr-defined]` call sites in `platform_lifecycle.py` to a typed `cast(BackendLifecycle, driver)`.
- [x] Evaluate `Environment[D]` generic over the driver type — **not adopted**. The four hooks split disjointly across backends (web owns `navigate`/`close`/`reset_context`, XCUITest owns `await_ready`), and making the `Environment` classes generic would ripple into `environment_for`'s return type and every consumer in the runner/pool — well outside a type-only fix. The opt-in Protocol plus the file's existing `cast(PlaywrightDriver, driver)` idiom keeps the change focused and precise.
- [x] Confirm `make check` (mypy strict + full test suite) passes with no behavior change.

- 2026-07-05: Added the `@runtime_checkable BackendLifecycle` Protocol, migrated the five call sites, and added Protocol-conformance tests ([#671](https://github.com/bajutsu-e2e/bajutsu/pull/671)).

## References

- `bajutsu/drivers/base.py:90-134` — the `Driver` and `EvidenceProvider` Protocols. `Lifecycle`
  follows the narrow, opt-in shape of `EvidenceProvider`.
- `bajutsu/environment.py:276,309,323,486,589` — the five `# type: ignore[attr-defined]` lifecycle
  call sites.
- `bajutsu/environment.py:293,334,344,354` — existing `cast(PlaywrightDriver, driver)` call sites
  that this proposal's pattern mirrors.
- `bajutsu/drivers/playwright.py:383,388,434` — `PlaywrightDriver.navigate` / `reset_context` /
  `close`.
- `bajutsu/drivers/xcuitest.py:259` — `XcuitestDriver.await_ready`.
- 16 `type: ignore` comments in total in `bajutsu/` source (excluding `tests/`). Five of them are
  the sites above.
- Related roadmap items: [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)
  (cross-platform abstractions), [BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md)
  (platform backend registry), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)
  (Android backend).
- Originates from the 2026-07-02 codebase-analysis report (design). The same report lists it as
  tech-debt finding #5.
