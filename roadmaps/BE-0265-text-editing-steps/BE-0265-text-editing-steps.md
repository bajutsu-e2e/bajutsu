**English** · [日本語](BE-0265-text-editing-steps-ja.md)

# BE-0265 — Text-editing steps: select, clear, delete, copy

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0265](BE-0265-text-editing-steps.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0265") |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

The only text-entry step today is `type` (`bajutsu/orchestrator/actions/handlers/gestures.py`),
which appends characters via `driver.type_text()` on every backend. There is no way to clear a
field, delete part of its content, select text, or copy a selection to the clipboard. This item
adds four steps — `select`, `clear`, `delete`, `copy` — that close that gap while staying inside
the existing determinism model.

## Motivation

Real forms are edited, not just filled once: a user clears a field and retypes, backspaces a typo,
selects a value to copy it elsewhere, or replaces part of an existing value. None of that is
expressible in a scenario today; the only workaround is a strained sequence of `tap` /
`longPress` guesses with no defined outcome, which is exactly the non-determinism the driver
abstraction exists to avoid.

The natural design hazard is treating "selection" as a piece of state — *which characters are
currently selected* — that a step could set and a later assertion could check. No backend exposes
that as queryable state: `Element` (`DESIGN.md` §5) carries `value` but no selection range, and
none of idb / adb / XCUITest expose a selection query. Modeling `select` that way would need new,
backend-specific state no `query()` can verify, in tension with the determinism-first directive
(prime directive 2) and the "ambiguity fails rather than guesses" contract the selector semantics
already enforce.

The fix is to keep `select` (and `copy`) as **actions**, not state: given the same field content
and the same fixed step parameters, the action is deterministic, and *its own outcome is never the
thing asserted*. Verification lands on what happens next — a `type` that replaces the selection
(existing `value` assertion), or a `copy` that is checked through the clipboard read-back BE-0052
already shipped (`expect: - clipboard: { equals | matches }`). `clear` and `delete` need no new
assertion at all: both are backspace-shaped mutations of `value`, and `value` is already an
assertable `Element` field and an existing `Assertion` case
(`bajutsu/scenario/models/assertions.py`).

## Detailed design

Four new steps, following the existing `type` shape (`into` selects the target field first, then
the step's own effect runs):

```yaml
- select: { into: form.note, mode: all }   # select the field's full current content
- copy: {}                                  # copy the active selection to the clipboard
- clear: { into: form.note }                # clear the field's full content
- delete: { into: form.note, count: 3 }     # delete the last 3 characters
```

- **`select`** — `mode: all` only for this item (a platform "select all" action: e.g. a hardware
  ⌘A key event on the iOS Simulator, Android's long-press ▸ "Select All", `Ctrl+A` / triple-click
  on web). Word- or range-level selection (`mode: word`, offset-based ranges) is deliberately out
  of scope: it needs drag-handle or cursor-position primitives none of the backends expose today,
  and the boundary semantics (what counts as a "word") are locale- and OS-dependent — a
  significantly bigger, less deterministic surface that can be scoped later if a concrete need
  shows up.
- **`copy`** — triggers the platform's "Copy" action on whatever is currently selected (no
  target selector of its own; it acts on the active selection `select` produced). Its result is
  verified purely through the existing clipboard read-back, not through any new state this step
  introduces. **When nothing is selected** (no prior `select`, or a step cleared the selection),
  `copy` fails the step deterministically rather than silently copying nothing or leaving the
  clipboard's prior contents in place: a "copy" with no selection is a scenario-authoring mistake,
  and per prime directive 2 it must surface as a failure, not a silent no-op that a later clipboard
  assertion might misread as success. The check is on Bajutsu's side (was a selection established
  since the last selection-clearing action?), not delegated to whatever each backend's native copy
  happens to do with an empty selection — so the failure is identical across backends.
- **`clear`** — clears the named field's entire content. `Element.value` (already returned by
  `query()`) gives the current string; the actuation removes exactly that many characters
  (backspace-equivalent) rather than guessing a fixed count, so it is agnostic to whatever content
  the field already held.
