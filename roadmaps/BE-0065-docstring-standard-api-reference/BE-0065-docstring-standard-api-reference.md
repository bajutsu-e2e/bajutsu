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

Establish a single, written **docstring standard** for the Python core and **generate an API
reference** from it, so the people and AI agents who read and change this repository can grasp its
public surface without opening every file. The public API is documented in **Google-style**
docstrings and published as a generated reference; internal helpers keep the concise prose
docstrings the codebase already favors. Types are never repeated in prose — they live in the
annotations the generator reads from each signature. The standard is enforced on the public
surface by `ruff` and rendered by a static documentation generator (recommended: MkDocs +
`mkdocstrings`). Neither touches the deterministic `run` / CI gate.

## Motivation

The Python core is already well-commented: module docstrings explain *why*, type annotations are
complete (`mypy` is strict and `ruff`'s `ANN` rules are on), and behavior is cross-referenced to BE
items. Two things are missing.

First, **the convention is implicit.** `ruff` does not select the `D` (pydocstyle) rules, and the
codebase uses prose docstrings with no structured sections — there is no `Args:` / `Returns:` /
`:param:` anywhere today. That is a deliberate, good style, but it is written down nowhere, so it
drifts as the public surface grows: the `Driver` protocol, the CLI, the MCP tools, the scenario
schema.

Second, **there is no generated reference.** The hand-written docs under `docs/` explain concepts
and workflows, but nothing renders the public API itself. A reader who wants the shape of `Driver`,
the selector types, or the MCP tools has to open the source.

Both gaps matter more now because **agentic coding is a primary way this code is read and
changed.** A consistent docstring standard and a navigable API surface help the contributors who
lean on AI assistants and the assistants themselves: a single place for the meaning of each public
parameter, and one rendered map of the surface instead of 100+ files. (Agents with repository
access still read source; the standard's larger payoff is the *consistency and the rendered
surface*, not the format alone — see *Alternatives considered*.)

This is documentation and tooling only. It adds no LLM anywhere, never runs inside `run`, and the
reference build lives outside the gate, so Prime directives 1 and 2 ([CLAUDE.md](../../CLAUDE.md))
hold by construction.

## Detailed design

### Scope: the public API surface

Structured (Google-style) docstrings apply to the **public API surface** — what outside callers
and agents reach for:

- the `Driver` protocol and the shared types (`Selector`, `Element`, …) in
  [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py);
- the CLI commands ([`bajutsu/cli/`](../../bajutsu/cli/));
- the MCP tools and resources ([`bajutsu/mcp/`](../../bajutsu/mcp/));
- the scenario schema (the hub artifact);
- the public functions of the runner, `assertions`, and `network`.

Internal, module-private helpers (the `_`-prefixed functions) keep a **concise prose docstring** —
one purposeful line saying why they exist. Forcing `Args:` blocks onto a small helper is the
*what*-narration the repo avoids. The generated reference excludes private members.

### The standard

- **Language: English**, like every code comment (the prose docs are bilingual; code is not —
  [`docs/README.md`](../../docs/README.md)).
- **Google style on the public surface.** A one-line summary, then `Args:` / `Returns:` / `Raises:`
  (and `Yields:` / `Examples:`) **only where they add information**.
- **Do not restate types.** Types live in annotations and the generator reads them from the
  signature; `Args:` / `Returns:` describe *meaning* — units, constraints, what `None` means — not
  the type.
- **Why, not what.** Rationale, invariants (especially anything protecting determinism),
  trade-offs, edge cases. Tie a behavior's rationale to its `BE-NNNN` item.
- **Match the surrounding density.** Short and purposeful; no narration.
- **Keep the per-field idiom.** For `TypedDict` and constant-holder classes, the per-field inline
  comment carries each field's *why* better than a prose block; keep it.

### Examples

A public function, before (today's prose) and after (the standard). The types are *not* repeated —
`Args:` / `Returns:` carry meaning, the determinism invariant leads, and the rationale is tied to a
BE item:

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

Internal helpers stay prose — one line of *why*, no `Args:` block:

```python
def _contains(outer: Frame, inner: Frame) -> bool:
    """Whether `inner`'s frame sits inside `outer`'s (edges inclusive)."""
```

`TypedDict` and constant-holder classes keep the per-field inline comment, which carries each
field's *why* better than a prose `Args:`-style block:

```python
class Selector(TypedDict, total=False):
    """How to address an element. Provided fields are combined with AND."""

    id: str      # exact accessibilityIdentifier (first choice)
    index: int   # nth of multiple matches (last resort; flaky)
```

### Generation

Recommended stack: **MkDocs + Material + `mkdocstrings[python]`**.

- It is **Markdown-native**, so the API pages live in the same site as the existing bilingual
  `docs/`.
- `mkdocstrings` reads signatures through **`griffe`, which analyzes statically** — it does not
  import the modules. That sidesteps the lazy / optional imports the core is built on (`playwright`,
  `fb-idb`, `fastapi`, `redis`, `boto3`), which a `Sphinx autodoc` setup would have to mock.
- Typed signatures come from the annotations automatically, so docstrings never restate types.

`Sphinx + autodoc + napoleon` (with `myst-parser` for Markdown) is the alternative: it parses the
same Google-style docstrings, but is heavier here and must mock the optional imports.

### Packaging, enforcement, hosting

- A new `docs` **optional-dependency group** in `pyproject.toml` (the generator + plugins),
  isolated like the other extras.
- A `make docs` / `make docs-serve` target, **kept out of the core gate** — like on-device E2E, the
  reference build is a separate, heavier path and must not slow `make check`.
- **Enforcement:** enable `ruff`'s `D` rules with `convention = "google"`, **scoped to the public
  modules** via `per-file-ignores` (tests / demos / scripts already ignore `ANN` / `T20`). Internal
  helpers and the rest of the tree are not forced into structured sections.
- A **GitHub Pages** workflow publishes the reference on merge to `main`. The API reference is
  English (it renders English docstrings); the site's hand-written chrome follows the repo's
  bilingual documentation rule.

### Where the standard lives

The standard itself is documented in [`docs/ai-development.md`](../../docs/ai-development.md) (and
its `docs/ja/` mirror) as a *Code documentation comments (docstrings)* section, and summarized in the
**Conventions** list of [`CLAUDE.md`](../../CLAUDE.md) — the same split the existing *Documentation
style* rule already uses.

### Migration, in phases

1. This proposal.
2. Stand up the site from the **existing prose docstrings**, with no docstring change — typed
   signatures already render, proving the pipeline and giving immediate value.
3. Write the standard into `docs/ai-development.md` (+ `docs/ja/`) and `CLAUDE.md`.
4. Migrate public-API docstrings to Google style **module by module, in small PRs** (small diffs
   merge fast and rarely conflict — the parallel-work model,
   [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)).
5. Enable the scoped `ruff D` enforcement.
6. Turn on Pages hosting.

## Alternatives considered

- **Keep prose docstrings; generate with a signature-introspecting tool, no format change.** `pdoc`
  or `mkdocstrings` already render prose docstrings *and* typed signatures (the types come from
  annotations, not the docstring), so a browsable, typed reference is achievable today without
  touching a single docstring. This is the lowest-cost path, and was the recommended one; it is
  recorded here as the baseline. This proposal goes further — adopting Google-style structured
  sections on the public surface — to get a predictable, parameter-level description in the
  reference, accepting the migration cost for that consistency.
- **`Sphinx + autodoc + napoleon`.** Parses the same Google docstrings, but is reStructuredText-
  native (needs `myst-parser` for Markdown) and `autodoc` imports modules, forcing
  `autodoc_mock_imports` for every optional / lazy dependency. `mkdocstrings` + `griffe` is
  Markdown-native and static. Kept as a fallback.
- **Migrate the whole tree, internal helpers included.** Rejected: ~1,000 docstrings is high churn
  and conflict surface, and `Args:` on tiny private helpers is the *what*-narration the repo avoids.
  Scope to the public surface.
- **Do nothing.** The status quo: an implicit, undocumented prose convention and no rendered API
  surface. Rejected — the convention drifts, and the growing public API has no map for human or
  agent readers.
- **A dedicated new roadmap topic.** Filed instead under *Contributor workflow* — the
  [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md) topic —
  since it is about how contributors and agents understand the code, following the precedent of not
  splitting a topic for a single item.

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
