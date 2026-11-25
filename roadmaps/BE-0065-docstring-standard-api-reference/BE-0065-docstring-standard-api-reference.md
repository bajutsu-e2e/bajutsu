**English** · [日本語](BE-0065-docstring-standard-api-reference-ja.md)

# BE-0065 — Docstring standard & generated API reference

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0065](BE-0065-docstring-standard-api-reference.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0065") |
| Implementing PR | [#232](https://github.com/bajutsu-e2e/bajutsu/pull/232) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This item establishes a single, written **docstring standard** for the Python core and **generates an API reference** from it. The people and AI agents who read and change this repository can then grasp its public surface without opening every file.

The public API is documented in **Google-style** docstrings and published as a generated reference. Internal helpers keep the concise prose docstrings that the codebase already favors. Prose never repeats types, because the annotations of each signature carry them and the generator reads them from there.

`ruff` enforces the standard on the public surface, and a static documentation generator renders the reference (recommended: MkDocs with `mkdocstrings`). Neither touches the deterministic `run` / CI gate.

## Motivation

The Python core is already well commented. Module docstrings explain *why*, and type annotations are complete (`mypy` is strict and the `ANN` rules of `ruff` are on). Behavior cross-references BE items. Two things are missing.

First, **the convention is implicit.** `ruff` does not select the `D` (pydocstyle) rules. The codebase uses prose docstrings with no structured sections, and no `Args:`, `Returns:`, or `:param:` appears anywhere today. That style is deliberate and good, but nothing writes it down. It drifts as the public surface grows across the `Driver` protocol, the CLI, the MCP tools, and the scenario schema.

Second, **no generated reference exists.** The hand-written docs under `docs/` explain concepts and workflows, but nothing renders the public API itself. A reader who wants the shape of `Driver`, the selector types, or the MCP tools has to open the source.

Both gaps matter more now, because **agentic coding is a primary way that people read and change this code.** A consistent docstring standard and a navigable API surface help contributors who lean on AI assistants, and they help the assistants themselves. Each public parameter gets one place for its meaning. One rendered map of the surface replaces a search through 100+ files.

Agents with repository access still read source. The larger payoff of the standard is therefore the *consistency and the rendered surface*, not the format alone (see *Alternatives considered*).

The change covers documentation and tooling only. It adds no LLM anywhere and never runs inside `run`, and the reference build lives outside the gate. Prime directives 1 and 2 ([CLAUDE.md](../../CLAUDE.md)) hold by construction.

## Detailed design

### Scope: the public API surface

Structured (Google-style) docstrings apply to the **public API surface**, which outside callers and agents reach for:

- the `Driver` protocol and the shared types (`Selector`, `Element`, …) in [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py)
- the CLI commands ([`bajutsu/cli/`](../../bajutsu/cli/))
- the MCP tools and resources ([`bajutsu/mcp/`](../../bajutsu/mcp/))
- the scenario schema (the hub artifact)
- the public functions of the runner, `assertions`, and `network`

Internal, module-private helpers (the `_`-prefixed functions) keep a **concise prose docstring**: one purposeful line that says why the helper exists. Forcing `Args:` blocks onto a small helper would add the *what*-narration that the repo avoids. The generated reference excludes private members.

### The standard

- **Language: English**, like every code comment. The prose docs are bilingual, but code is not ([`docs/README.md`](../../docs/README.md)).
- **Google style on the public surface.** Write a one-line summary. Add `Args:`, `Returns:`, and `Raises:` (and `Yields:` or `Examples:`) **only where they add information**.
- **Do not restate types.** Types live in annotations, and the generator reads them from the signature. `Args:` and `Returns:` describe *meaning*, such as units, constraints, and what `None` means, and never the type.
- **Why, not what.** Cover rationale, invariants (especially anything that protects determinism), trade-offs, and edge cases. Tie the rationale of a behavior to its `BE-NNNN` item.
- **Match the surrounding density.** Stay short and purposeful, without narration.
- **Keep the per-field idiom.** For `TypedDict` and constant-holder classes, the inline comment on each field carries the *why* of that field better than a prose block does. Keep it.

### Examples

The next example shows a public function before (today's prose) and after (the standard). The types are *not* repeated. `Args:` and `Returns:` carry meaning, the determinism invariant leads, and the rationale ties to a BE item:

```python
# before (today's prose) — bajutsu/drivers/base.py
def resolve_unique(elements: list[Element], sel: Selector) -> Element:
    """Resolve to exactly one element for a single action.

    - 0 matches -> ElementNotFound
    - 2+ matches -> AmbiguousSelector (rules out "tap whatever matched first")
    - only with `index` do we pick the nth of multiple candidates (last resort)
    """

# after (Google style on the public surface)
def resolve_unique(elements: list[Element], sel: Selector) -> Element:
    """Resolve a selector to exactly one element for a single action.

    A single action requires a unique match, so an ambiguous selector fails
    rather than acting on "whatever matched first" (the determinism core, BE-0001).

    Args:
        elements: One `query()` snapshot of the on-screen elements.
        sel: The selector to resolve. `index` is honored only as a last resort,
            picking the nth of several candidates.

    Returns:
        The one element the selector resolves to.

    Raises:
        ElementNotFound: Nothing matched, or `index` is out of range.
        AmbiguousSelector: Two or more matched and no `index` disambiguates.
    """
```

Internal helpers stay prose, with one line of *why* and no `Args:` block:

```python
def _contains(outer: Frame, inner: Frame) -> bool:
    """Whether `inner`'s frame sits inside `outer`'s (edges inclusive)."""
```

`TypedDict` and constant-holder classes keep the inline comment on each field. That comment carries the *why* of the field better than a prose `Args:`-style block:

```python
class Selector(TypedDict, total=False):
    """How to address an element. Provided fields are combined with AND."""

    id: str      # exact accessibilityIdentifier (first choice)
    index: int   # nth of multiple matches (last resort; flaky)
```

### Generation

The recommended stack is **MkDocs + Material + `mkdocstrings[python]`**:

- The stack is **Markdown-native**, so the API pages live in the same site as the existing bilingual `docs/`.
- `mkdocstrings` reads signatures through **`griffe`, which analyzes statically** and does not import the modules. That sidesteps the lazy and optional imports on which the core is built (`playwright`, `fb-idb`, `fastapi`, `redis`, `boto3`). A `Sphinx autodoc` setup would have to mock them.
- Typed signatures come from the annotations automatically, so docstrings never restate types.

The alternative is `Sphinx + autodoc + napoleon`, with `myst-parser` for Markdown. It parses the same Google-style docstrings, but it is heavier here and must mock the optional imports.

### Packaging, enforcement, hosting

- A new `docs` **optional-dependency group** in `pyproject.toml` holds the generator and its plugins. It is isolated like the other extras.
- A `make docs` target and a `make docs-serve` target stay **out of the core gate**. Like on-device E2E, the reference build is a separate, heavier path and must not slow `make check`.
- **Enforcement:** enable the `D` rules of `ruff` with `convention = "google"`, **scoped to the public modules** via `per-file-ignores`. Tests, demos, and scripts already ignore `ANN` and `T20`. Internal helpers and the rest of the tree are not forced into structured sections.
- A **GitHub Pages** workflow publishes the reference on merge to `main`. The API reference is English, because it renders English docstrings. The hand-written chrome of the site follows the bilingual documentation rule of the repo.

### Where the standard lives

The standard itself is documented in [`docs/ai-development.md`](../../docs/ai-development.md) (and its `docs/ja/` mirror) as a *Code documentation comments (docstrings)* section. A summary goes in the **Conventions** list of [`CLAUDE.md`](../../CLAUDE.md). The existing *Documentation style* rule already splits its text the same way.

### Migration, in phases

1. This proposal.
2. Stand up the site from the **existing prose docstrings**, with no docstring change. Typed signatures already render, which proves the pipeline and gives immediate value.
3. Write the standard into `docs/ai-development.md` (+ `docs/ja/`) and `CLAUDE.md`.
4. Migrate public-API docstrings to Google style **module by module, in small PRs**. Small diffs merge fast and rarely conflict, as the parallel-work model of [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md) expects.
5. Enable the scoped `ruff D` enforcement.
6. Turn on Pages hosting.

## Alternatives considered

- **Keep prose docstrings and generate with a signature-introspecting tool, with no format change.** `pdoc` and `mkdocstrings` already render prose docstrings *and* typed signatures, because the types come from annotations and not from the docstring. A browsable, typed reference is thus achievable today without touching a single docstring. That path costs least and was the recommended one, so this item records it as the baseline. This proposal goes further and adopts Google-style structured sections on the public surface. The reference then gives a predictable, parameter-level description, and the item accepts the migration cost for that consistency.
- **`Sphinx + autodoc + napoleon`.** It parses the same Google docstrings, but it is reStructuredText-native and needs `myst-parser` for Markdown. `autodoc` also imports modules, which forces `autodoc_mock_imports` for every optional or lazy dependency. `mkdocstrings` with `griffe` is Markdown-native and static. Sphinx stays as a fallback.
- **Migrate the whole tree, internal helpers included.** Rejected. About 1,000 docstrings mean high churn and a large conflict surface. `Args:` on tiny private helpers is the *what*-narration that the repo avoids. Scope the work to the public surface.
- **Do nothing.** This is the status quo: an implicit, undocumented prose convention and no rendered API surface. Rejected. The convention drifts, and the growing public API has no map for human or agent readers.
- **A dedicated new roadmap topic.** The item is filed under *Contributor workflow*, the topic of [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md), instead. The item concerns how contributors and agents understand the code. It also follows the precedent of not splitting a topic for a single item.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [CLAUDE.md](../../CLAUDE.md) — Conventions (comments explain *why*; bilingual docs; code
  comments in English) and the Prime directives this respects (no LLM in the gate).
- [BE-0043 — Conflict-resistant file flow](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
  — the *Contributor workflow* precedent: generated docs as artifacts, small conflict-free PRs.
- [`docs/ai-development.md`](../../docs/ai-development.md) — where the standard will live;
  [`docs/README.md`](../../docs/README.md) — "code comments / docstrings are in English".
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py),
  [`bajutsu/cli/`](../../bajutsu/cli/), [`bajutsu/mcp/`](../../bajutsu/mcp/) — the public
  surfaces the standard covers first.
- [`pyproject.toml`](../../pyproject.toml) — `mypy` strict + `ruff` `ANN`: why types live in
  annotations, not docstrings.
- MkDocs Material, `mkdocstrings[python]` (`griffe`), and `Sphinx` + `napoleon` — the candidate
  generators.
