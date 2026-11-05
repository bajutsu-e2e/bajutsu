**English** · [日本語](BE-0265-text-editing-steps-ja.md)

# BE-0265 — Text-editing steps: select, clear, delete, copy

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0265](BE-0265-text-editing-steps.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0265") |
| Implementing PR | [#1114](https://github.com/bajutsu-e2e/bajutsu/pull/1114) |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

The sole text-entry [step](../../docs/glossary.md#scenario-authoring) today is `type`
(`bajutsu/orchestrator/actions/handlers/gestures.py`). It appends characters through
`driver.type_text()` on every backend. A scenario cannot clear a field, delete part of its content,
select text, or copy a selection to the clipboard.

This item adds four steps that close that gap: `select`, `clear`, `delete`, and `copy`. The steps
stay inside the existing determinism model.

## Motivation

Real forms are edited, not filled once. A user clears a field and retypes, backspaces a typo,
selects a value to copy it elsewhere, or replaces part of an existing value. None of that is
expressible in a scenario today. The sole workaround is a strained sequence of `tap` and
`longPress` guesses with no defined outcome. That is the non-determinism that the driver
abstraction exists to avoid.

The natural design hazard is to treat "selection" as a piece of state: *which characters are
currently selected*. A step could set that state, and a later assertion could check it. No backend
exposes selection as queryable state:

- `Element` (`DESIGN.md` §5) carries `value` but no selection range.
- None of idb, adb, and XCUITest exposes a selection query.

Modeling `select` as state would need new, backend-specific state that no `query()` can verify. That
conflicts with the determinism-first directive (prime directive 2) and with the "ambiguity fails
rather than guesses" contract that the selector semantics already enforce.

The fix is to keep `select` (and `copy`) as **actions**, not state. Given the same field content and
the same fixed step parameters, the action is deterministic. *Its own outcome is never the thing
asserted.* Verification lands on what happens next:

- A `type` that replaces the selection is verified by the existing `value` assertion.
- A `copy` is verified through the clipboard read-back that BE-0052 already shipped
  (`expect: - clipboard: { equals | matches }`).

`clear` and `delete` need no new assertion at all. Both are backspace-shaped mutations of `value`.
`value` is already an assertable `Element` field and an existing `Assertion` case
(`bajutsu/scenario/models/assertions.py`).

## Detailed design

Four new steps follow the existing `type` shape. `into` selects the target field first, and then the
step's own effect runs.

```yaml
- select: { into: form.note, mode: all }   # select the field's full current content
- copy: {}                                  # copy the active selection to the clipboard
- clear: { into: form.note }                # clear the field's full content
- delete: { into: form.note, count: 3 }     # delete the last 3 characters
```

- **`select`** supports `mode: all` only, in this item. It performs a platform "select all" action:
  a hardware ⌘A key event on the iOS Simulator, long-press ▸ "Select All" on Android, and `Ctrl+A`
  or triple-click on web. Word-level and range-level selection (`mode: word`, offset-based ranges)
  are deliberately out of scope.
  - They need drag-handle or cursor-position primitives that no backend exposes today.
  - The boundary semantics (what counts as a "word") depend on locale and OS.
  - The result is a significantly bigger, less deterministic surface. A later item can scope it if a
    concrete need shows up.
- **`copy`** triggers the platform's "Copy" action on whatever is currently selected. It has no
  target selector of its own, because it acts on the active selection that `select` produced. The
  existing clipboard read-back verifies its result. The step introduces no new state.
  - **When nothing is selected**, `copy` fails the step deterministically. Nothing is selected when
    no `select` came before, or when a step cleared the selection. `copy` neither silently copies
    nothing nor leaves the clipboard's prior contents in place.
  - A "copy" with no selection is a scenario-authoring mistake. Per prime directive 2, it must
    surface as a failure, not as a silent no-op that a later clipboard assertion might misread as
    success.
  - The check sits on Bajutsu's side: was a selection established since the last
    selection-clearing action? It is not delegated to whatever each backend's native copy does with
    an empty selection. The failure is identical across backends.
- **`clear`** clears the named field's entire content. `Element.value`, which `query()` already
  returns, gives the current string. The actuation removes exactly that many characters
  (backspace-equivalent), not a guessed fixed count. It is therefore agnostic to whatever content
  the field already held.
- **`delete`** removes `count` characters from the end of the field's current value. It uses the
  same backspace-equivalent mechanism as `clear`, bounded to a smaller count. It serves a scripted
  typo correction or a partial edit without clearing the whole field.

**Per-backend actuation.** The concrete mechanism is a per-backend implementation detail, finalized
at build time. This mirrors BE-0052's own experience, where two of four primitives needed
implementation-time triage to find a mechanism that holds up. The directions to evaluate are:

- **idb.** `driver.type_text()` already reaches the focused field over `idb_companion`'s text gRPC
  call (`bajutsu/drivers/idb.py`). Implementation-time verification must show whether control
  characters (for example `\b`) are honored the way a hardware backspace key would be. If they are
  not, the fallback is a companion HID key-event call, as opposed to the text-injection call.
- **adb.** Run `input keyevent KEYCODE_DEL` (67) `count` times, the same way the existing
  `KEYCODE_BACK` call runs (`bajutsu/adb.py`). `select`'s "select all" has no single keyevent
  equivalent and likely needs the long-press context-menu path.
- **XCUITest.** Use the same HTTP transport channel as `type` (`/type`). Evaluate a same-shape
  `/select` and `/copy` endpoint, or a general `/keyEvent` endpoint, on the runner side
  (`BajutsuKit`). `XCUIElement` natively exposes both text replacement and keyboard-key events.
- **Playwright.** This is the most direct backend:
  - `page.fill(selector, "")` for `clear`
  - `keyboard.press("Backspace")`, repeated, for `delete`
  - `selectText()` or triple-click for `select`
  - the standard clipboard permission plus `navigator.clipboard` (or a `Ctrl+C` key event) for `copy`

The design preserves the prime directives:

- **Determinism.** Every step is a fixed mutation: a backspace-equivalent count, or a platform
  select-all or copy action. The result is machine-checkable through the existing `value` and
  `clipboard` assertions. The design adds no new unverifiable state, no `sleep`, and no LLM.
- **App-agnostic.** No per-app code is needed. `into` addresses the target the same way every other
  selector-based step does.
- **Codegen.** These steps map to native XCUITest, Espresso, and Playwright actions with direct
  equivalents. Codegen therefore emits real calls, not a `// TODO` stub (unlike BE-0052's
  `simctl`-only primitives).

## Alternatives considered

- **Model `select` as queryable state (a selection range on `Element`).** Rejected. No backend
  exposes selection state through its element and query surface, so any assertion against it would
  be unverifiable. That is a determinism-first violation, not merely an implementation gap.
- **Fold `select` and `copy` into a single `copySelection` step.** Considered, because "select all,
  then copy" is the only flow this item ships. Kept as two steps instead, for two reasons.
  - `select` is meaningful on its own once other selection-consuming actions exist. For example,
    typing over a selection replaces it.
  - Keeping `copy` separate mirrors the existing one-action-per-step convention (like `type`'s
    `into` plus effect shape). It avoids special-casing this one combination.
- **Extend `type` with a `mode: replace` or `clear: true` modifier, instead of a new `clear` step.**
  Rejected. `type` requires `text`, so "clear without retyping" would need an empty string standing
  in for intent. A scenario also reads less clearly when one step's modifiers mix "focus, optionally
  clear, optionally type". Two small, single-purpose steps chained in sequence read better.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `clear` step (all backends) + `value` assertion coverage
- [x] `delete` step (all backends) + `value` assertion coverage
- [x] `select` step, `mode: all` (all backends)
- [x] `copy` step + clipboard read-back coverage (all backends)

**Log**

- [#1114](https://github.com/bajutsu-e2e/bajutsu/pull/1114) landed all four steps in one PR. The PR
  contains:
  - The scenario models (`select`, `clear`, `delete`, `copy`).
  - The `delete_text`, `select_all`, and `copy_selection` `Driver` primitives across every backend
    (fake, adb, playwright, xcuitest, idb, webview).
  - The `copy`-requires-`select` selection-state seam, centralized in `_do_action`.
  - Codegen for all three emitters.
  - Bilingual docs.

  idb `select` and `copy`, and the web-context driver, raise `UnsupportedAction`. Codegen routes
  these to XCUITest, mirroring the multi-touch precedent. The deterministic gate verified the PR.
  On-device actuation is build-time triage per *Detailed design*.

## References

- [DESIGN.md §5](../../DESIGN.md) — the `Driver` abstraction, `Element`/`Selector` shape, and the
  selector-resolution determinism contract these steps must stay inside.
- [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)
  — clipboard seed/read-back this item's `copy` step verifies against, and the precedent for
  per-primitive implementation-time triage reflected in *Detailed design*.
- `bajutsu/orchestrator/actions/handlers/gestures.py` — the existing `type` / `select_option`
  handlers this item's steps follow the shape of.
