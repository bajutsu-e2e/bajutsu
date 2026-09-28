**English** · [日本語](ja/selectors.md)

# Selectors and deterministic resolution (the determinism core)

> This module defines how you specify which element to act on or verify, and how it narrows that
> specification to exactly one match. Every execution path (orchestrator / drivers /
> assertions) depends on this module. Bajutsu's determinism logic is implemented here.
>
> Implementation: `bajutsu/common/drivers/base/_functions.py`.

Related: [the determinism principles](concepts.md#3-determinism-first-four-concrete-mechanisms) · [the DSL in scenarios](scenarios.md#assertion-dsl) · [drivers](drivers.md)

---

## The normalized element (`Element`)

The driver normalizes the backend's output into a common `Element` (TypedDict). Resolution and
assertions only ever look at this normalized form (the driver absorbs backend differences).

```python
class Element(TypedDict):
    identifier: str | None        # stable id (iOS accessibilityIdentifier · web data-testid)
    label: str | None             # accessibilityLabel
    traits: list[str]             # normalized traits (below)
    value: str | None             # accessibility value
    frame: tuple[float, float, float, float]  # x, y, w, h (points)
```

### Normalized traits (`Trait`)

The common tokens state assertions, selectors, and other checks read. Drivers normalize at least these:

| Token | Meaning | Used by |
|---|---|---|
| `button` / `link` | kind | the `traits` selector · doctor's actionable check |
| `notEnabled` | disabled state | `enabled` / `disabled` |
| `selected` | selected / toggled on | `selected` |
| `other` | generic/unclassified element (iOS's catch-all `XCUIElementTypeOther`, for example) | `resolve_unique`'s ambiguity judgment (below) |

> Each backend normalizes its own attributes to these tokens. adb maps UI Automator's
> `enabled="false"` to `notEnabled`. It maps `selected="true"` / `checked="true"` to `selected` the
> same way. XCUITest's resident runner normalizes `isEnabled == false` to `notEnabled` the same way.
> See [drivers](drivers.md) for each backend's exact mapping.

## The selector (`Selector`)

Addresses an element. **All provided fields are AND-ed.**

| Field | Meaning | Stability |
|---|---|---|
| `id` | exact `accessibilityIdentifier`; a **list** is an OR of candidates (matches any), for one scenario carrying several platforms' id forms | ★ first choice |
| `idMatches` | glob over id (assumes multiple matches, e.g. `"list.row.*"`); a **list** matches any glob | for set operations |
| `label` | exact `accessibilityLabel` | auxiliary / disambiguation only |
| `labelMatches` | substring / regex over label (`re.search`) | auxiliary |
| `traits` | narrow by trait (subset test, e.g. `["button"]`) | auxiliary |
| `value` | exact accessibility value | auxiliary |
| `within` | scope to a container (geometric: the candidate's frame must sit inside one the `within` selector resolves to; nestable) | disambiguation |
| `index` | nth of multiple matches (negative allowed) | last resort · flaky |

> `id` / `idMatches` match via `fnmatch.fnmatchcase` (case-sensitive glob), `labelMatches` via
> `re.search` (regex / substring), `traits` is "the given set ⊆ the element's trait set."

> `id` / `idMatches` also accept a **list of candidates** — an OR: the element matches when its id
> equals (or glob-matches) *any* candidate (BE-0221). This lets one shared scenario carry a
> platform's differing id spelling, e.g. `id: [stable.refresh, stable_refresh]` for Android Views'
> `android:id` (which can't hold `.`/`-`). Only one form is on screen per app, so resolution stays
> deterministic — 2+ matching elements still fail fast. See
> [scenarios](scenarios.md#cross-platform-ids-a-candidate-list-be-0221).

### Authoring vs. runtime representation

- The scenario-side [selector](glossary.md#scenario-authoring) is `scenario/models/selector.py`'s `Selector` (pydantic, with aliases like `idMatches`).
- What reaches resolution is `common/drivers/base/selector.py`'s `Selector` (TypedDict).
- The conversion is `Selector.as_selector()` (drops `None`, turns it into a TypedDict).

## Resolution semantics

Apply the selector to the elements from `query()` to narrow candidates. There are three public
functions.

### `matches(el, sel) -> bool`

Whether one element satisfies the per-element conditions (AND). `within` is a cross-element
(spatial) constraint resolved by `find_all`, not here.

### `find_all(elements, sel) -> list[Element]`

**All** matching elements. Used for `idMatches` triggers, `count` assertions, and `exists`
(multiple matches allowed).

### `resolve_unique(elements, sel) -> Element`

**Resolves to exactly one element for a single action.** The most important function for Bajutsu's
determinism.

| Candidate count | Behavior |
|---|---|
| 0 | `ElementNotFound` (an immediate action fails; via a wait (`wait_until`), it times out) |
| 1 | resolved |
| 2+ | raises `AmbiguousSelector` — **structurally rules out** "tap whatever matched first" |

Before counting candidates, `resolve_unique` collapses any that report identical content
(identifier, label, traits, value, and frame all equal) to one. This targets a known XCUITest
quirk — a standard `UIAlertController` button sometimes registers twice in the accessibility
tree, indistinguishably and for the alert's whole lifetime — where the two "candidates" carry no
information to disambiguate on, so `index` cannot pick a "real" one (which twin a run actually
taps swaps between runs). `index` stays reserved for candidates that genuinely differ in some
field; on truly indistinguishable duplicates it is unnecessary and unused.

Next, before judging a 2+ match ambiguous, resolution drops candidates carrying the `other` trait.
A generic wrapper commonly repeats a real element's label. iOS's catch-all `XCUIElementTypeOther`
is one example. A scenario shouldn't need `within` or `index` to route around that duplicate. Two
cases keep the tie as-is. One: every candidate is `other`, so there's nothing to fall back to. Two:
the selector explicitly requests `other` via `traits: ["other"]`. The filtering stays local to
`resolve_unique`. `find_all` (and so `count` / `exists`) still sees every match, `other` included.

As an exception, only when `index` is given does it pick the nth of multiple candidates
(out-of-range = `ElementNotFound`). This filtering runs before the `index` branch. `index`
counts the same filtered set, not the raw `find_all` result. That keeps it aligned with the
ambiguity count above. Otherwise, a dropped `other` would shift every later position by one.
`index` breaks on order changes regardless, so it stays a last resort. For sets, use `idMatches` +
`count` ([scenarios](scenarios.md#assertion-dsl)).

> **Trade-off.** On iOS, `other` also covers a real control whose `XCUIElementType` this driver has
> not named (`checkBox` / `radioButton` / `popUpButton` / `stepper` / `datePicker` and more). Those
> fall through `typeName`'s `default:` arm the same as the generic wrapper
> (`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`). A tie between such a control and a
> classified sibling sharing its label silently keeps the sibling rather than raising
> `AmbiguousSelector`. Only a same-selector tie is affected; a lone unclassified control (no
> classified sibling sharing the selector) resolves normally. A `UIDatePicker` is one case where
> this costs nothing in practice: the wheels underneath its `other` container are each classified
> `pickerWheel`, so [`setPickerValue`](scenarios.md#setpickervalue) addresses them directly — closing
> the classification gap on the parent is not needed to set a date picker's value.

```python
# common/drivers/base/_functions.py (excerpt)
def resolve_unique(elements, sel):
    candidates = _collapse_identical_duplicates(find_all(elements, sel))
    if len(candidates) > 1 and "other" not in sel.get("traits", []):
        without_other = [c for c in candidates if "other" not in c["traits"]]
        if without_other:
            candidates = without_other  # drop `other` ties unless they're all there is
    if "index" in sel:
        ...                          # nth of the filtered set (out-of-range raises ElementNotFound)
    if not candidates:
        raise ElementNotFound(...)
    if len(candidates) > 1:
        raise AmbiguousSelector(...)  # needs within or index to disambiguate
    return candidates[0]
```

Exception hierarchy: `SelectorError` (base) ← `ElementNotFound` / `AmbiguousSelector`. The
orchestrator and assertions catch these and translate them into "step failure" / "assertion
failure" (they do not propagate the exception upward).

### `ElementNotTappable`: a resolved but unreachable target

`resolve_unique` only judges *how many* elements match; it says nothing about whether the one
match is actually reachable on screen. An element can resolve uniquely, carry a valid frame, and
still sit under a sticky header, a toast, or a dimmed modal backdrop — so a tap would land on the
obstruction instead. `tap` / `double_tap` / `long_press` (and the focus-tap inside `type` /
`clear` / `delete` / `select`) now check this before acting, the idiomatic way per backend (iOS's
native `isHittable`; the web's `document.elementFromPoint`; adb's document-order geometric proxy,
`Driver.is_tappable`). When the check fails, the orchestrator takes a small, bounded scroll — up
to three `down` steps, then, only if `down` never clears it, up to six `up` steps (widened, since
`up` must first retrace the ground `down` already covered before it can make any net progress of
its own) — and retries the action once; if the target is still unreachable, the action raises
`ElementNotTappable` instead of the misleading `ElementNotFound` a caller might otherwise mistake
for "not in the tree at all".

`ElementNotTappable` is a sibling of `SelectorError`, not a subclass — the selector *did* resolve,
so lumping it in with resolution failure would blur that "who matched" and "is it reachable" are
different questions. The orchestrator's step-execution catch handles it the same way it handles
`SelectorError`: a clean step failure, never a crash.

On the XCUITest backend a refused **tap** takes one more step before it fails, because iOS can
report a container inflated over the control it wraps — a SwiftUI `Stepper` whose accessibility
element spans its whole form row, refused while the buttons inside it are perfectly reachable. The
driver probes the target's *named* descendants (those emitted after it, inside its frame, carrying
an identifier). Where exactly one is reachable there is no choice to make and the tap goes there,
recorded with `substitution: soleHittableDescendant` so the report and the `trace` timeline both
say the element actuated is not the one the selector named. Where none or several are, there *is* a
choice an author cannot predict, so the tap fails and the message names the candidates instead of
picking one — a real `Stepper` has two reachable children, and `tap: { id: log.count }` has no
single meaning between them. Only `tap` does this: a long-press redirected to a child is a
different intent, not the same intent reaching its target.

The bounded scroll is a safety net for an obstruction an author could not have anticipated (a
transient overlay, a sticky header settling into place) — it is not a substitute for the explicit
[`scroll` action](scenarios.md#scroll). An author who already knows a target starts off-screen
still writes `scroll` themselves; this check only ever fires on a target that already resolved.

### Centralized regardless of backend

adb (Android), playwright (web), and the fake driver have no semantic tap, so each **always
verifies the candidate count via `query()` before** acting, then taps the resolved element's
frame center. XCUITest resolves through that same check too, then taps directly by identifier
instead of a coordinate. Every action resolves through the shared `resolve_unique`, so the
"ambiguous = fail" behavior is identical across every backend. Each driver's `tap` implementation is in
[drivers](drivers.md).

Each backend derives `identifier` from its own accessibility id. XCUITest uses
`accessibilityIdentifier`. adb uses `resource-id` (package prefix stripped). The web backend uses
`data-testid`. Each normalizes into `Element.identifier`. The `id` selector then resolves directly
against that normalized form.

## Porting contract for a device-side resolver

[BE-0408](../roadmaps/BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md)
ports `find_all` and `resolve_unique` to Swift and Kotlin. A device-side executor then resolves a
selector without a host round trip. Both ports must resolve every selector to the same element
this module's Python code would pick. Both must fail the same way on an ambiguous match. This
section states rules a literal reading of the code above could miss. It also names two
divergences a port must keep, not close.

`tests/fixtures/be0408/` holds this contract in executable form.
`selector_resolution.json` replays `find_all` and `resolve_unique` against 42 cases.
`android_derived_label.json` replays Android's label-derivation rule against 9 more.
`tests/test_selector_fixtures.py` checks every case against this module's own functions. A change
here that breaks a fixture fails the fast gate first, before it ever reaches a port.

### Two distinct matching engines

`idMatches` and `labelMatches` do not share one matching engine. `idMatches` uses
`fnmatch.fnmatchcase`: a glob, fully anchored, case-sensitive. `labelMatches` uses `re.search`: a
regular expression, unanchored, case-sensitive. A glob library's default anchoring often differs
from a regex engine's default anchoring. A port must reproduce Python's own anchoring for each
field, not the port language's own default. That means fully anchored for `idMatches` and
unanchored for `labelMatches`.

### A present field is not a non-null field

The scenario-side `Selector` model drops every `None` field before it reaches resolution. This
happens in `as_selector()`, defined in `bajutsu/common/scenario/models/selector.py`. A field's
absence from the wire form always means "not specified". It never means "specified as empty". A
port must never treat an explicit null for a selector field as "present but empty".

### `find_all` never raises, and returns elements in document order

`find_all` has no concept of ambiguity. It returns every matching element, in the input list's own
order. `within` scopes that result to elements whose frame sits inside a container's frame. This
containment is geometric: edge-inclusive, over a flat element list with no parent pointer. A
`within` selector may nest to any depth. Containment includes the boundary case where a candidate's
own frame equals its container's frame. An element that scopes to itself meets this test. Treat
that outcome as expected, not as a bug to guard against.

A `within` selector that itself matches nothing produces an empty scope list. The whole result is
then empty too. This is never an unscoped pass-through, and never an error. `contains` never runs
against zero scopes. `any()` over an empty sequence is false for every candidate. A port might read
a missing container as "nothing to scope by, fall back to unscoped" instead. That port then
resolves a selector the host fails on. That is the one-side-resolves, other-side-fails divergence
this whole contract exists to rule out.

### The duplicate-collapse key

`resolve_unique` first collapses identical-content candidates into one representative. One known
XCUITest quirk motivates this collapse. A standard alert button sometimes registers twice,
indistinguishably. The collapse key is:

- `identifier`
- `label`
- the trait *set* (order does not matter)
- `value`
- an exact frame match

`nativeZ` stays out of the key on purpose: this field exists for diagnostics, not identity. Two
candidates that agree on every other field still collapse. Their `nativeZ` values may differ by any
amount.

The collapse keeps the *first* candidate carrying a given key, in `find_all`'s own document order.
It drops the rest. `index` then counts positions in that same order. A plain hash map has no
defined iteration order — Swift's `Dictionary`, for one. A port reaching for one gets this wrong.
It would pick a different survivor from run to run, on any screen where the collapse fires. That
makes an `index`-addressed tap nondeterministic. This rule exists for that same screen. A port
needs an insertion-ordered structure for the collapse. Another structure works too, as long as it
preserves first-seen order explicitly.

### The `other`-trait drop, and where `index` counts from

Before judging ambiguity, `resolve_unique` drops every candidate carrying the `other` trait. Two
exceptions leave the tie untouched. The selector itself may target `other`, or every remaining
candidate may already be `other`. `index` then counts positions in that filtered set, never the raw
`find_all` result. A dropped `other` candidate must never shift a later position by one. An `index`
against zero candidates is out of range, not "no match". A port's own error taxonomy needs that
same two-way split. The Python message text is implementation-specific. A port needs the
classification alone, never the exact words.

### Android's derived-label rule

A clickable Android node with no `text` or `content-desc` derives a label from its descendants.
The join runs depth-first, in pre-order. This is `_derived_label`, in
`bajutsu/common/drivers/adb/_functions.py`. `_derived_label` skips a nested clickable descendant
whole, subtree included. That descendant is its own control. It carries its own label. Its text
never folds into its parent's label. A non-clickable container never derives a label at all.
Derivation fires for a clickable node alone.

The join folds in a descendant's `text`, never its `content-desc`. `content-desc` is this
driver's own *value* channel. The showcase app mirrors assertion state into it. Folding it into a
derived label risks that mirrored value leaking into the label instead. A descendant that carries
`content-desc` and no `text` contributes nothing to the join.
`android_derived_label.json`'s `descendant_content_desc_is_never_folded_into_the_join` case pins
this: a port that also folds `content-desc` in fails that case before it ever reaches a device.

This rule runs one layer before selector resolution. It builds the `Element`
a selector later matches against. A device-side Android executor's own accessibility read must
reach the same label. The host's `/source` read would reach that same label too. Otherwise a
label-based selector could resolve on one side of a run and fail on the other.

### Two divergences a port must keep, not close

Two existing Swift behaviors differ from this module on purpose. A new port must not "correct"
either one toward Python's own behavior.

`PositionPath.framesEqual` allows one point of slack on each of a frame's four values. This lives
in `BajutsuKit/Sources/BajutsuRunner/PositionPath.swift`. This module's own duplicate-collapse key
compares frames as an exact match instead. The two frames come from different moments. A
duplicate-collapse frame comes from one atomic snapshot. An exact match is the right test for that
case. `PositionPath`'s frame comes from its own live re-fetch, per candidate. A point of slack there
absorbs the noise a second read can introduce. Sync the fields the two keys compare. Never sync the
tolerance between them.

`PositionPath.attributesMatch` compares `traits` as an ordered array. This module's own `matches`
treats `traits` as a set instead. The two answer different questions. `PositionPath` asks whether a
specific, already-recorded element is still the same element. A device that reports its traits in a
fixed order can compare that order too. A fresh `resolve_unique` call has no recorded order to
compare against. It treats the field as the set it semantically is.

## Assertion evaluation

Implementation: `bajutsu/common/assertions/evaluate/_functions.py`. BE-0250 split it from a single
module, then BE-0411 packaged it per class.
`evaluate(elements, assertions) -> list[AssertionResult]`
evaluates each assertion, and `passed(results)` ANDs them. **Evaluation is total**: a resolution
failure (not-found / ambiguous) is returned as a failed `AssertionResult` rather than an exception
(it lands straight in the report).

```python
@dataclass(frozen=True)
class AssertionResult:
    ok: bool
    kind: str        # "exists" / "value" / ...
    detail: str      # what was checked (for the report)
    reason: str      # failure reason (empty when ok)
```

Per-kind mechanics:

| Kind | Resolution | Decision |
|---|---|---|
| `exists` | `find_all` ≥ 1 | `found != negate` (negate checks absence) |
| `value` | `resolve_unique` (ambiguous / not-found fails) | compares `value` via `equals`/`contains`/`matches` |
| `label` | same | compares `label` likewise |
| `count` | the `find_all` count | `equals`/`atLeast`/`atMost` |
| `enabled` | `resolve_unique` | the `notEnabled` trait is **absent** |
| `disabled` | `resolve_unique` | the `notEnabled` trait is **present** |
| `selected` | `resolve_unique` | the `selected` trait is present |
| `request` | matches over the observed network exchanges (not the element tree) | `equals`/`atLeast`/… via `count`, else ≥ 1 ([network](network.md)) |

> Only `exists` uses `find_all` (allows multiple); the other single-element assertions use
> `resolve_unique` (ambiguous fails). So "tried to check the value when there were two matches" also
> fails deterministically. The table above covers eight kinds. Seven resolve against the element
> tree. The eighth, `request`, checks the captured HTTP(S) exchanges instead. Six more kinds never
> resolve a selector this way. They are `event` / `requestSequence` / `responseSchema` / `visual` /
> `clipboard` / `golden`. See [scenarios](scenarios.md#assertion-dsl) for the full list.
