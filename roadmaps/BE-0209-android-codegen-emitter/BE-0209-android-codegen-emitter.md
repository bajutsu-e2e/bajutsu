**English** · [日本語](BE-0209-android-codegen-emitter-ja.md)

# BE-0209 — Android codegen emitter (Espresso / UI Automator)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0209](BE-0209-android-codegen-emitter.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0209") |
| Topic | codegen coverage |
| Related | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu transpiles a scenario into a native test for two targets — XCUITest for iOS and Playwright
for the web (`EMIT_TARGETS = ("xcuitest", "playwright")` in `bajutsu/codegen_emit.py`). The Android
backend ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)) has no codegen target,
so a scenario cannot be emitted as a native Android test. This item adds an Android emitter (Kotlin,
Espresso or UI Automator) as the third target, closing the codegen half of the iOS-vs-Android parity
gap.

## Motivation

[BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md) already
extracted the shared scenario walk so a new emitter supplies only its per-line target syntax — and
it named "adding an Android codegen target" as the motivating third emitter. The unification work
is done, so this item is the small, structural remainder: a per-line emitter over the existing walk,
not a new traversal. Codegen is a deterministic, LLM-free path (a structural mapping from scenario
to source), so it stays off the `run`/CI gate and within the prime directives.

## Detailed design

### The target dialect

Espresso (`onView(withId(...)).perform(...)`) and UI Automator
(`device.findObject(By.res(...)).click()`) are both candidates. UI Automator maps more directly onto
the adb driver's coordinate/id model and its cross-process, black-box view of the app, so it is the
closer twin of what the backend actually does; Espresso reads more idiomatically for
instrumentation tests against a known app. The dialect choice is the one design decision, made when
the emitter lands.

### Work breakdown (MECE)

1. **Choose the dialect** (UI Automator vs Espresso) and record the rationale.
2. **Per-line emitter** (`bajutsu/codegen_espresso.py`, or a UI-Automator-named module) built on
   BE-0083's shared walk: the launch line, each step (`tap` / `type` / `swipe` / system back /
   deeplink), and the `expect` block, in the chosen Kotlin dialect.
3. **Selector mapping** to `resource-id` / `text` / `content-desc`, mirroring the adb driver's own
   mapping so the generated test resolves elements the same way the driver does.
4. **Register the target** in `EMIT_TARGETS` and the `codegen_emit` dispatch, guarded to an Android
   target (as the Playwright target is guarded to a web target).
5. **Validation**. Golden-output tests over scenario fixtures, byte-for-byte, as the XCUITest and
   Playwright emitters are tested — a pure fast-gate check, no device.

## Alternatives considered

- **Espresso vs UI Automator** — captured above as the dialect decision, not a separate landing.
- **An Appium client codegen target (Python/Java).** Deferred: it pairs with the Appium actuator
  alternative that BE-0007 also defers. The first Android emitter matches the shipped adb backend's
  model, so UI Automator / Espresso is the natural first target.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Choose the dialect (UI Automator vs Espresso) with rationale.
- [ ] Per-line emitter on BE-0083's shared walk (`bajutsu/codegen_espresso.py`).
- [ ] Selector mapping to `resource-id` / `text` / `content-desc`.
- [ ] Register the target in `EMIT_TARGETS` / `codegen_emit`, guarded to an Android target.
- [ ] Validation — byte-for-byte golden-output tests over scenario fixtures.

## References

[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0083 — Unify the codegen emitters behind a shared scenario walk](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md),
`bajutsu/codegen_emit.py`
