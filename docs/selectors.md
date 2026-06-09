**English** · [日本語](ja/selectors.md)

# Selectors and deterministic resolution (the determinism core)

> How you specify "which element to act on or verify," and how that is narrowed to exactly one.
> Bajutsu's determinism is concentrated **here**. Every execution path (orchestrator / drivers /
> assertions) depends on this module.
>
> Implementation: `bajutsu/drivers/base.py`.

Related: [the determinism principles](concepts.md#3-determinism-first-four-concrete-mechanisms) · [the DSL in scenarios](scenarios.md#assertion-dsl) · [drivers](drivers.md)

---

## The normalized element (`Element`)

The driver normalizes the backend's output into a common `Element` (TypedDict). Resolution and
assertions only ever look at this normalized form (backend differences are absorbed in the driver).

```python
class Element(TypedDict):
    identifier: str | None        # accessibilityIdentifier
    label: str | None             # accessibilityLabel
    traits: list[str]             # normalized traits (below)
    value: str | None             # accessibility value
    frame: tuple[float, float, float, float]  # x, y, w, h (points)
```

### Normalized traits (`Trait`)

The common tokens that state assertions look at. Drivers normalize at least these:

| Token | Meaning | Used by |
|---|---|---|
| `button` / `link` | kind | the `traits` selector · doctor's actionable check |
| `notEnabled` | disabled state | `enabled` / `disabled` |
| `selected` | selected / toggled on | `selected` |

(idb normalizes `enabled: false` → `notEnabled`, `selected: true` → `selected`. Type strings drop
the `AX` prefix and lowercase the first letter: `AXButton` → `button`. See `drivers/idb.py`.)

## The selector (`Selector`)

Addresses an element. **All provided fields are AND-ed.**

| Field | Meaning | Stability |
|---|---|---|
| `id` | exact `accessibilityIdentifier` | ★ first choice |
| `idMatches` | glob over id (assumes multiple matches, e.g. `"list.row.*"`) | for set operations |
| `label` | exact `accessibilityLabel` | auxiliary / disambiguation only |
| `labelMatches` | substring / regex over label (`re.search`) | auxiliary |
| `traits` | narrow by trait (subset test, e.g. `["button"]`) | auxiliary |
| `value` | exact accessibility value | auxiliary |
| `within` | scope to a container (geometric: the candidate's frame must sit inside one the `within` selector resolves to; nestable) | disambiguation |
| `index` | nth of multiple matches (negative allowed) | last resort · flaky |

> `id` / `idMatches` match via `fnmatch.fnmatchcase` (case-sensitive glob), `labelMatches` via
> `re.search` (regex / substring), `traits` is "the given set ⊆ the element's trait set."

### Authoring vs. runtime representation

- The scenario-side selector is `scenario.py`'s `Selector` (pydantic, with aliases like `idMatches`).
- What reaches resolution is `drivers/base.py`'s `Selector` (TypedDict).
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
| 0 | `ElementNotFound` (an immediate action fails; via `wait_for`, it times out) |
| 1 | resolved |
| 2+ | raises `AmbiguousSelector` — **structurally rules out** "tap whatever matched first" |

As an exception, only when `index` is given does it pick the nth of multiple candidates
(out-of-range = `ElementNotFound`). `index` breaks on order changes, so it is a last resort. For
sets, use `idMatches` + `count` ([scenarios](scenarios.md#assertion-dsl)).

```python
# drivers/base.py (excerpt)
def resolve_unique(elements, sel):
    candidates = find_all(elements, sel)
    if "index" in sel:
        ...                          # nth (out-of-range raises ElementNotFound)
    if not candidates:
        raise ElementNotFound(...)
    if len(candidates) > 1:
        raise AmbiguousSelector(...)  # needs within or index to disambiguate
    return candidates[0]
```

Exception hierarchy: `SelectorError` (base) ← `ElementNotFound` / `AmbiguousSelector`. The
orchestrator and assertions catch these and translate them into "step failure" / "assertion
failure" (they do not propagate the exception upward).

### Centralized regardless of backend

idb exposes no usable semantic tap, so the abstraction **always verifies the candidate count via
`query()` before** acting, then taps the resolved element's frame center. This makes the "ambiguous =
fail" behavior identical across idb / fake (each driver's `tap` implementation is in
[drivers](drivers.md)).

The `id` comes straight from idb's element tree (`AXUniqueId`), normalized into `Element.identifier`,
so the `id` selector resolves directly against the normalized form.

## Assertion evaluation

Implementation: `bajutsu/assertions.py`. `evaluate(elements, assertions) -> list[AssertionResult]`
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

> Only `exists` uses `find_all` (allows multiple); the other single-element assertions use
> `resolve_unique` (ambiguous fails). So "tried to check the value when there were 2 matches" also
> fails deterministically.
