**English** · [日本語](BE-0141-backend-lifecycle-protocol-ja.md)

# BE-0141 — Bring backend lifecycle into the type system

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0141](BE-0141-backend-lifecycle-protocol.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0141") |
| Topic | Platform expansion (Android / Web / Flutter) |
| Related | [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md), [BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/drivers/base.py` defines `Driver`, the `Protocol` every backend implements
(`base.py:91`) so the runner can drive iOS, web, and (eventually) Android through one interface.
But four lifecycle operations a backend needs around a run — `navigate`, `close`, `reset_context`,
`await_ready` — are not part of that Protocol. They live only on the concrete backend classes
(`PlaywrightDriver`, `XcuitestDriver`) and are reached from `bajutsu/environment.py` through
`# type: ignore[attr-defined]` escapes. This proposal brings those operations into the type system,
either as a second `Protocol` or by making the environment generic over the driver type.

## Motivation

`environment.py` calls four lifecycle methods that `Driver` (`base.py:91-116`) does not declare:

- `driver.navigate()` — `environment.py:276` (web start) and `environment.py:589` (web relaunch),
  both `# type: ignore[attr-defined]  # web-only lifecycle`.
- `driver.close()` — `environment.py:309`, `# type: ignore[attr-defined]  # web-only lifecycle,
  confined to this env`.
- `driver.reset_context()` — `environment.py:323`, `# type: ignore[attr-defined]  # web-only
  lifecycle (fresh context)`.
- `driver.await_ready()` — `environment.py:486`, `# type: ignore[attr-defined]  # xcuitest-only
  lifecycle`.

These five call sites are exactly the ones implementing `navigate`/`close`/`reset_context` on
`PlaywrightDriver` (`bajutsu/drivers/playwright.py:383,388,434`) and `await_ready` on `XcuitestDriver`
(`bajutsu/drivers/xcuitest.py:259`). The repository has 16 `type: ignore` comments in `bajutsu/` source
(excluding tests); **5 of them — nearly a third — are these four lifecycle calls concentrated in
one file**, which the codebase-analysis report separately flagged as tech-debt finding #5. That
concentration is itself the signal: it is not scattered incidental type-checking noise, it is one
recurring shape (a lifecycle method absent from the interface the caller is typed against) reached
for four different methods. mypy is strict project-wide, so each of these is a deliberate, repeated
escape rather than an oversight — and the pattern the escape works around (a lifecycle call the
`Driver` Protocol doesn't know about, reached via a comment instead of a type) will recur verbatim
for Android's own startup/teardown sequence.

The severity is **medium**: none of these five sites is wrong today — each is narrowly commented
with which platform it belongs to, and `cast(PlaywrightDriver, driver)` is used correctly elsewhere
in the same file (e.g. `environment.py:293,334,344,354`) for read-style calls. The risk is
regression safety and onboarding cost: a `# type: ignore` silences mypy for that line rather than
proving the call is safe, so a refactor that moves a lifecycle call to a driver that doesn't
implement it fails at runtime (`AttributeError`) instead of at `make check` time — the opposite of
what "mypy is strict" is meant to guarantee for the rest of the codebase.

## Detailed design

Two complementary changes close the gap; both are needed for the fix to be complete, and each
targets a different half of the problem (the missing type versus the awkward call-site cast):

1. **Introduce a `Lifecycle` Protocol** in `bajutsu/drivers/base.py`, alongside `Driver` and
   `EvidenceProvider`, declaring the four operations actually used:
   `navigate() -> None`, `close() -> None`, `reset_context() -> None`, `await_ready(timeout: float
   = ..., poll: float = ...) -> None`. Following `EvidenceProvider`'s existing precedent
   (`base.py:119-133`) as a narrow, `@runtime_checkable` Protocol a backend opts into rather than a
   mandatory extension of `Driver` — a backend without a lifecycle need (idb's `simctl` sequence
   drives boot/erase/install outside the driver entirely) is not forced to implement no-op methods.
   The name must not collide with the sibling `module-naming-debt` proposal (environment and config
   module naming debt) — if that item's `environment.py` rename lands first, this Protocol needs a
   name other than `Lifecycle` (e.g. `BackendLifecycle` or `DriverLifecycle`).
2. **Type the call sites through the Protocol, not `attr-defined` suppression.** Each of the five
   sites in `environment.py` (276, 309, 323, 486, 589) changes from
   `driver.navigate()  # type: ignore[attr-defined]` to `cast(Lifecycle, driver).navigate()` (or,
   where the concrete type is already known in scope, a direct call on that type) — mirroring the
   `cast(PlaywrightDriver, driver)` pattern the same file already uses correctly for other
   web-specific calls. This makes the *existence* of `navigate`/`close`/etc. on the target a
   type-checked fact instead of an assertion in a comment; a backend that stops implementing one of
   these methods now fails `make check` (mypy strict) rather than only at run time.
3. **Alternative worth scoping before committing: `Environment[D]` generic over the driver type.**
   If, during implementation, most `Lifecycle` calls turn out to be scoped to one environment class
   each (`WebEnvironment` only ever lifecycle-calls `PlaywrightDriver`,
   the XCUITest-driving environment only ever calls `XcuitestDriver`), making the environment class
   itself generic over its driver type (`class WebEnvironment(Generic[D])` with `D` bound to
   `PlaywrightDriver`) may remove the need for a cast at each call site entirely, since the driver's
   concrete type would already be known within that class. The implementer picks between the
   `Lifecycle` Protocol and the generic-environment approach (or both, if some calls are
   cross-cutting and others aren't) based on which produces fewer casts once the actual call sites
   are mapped out — this is a design decision to make with the code in front of the implementer,
   not to prescribe up front.
4. **Verify no regression in behavior.** This is a typing-only change (prime directive constraints
   are untouched — no runtime branching changes); `make check` (mypy strict + the existing test
   suite) is the complete verification, with no new test scenarios needed beyond confirming the
   five call sites still resolve to the same concrete methods.

## Alternatives considered

- **Leave the `type: ignore`s as documentation.** Each is already commented with which platform it
  belongs to, so a reader isn't confused about intent. But a comment is not checked by mypy: it
  survives a refactor that breaks the assumption it documents, exactly the failure mode `make check`
  exists to catch everywhere else in the codebase.
- **Fold `navigate`/`close`/`reset_context`/`await_ready` directly into the `Driver` Protocol**,
  making every backend implement them (idb's `Driver` implementation would gain no-op stubs). Rejected:
  it forces platform-specific lifecycle concerns onto backends (like idb) that don't have them,
  which is the same "one flat interface accumulates every platform's concerns" problem the sibling
  `per-platform-effective-config` proposal (splitting `Effective` into per-platform configs)
  addresses for config — consistency across the two items favors a narrower, opt-in Protocol here
  too.
- **`hasattr`/`getattr` at each call site instead of `cast`.** Removes the `type: ignore` but trades
  a static guarantee for a runtime check that duck-types past the type system entirely — worse than
  today's narrowly-scoped `type: ignore`, not better.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Introduce a `Lifecycle` (name TBD, pending the sibling naming-debt item) Protocol in `bajutsu/drivers/base.py`.
- [ ] Migrate the five `type: ignore[attr-defined]` call sites in `environment.py` to a typed cast against the Protocol.
- [ ] Evaluate and, if it reduces casts, adopt `Environment[D]` generic over the driver type.
- [ ] Confirm `make check` (mypy strict + full test suite) passes with no behavior change.

No PR has landed yet.

## References

- `bajutsu/drivers/base.py:90-134` — the `Driver` and `EvidenceProvider` Protocols; `Lifecycle`
  follows the latter's narrow, opt-in shape.
- `bajutsu/environment.py:276,309,323,486,589` — the five `# type: ignore[attr-defined]` lifecycle
  call sites.
- `bajutsu/environment.py:293,334,344,354` — existing `cast(PlaywrightDriver, driver)` call sites
  this proposal's pattern mirrors.
- `bajutsu/drivers/playwright.py:383,388,434` — `PlaywrightDriver.navigate` / `reset_context` /
  `close`.
- `bajutsu/drivers/xcuitest.py:259` — `XcuitestDriver.await_ready`.
- 16 `type: ignore` comments total in `bajutsu/` source (excluding `tests/`); 5 are the sites above.
- Related roadmap items: [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)
  (cross-platform abstractions), [BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md)
  (platform backend registry), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)
  (Android backend).
- Originates from the 2026-07-02 codebase-analysis report (design); also tech-debt finding #5 in
  the same report.