- **`delete`** — removes `count` characters from the end of the field's current value, the same
  backspace-equivalent mechanism as `clear` bounded to a smaller count. Useful for a scripted typo
  correction or partial edit without clearing the whole field.

**Per-backend actuation** — the concrete mechanism is a per-backend implementation detail
finalized at build time (mirroring BE-0052's own experience, where two of four primitives needed
implementation-time triage to find a mechanism that actually holds up). The directions to
evaluate:

- **idb** — `driver.type_text()` already reaches the focused field over `idb_companion`'s text
  gRPC call (`bajutsu/drivers/idb.py`); whether control characters (e.g. `\b`) are honored the same
  way a hardware backspace key would be needs implementation-time verification. If not, a
  companion HID key-event call (as opposed to the text-injection call) is the fallback.
- **adb** — `input keyevent KEYCODE_DEL` (67) repeated `count` times, run the same way the existing
  `KEYCODE_BACK` call is (`bajutsu/adb.py`); `select`'s "select all" has no single keyevent
  equivalent and likely needs the long-press context-menu path.
- **XCUITest** — same HTTP transport channel used for `type` (`/type`); evaluate a same-shape
  `/select` and `/copy` (or a general `/keyEvent`) endpoint on the runner side (`BajutsuKit`),
  since `XCUIElement` exposes both text replacement and keyboard-key events natively.
- **Playwright** — the most direct backend: `page.fill(selector, "")` for `clear`,
  `keyboard.press("Backspace")` repeated for `delete`, `selectText()` / triple-click for `select`,
  and the standard clipboard permission + `navigator.clipboard` (or `Ctrl+C` key event) for `copy`.

Prime directives preserved:

- **Determinism.** Every step is a fixed mutation (backspace-equivalent count, or a platform select
  -all/copy action) with a machine-checkable result reached through existing `value` / `clipboard`
  assertions — no new unverifiable state, no `sleep`, no LLM.
- **App-agnostic.** No per-app code; `into` addresses the target the same way every other selector
  -based step does.
- **Codegen.** These map to native XCUITest / Espresso / Playwright actions with direct
  equivalents, so codegen emits real calls rather than a `// TODO` stub (unlike BE-0052's
  `simctl`-only primitives).

## Alternatives considered

- **Model `select` as queryable state (a selection range on `Element`).** Rejected: no backend
  exposes selection state through its element/query surface, so any assertion against it would be
  unverifiable — a determinism-first violation, not just an implementation gap.
- **Fold `select` + `copy` into a single `copySelection` step.** Considered, since "select all then
  copy" is the only flow this item ships. Kept as two steps instead: `select` is meaningful on its
  own once other selection-consuming actions exist (e.g. typing over a selection replaces it), and
  keeping `copy` separate mirrors the existing one-action-per-step convention (like `type`'s
  `into` + effect shape) rather than special-casing this one combination.
- **Extend `type` with a `mode: replace` / `clear: true` modifier instead of a new `clear` step.**
  Rejected: `type` requires `text`, so "clear without retyping" would need an empty string
  standing in for intent, and mixing "focus + optionally clear + optionally type" into one step's
  modifiers reads less clearly in a scenario than two small, single-purpose steps chained in
  sequence.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `clear` step (all backends) + `value` assertion coverage
- [ ] `delete` step (all backends) + `value` assertion coverage
- [ ] `select` step, `mode: all` (all backends)
- [ ] `copy` step + clipboard read-back coverage (all backends)

## References

- [DESIGN.md §5](../../DESIGN.md) — the `Driver` abstraction, `Element`/`Selector` shape, and the
  selector-resolution determinism contract these steps must stay inside.
- [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)
  — clipboard seed/read-back this item's `copy` step verifies against, and the precedent for
  per-primitive implementation-time triage reflected in *Detailed design*.
- `bajutsu/orchestrator/actions/handlers/gestures.py` — the existing `type` / `select_option`
  handlers this item's steps follow the shape of.
