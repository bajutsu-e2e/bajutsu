**English** · [日本語](ja/ai-development.md)

# Developing with AI agents (and humans) in parallel

> How several sessions — humans and AI agents — work this repo at the same time without
> colliding or regressing each other. The short version lives in [`CLAUDE.md`](../CLAUDE.md);
> this page is the full operational guide.

The whole design rests on one property: **the deterministic gate is cheap, runs anywhere, and
mirrors CI exactly.** This is what lets work fan out safely — every branch is independently
verifiable, so "green locally" reliably predicts "green in CI", and the test suite is a
regression net that catches one session breaking another's feature.

## The gate

```bash
make check        # ruff check . + mypy bajutsu + pytest -q
```

Same three steps as [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). The Python core
needs no Simulator, so it runs on Linux in seconds. Run it before you call a change done and
again before you push. On-device E2E (macOS + Simulator) is a separate, heavier path and is
**not** part of this gate.

## One topic per branch

- Branch off `main`: `claude/<short-topic>` for agents, `<user>/<topic>` for humans.
- Keep each branch small and single-purpose. Small diffs merge fast and rarely conflict.
- Don't open a PR unless the human asks; push your branch and let them open it.

## Never push red

The tracked **pre-push hook** runs `make check` and refuses the push if anything fails:

```bash
make setup   # uv sync --group dev + wire the git hooks (run once on a fresh clone)
```

`core.hooksPath` is a per-clone local setting that clone/pull never carry over, so an existing
clone won't have it — but you don't need to remember: `make check` (and `make hooks`) re-wires it
every time, so the gate self-heals right before you push. Claude Code web sessions also get it
automatically via [`.claude/hooks/session-start.sh`](../.claude/hooks/session-start.sh). In a real
emergency you can bypass with `git push --no-verify`, but the next CI run will still gate the PR.

When you change behavior, change a test with it — the suite is the contract that protects every
other session from your change.

## Rebase early, integrate small conflicts

```bash
git fetch origin
git rebase origin/main      # pull in others' merged work; resolve while conflicts are tiny
make check                  # re-verify after the rebase
```

Rebasing frequently means you meet other sessions' merged work early, when conflicts are a line
or two — not at the end as a tangled merge.

`make hooks` also self-heals two local git settings that take the sting out of the conflicts that
remain (BE-0043), so you don't have to configure them by hand:

- a **`uv.lock` merge driver** ([`scripts/merge-uv-lock.sh`](../scripts/merge-uv-lock.sh), mapped via
  [`.gitattributes`](../.gitattributes)) that **regenerates the lockfile from `pyproject.toml`** on a
  conflict instead of line-merging resolver output. If `pyproject.toml` itself conflicts, `uv lock`
  fails and git leaves `uv.lock` conflicted — resolve `pyproject.toml` first, then re-merge.
- **`rerere`** (reuse recorded resolution), so a conflict you have resolved once replays
  automatically the next time the same conflict appears.

Like `core.hooksPath`, these are per-clone local git settings that clone/pull never carry over, so
`make check` / `make setup` re-wire them every time.

## Isolate concurrent sessions with worktrees

Two agents must never edit the same checkout. Give each session its own
[worktree](https://git-scm.com/docs/git-worktree) + branch, all sharing one `.git`:

```bash
# from the main checkout
git fetch origin            # always sync main first — branch off the latest, not a stale ref
git worktree add ../bajutsu-<topic> -b claude/<topic> origin/main
cd ../bajutsu-<topic>
make setup                   # uv sync --group dev + wire the hooks for this worktree
```

The `git fetch origin` is not optional: `origin/main` is a local tracking ref that only
advances when you fetch, so skipping it branches the new worktree off whatever main looked like
last time — re-introducing conflicts that other sessions already merged away. Fetch, then branch
off the fresh `origin/main`.

When the branch is merged (or abandoned), clean up:

```bash
git worktree remove ../bajutsu-<topic>
```

Generated and scratch output — `runs/`, `tmp/`, `.venv/`, build artifacts — is gitignored on
purpose; keep it out of commits so worktrees stay independent.

## Stay in your lane

Touch only the files your task needs. The architecture is layered (scenario → orchestrator →
driver → backend; see [architecture](architecture.md)), so most tasks live in one layer. If a
change must cut across many modules — e.g. altering the abstract **Driver API**, the scenario
**schema**, or a shared config shape — call it out up front so other sessions can steer clear of
that surface (or wait for it to land) instead of building on top of a moving target.

High-traffic shared surfaces to coordinate on:

| Surface | Files | Why it's shared |
|---|---|---|
| Driver API | [`bajutsu/drivers/base.py`](../bajutsu/drivers/base.py) | every backend + the orchestrator depend on it |
| Scenario schema | [`bajutsu/scenario.py`](../bajutsu/scenario.py) | the hub artifact; codegen/runner/report all read it |
| Config shape | [`bajutsu/config.py`](../bajutsu/config.py) | per-target layering every command resolves through |

## CI keeps the branches honest

CI runs the same gate on every PR and uses
`concurrency: ci-${{ github.ref }}` with `cancel-in-progress`, so re-pushes to the same branch
supersede stale runs instead of piling up. Two PRs that each pass independently can still
conflict in behavior — the merge is where they meet, which is exactly why the deterministic test
suite (not an LLM, not a human eyeball) is the arbiter. Keep the suite meaningful and your branch
rebased, and parallel work composes.

## Pull requests: title and body

Don't open the PR yourself unless the human asks (see [One topic per branch](#one-topic-per-branch));
push your branch and let them open it. But when you draft a PR — or write the title and body for a
human to open — follow the shape below. It is reverse-engineered from the PRs this repo already
merges, so matching it keeps the history uniform and a reviewer always finds the same things in the
same places. **The title and body are always in English**, whatever language the session ran in.

### Title

One scoped, [Conventional Commits](https://www.conventionalcommits.org/) subject — the same line you
would write as the lead commit:

```
[BE-NNNN] type(scope): summary
```

- **`type(scope):`** — a conventional-commit type (`feat`, `fix`, `docs`, `chore`, `ci`, `refactor`,
  `test`) and the area it touches (`run`, `web`, `codegen`, `audit`, `roadmap`, `hooks`, `ja`, …),
  e.g. `feat(audit):`, `fix(hooks):`, `docs(roadmap):`.
- **summary** — imperative mood, lower-case, no trailing period; a single line a reviewer reads at a
  glance. A roadmap proposal reads `docs(roadmap): propose <the idea>`.
- **`[BE-NNNN]` prefix** — only when the PR is tied to a roadmap item, in brackets before the scoped
  subject (e.g. `[BE-0017] feat(mcp): add MCP server`). A PR with no roadmap item keeps the plain
  scoped subject. When the PR *introduces* a new roadmap item, draft with the literal `[BE-XXXX]`
  placeholder — the `roadmap-id` workflow rewrites it to the allocated number (see
  [Roadmap items](#roadmap-items-be-ids-strict)).

### Body

Two parts are mandatory — `## Summary` and a verification statement — and the rest appear as the
change warrants, in the order below. Match the depth to the diff: a one-file fix is a short Summary
and the green numbers; a cross-cutting feature earns the full set. Write the prose the way these
sections already read in the merged PRs — present tense, describing what the change *is*, not a
narration of how you got there. Keep **bold** for the few nouns that carry the change, never whole
sentences. In the change list, follow the recurring `**path** — what it does, and why this seam`
shape: name the design choice, not just the edit.

The sections that recur, and what each carries:

- **`## Summary`** (mandatory) — one to three short paragraphs: what the PR does and *why it
  matters*, with the key nouns in **bold**. Open with the change itself, not its history. When the
  PR is one slice of a larger item, name the slice and say what merging it does to the item's
  `Status` (e.g. moves it to *In progress*).
- **`## What changed`** / **`## Changes`** — one bullet per file or component, the **path or
  component in bold**, then an em-dash and what it does *and why this seam* — the design choice, not
  just the edit. Mark new files `(new)`. Group by component, not by commit; the reviewer reads the
  result, not the path you took to it.
- **`## Prime-directive compliance`** — whenever the change touches tool behavior or the runtime.
  State it plainly: no model is consulted on the verdict, the `run` / CI gate stays deterministic,
  and per-target differences stay in config — a line per [prime
  directive](../CLAUDE.md#prime-directives-do-not-violate) the change bears on. A docs-only or
  infrastructure PR can say so in a sentence instead.
- **`## Scope`** (often *Scope (deferred to …)*) — what is deliberately **not** in this PR, so a
  reviewer never has to infer the boundary. For a slice of a larger item, list what later slices
  still owe.
- **`## Verification`** / **`## Testing`** / **`## Test plan`** (mandatory, in some form) —
  `make check` green with the concrete numbers it printed (`N passed, coverage X%`), and a sentence
  on what the new tests cover. Call out anything the gate *can't* exercise (a workflow's runtime, a
  Simulator-only path) so the reviewer knows what was and wasn't proven — accuracy here is the point,
  don't claim a path was tested when it wasn't.
- For a roadmap proposal: **`## Files`** (the bilingual pair) and **`## BE ID allocation`** (the
  `BE-XXXX` placeholder note — the workflow numbers it; don't hand-edit the number).
- **`## Notes`** — caveats, a related or competing open PR, an expected merge conflict and how to
  resolve it.

Close the body with reference-style links for the items you cited (`[BE-0049]: roadmaps/…`) and the
footer `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Reserve GitHub's
`> [!NOTE]` callouts for a caveat a reviewer must not miss.

A small fix needs only the two mandatory parts:

```markdown
## Summary

Follow-up to #189: `session-start.sh` could abort the hook — and the session — under `set -e`
when `CLAUDE_PROJECT_DIR` is unset. This makes the project-dir discovery best-effort.

## Verification

`shellcheck` clean; `make check` green (1059 passed, coverage 87.4%). Repro'd that the hook now
logs the skip and exits 0 instead of aborting.
```

A feature or roadmap-bearing PR fills the full shape:

```markdown
## Summary

The **<slice>** of [BE-NNNN]. <What it does and why it matters, key nouns in bold.> This moves
the item to **In progress**.

## What changed

- **`bajutsu/<file>.py` (new)** — <what it does, and why this seam>.
- **`bajutsu/<other>.py`** — <the change, and the design choice behind it>.
- **docs (en/ja)** — <what was documented>.

## Prime-directive compliance

No model is consulted on the verdict; the `run` / CI gate stays deterministic; per-target
differences stay in config.

## Scope (deferred to later BE-NNNN slices)

<What is deliberately not in this PR.>

## Verification

`make check` green: format-check / ruff / mypy (Success) / test (N passed, coverage X%). New
tests cover <…>.

[BE-NNNN]: roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

The short form of these rules is in [`CLAUDE.md`](../CLAUDE.md).

## Responding to PR review comments

Reviews get answered comment by comment, by **whoever owns the pull request — a human contributor
or an AI agent alike**. When a reviewer (GitHub Copilot and other AI reviewers, or a human) leaves
comments, keep working until every comment is resolved, then **reply to each comment
individually**. A single summary reply on the PR is not enough: each comment thread gets its own
reply, so the thread that raised a point is the thread that records its resolution.

Every reply states two things:

- **that the comment is addressed** — fixed in code, or consciously declined; and
- **the grounds for it** — the concrete change that resolves it (what you altered, and where —
  cite the commit or the file/line), or, when you make no change, the specific reason the comment
  does not apply.

A bare "done" or a 👍 is not a reply under this rule; the grounds are what let whoever later reads
the thread audit the resolution. Keep each reply short and factual — the point is evidence, not
narration.

When you are unsure how a comment should be handled — the fix is ambiguous, or it touches
something architecturally significant — ask rather than guess (an AI agent checks with the human
driving it; a human contributor checks with the reviewer or a maintainer), and leave that thread
open until it is decided.

## Roadmap items: BE IDs (strict)

The roadmap is **one directory per item** under [`roadmaps/`](../roadmaps/README.md). Each item lives in
`roadmaps/<category>/BE-NNNN-<slug>/`, which holds the English file `BE-NNNN-<slug>.md` and its
Japanese version `BE-NNNN-<slug>-ja.md` (same ID and slug). **BE** stands for *Bajutsu Evolution* and `NNNN`
is a **zero-padded, 4-digit, monotonically increasing** ID. There are **four** folders, one per `Status`
value (BE-0078): `roadmaps/implemented/` (`Implemented`), `roadmaps/in-progress/` (`In progress`),
`roadmaps/proposals/` (`Proposal`), `roadmaps/deferred/` (`Proposal (deferred)`).

When you add a roadmap item:

1. **Allocate the next ID** = the highest existing `BE-NNNN` + 1, counting all four folders. Find the current
   max with:
   ```bash
   ls -d roadmaps/{implemented,in-progress,proposals,deferred}/BE-*/ | sort | tail -1
   ```
   Never reuse, skip, or guess a number.
2. **Create the item directory and both language files** under `roadmaps/proposals/` (a new item is always a
   proposal first) — `roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>.md`
   (English) and `roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>-ja.md` (Japanese, same ID & slug). **Do not
   hand-edit the index tables** — they are generated from each item's own metadata. Run
   `make roadmap-index` (or `python scripts/build_roadmap_index.py`) to regenerate the tables between the
   `<!-- GENERATED:* -->` markers in **both** index pages ([en](../roadmaps/README.md), [ja](../roadmaps/README-ja.md)).
   The item's `Status` (its bucket) + `Topic` decide which section it lands in, so an item in an existing
   section needs no manual table edit; `tests/test_roadmap_index.py` (run by `make test`) fails if the
   committed index drifts. The first item of a topic to reach a bucket needs its own marked section (the
   generator names the missing region).
3. **IDs are permanent.** Never renumber an existing item — not when its status changes, not when
   it is completed, not when it is removed from a table. A BE ID, once assigned, refers to that
   item forever.

Allocating by hand races, so you do not have to: the `roadmap-id` workflow assigns IDs at PR time,
and two defenses ([BE-0061](../roadmaps/implemented/BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md))
keep them unique across `main` *and* every open PR. **Allocation reserves atomically.** Each ID it
hands out is claimed as a `refs/be-claims/<NNNN>` git ref through GitHub's create-ref API — a
compare-and-set that fails if the ref already exists — so two branches allocating in the same window
cannot both take a number; the loser re-picks. A claim is released when the PR closes (its IDs are by
then on `main`, or abandoned), with a daily sweep reaping any leak. **Repair is the backstop** for
whatever still slips through — a hand-typed concrete ID, or a branch predating the machinery: the
`roadmap-id-repair` workflow, on a push to `main` and on a daily schedule, re-attempts allocation on
every open roadmap PR. For an item a PR introduces (a slug not yet on `main`) whose number is already
taken, it allocates the next free ID — moving the directory and rewriting the files, cross-references,
and PR title — and pushes the fixup onto the branch (`make roadmap-id-repair` runs the same step
locally). Authority — who keeps a contested number — is `main` first (a merged item always wins),
else the **lowest open-PR number** holding it; only the loser moves. An item the branch merely
inherited from an older `main` (its slug already there) is left for a rebase, never renumbered.
Drafting with the `BE-XXXX` placeholder is still the norm — it keeps you from guessing a number at
all. (Fork PRs can be neither pushed to nor have claim refs created, so both workflows act only on
same-repo PRs.)

Each file follows the **Swift-Evolution proposal format** — a metadata block (`* Proposal`,
`* Author`, `* Status`, `* Topic`, optional `* Origin`) followed by `## Introduction` /
`## Motivation` / `## Detailed design` / `## Alternatives considered` / `## References`. Fill what
you can and mark unknowns `TBD`. **Name the author by GitHub handle** —
`* Author: [@handle](https://github.com/handle)`, the account of whoever first authored the item
(for an AI-assisted draft, the person who drove and committed it). The **Status** field is the single
source of truth for both the folder an item lives in and the index bucket it appears under — a
bijection (BE-0078):

| Status | Folder / index bucket |
|---|---|
| `Implemented` | `roadmaps/implemented/` — shipped |
| `In progress` | `roadmaps/in-progress/` — accepted, actively being built |
| `Proposal` | `roadmaps/proposals/` — under consideration |
| `Proposal (deferred)` | `roadmaps/deferred/` — parked |

As an item advances, **update its Status** and regenerate the index (its row moves to the right bucket
automatically). When its status changes — it starts being built, or it ships — the **`roadmap-promote`**
workflow **moves its directory** to the matching folder (keeping the same ID and slug) and regenerates
the index on your PR — or run `make roadmap-promote` to do it locally. `make test` fails if a folder
and `Status` disagree, so an item can never merge while filed under the wrong folder. A promotion also
**repairs the item-to-item cross-links** that the move would otherwise break (a sibling `../BE-NNNN/`
link is wrong once the target sits in a different status folder) — the same self-healing the index
already had (BE-0069). **`make lint-roadmap`** (in `make check`) is the gate for this: it fails if any
item's markdown link to another item does not resolve, or if an `Author` is not a `[@handle](…)` link;
`make lint-roadmap` with `--fix` (via `python scripts/lint_roadmap.py --fix`) rewrites a broken item
link to the target's current folder.
Milestones M1–M4 are `BE-0001`–`BE-0004` (implemented).

This is a hard rule agents must follow; the short form is in [`CLAUDE.md`](../CLAUDE.md).

## Documentation style (every document, both languages)

These rules apply to all documentation — English under `docs/` and the Japanese mirror under
`docs/ja/` — and to every future update, not just new files. Agents must follow them, and they
apply equally when reporting on or summarizing work.

- **Write natural prose.** A Japanese document must read as natural Japanese; an English document
  must read as natural English. A mirror conveys the same content naturally in its own language —
  it is not a word-for-word transliteration of the other.
- **No coined terms.** Use established, widely-used technical terms and ordinary words. Do not
  invent vocabulary, and do not stretch a word into a meaning it does not normally carry.
- **No forced or unnatural translation.** Use the conventional translation of a term. When
  translating it would read unnaturally, keep the original term instead — usually the English word
  (e.g. `selector`, `actuator`, `backend`, `assertion`) rather than a contrived literal rendering.
- **No omissions; be self-contained.** A reader must be able to understand the document on its own.
  Spell out an abbreviation the first time it appears, give a term the context it needs, and do not
  assume the reader has already read another page.
- **Japanese prose follows the `japanese-tech-writing` skill.** Whether you write the Japanese side
  fresh or translate the English mirror into `docs/ja/` (or a roadmap `*-ja.md`), apply
  [`japanese-tech-writing`](../.claude/skills/japanese-tech-writing/): it is the authoritative style
  for Japanese prose in this repo, and a translation must read as natural Japanese under those norms,
  not a literal rendering of the English.
- **Japanese documents use 敬体 (the polite *desu/masu* style, ですます調).** Every Japanese file
  under `docs/ja/` and every roadmap `*-ja.md` is written in 敬体, never the plain *da/dearu* style
  (常体). Keep the whole document consistent: only sentence-final predicates take the polite form —
  embedded clauses, conditionals, and connective forms (連体修飾・〜すると・〜であり) stay plain as
  usual, and headings or pure noun-phrase labels (体言止め) need no copula.

The short form of these rules is in [`CLAUDE.md`](../CLAUDE.md).

## Code documentation comments (docstrings) — BE-0065

The *Documentation style* rules above govern the prose docs. This is the companion rule for
**docstrings in the Python core** — what the generated API reference (`make docs`, MkDocs +
`mkdocstrings`) renders. The reference build is a separate, heavier path kept out of `make check`,
adds no LLM, and never runs inside `run`, so the prime directives hold by construction.

- **English, like every code comment.** Code (and its docstrings) is not bilingual; only the prose
  docs under `docs/` are.
- **Google style on the public surface.** The public API — the `Driver` protocol and shared types
  in [`bajutsu/drivers/base.py`](../bajutsu/drivers/base.py), the CLI, the MCP tools, the scenario
  schema, and the public functions of the runner / `assertions` / `network` — uses a one-line
  summary followed by `Args:` / `Returns:` / `Raises:` (and `Yields:` / `Examples:`) **only where
  they add information**. The generated reference excludes private (`_`-prefixed) members.
- **Internal helpers stay prose.** A module-private `_helper` keeps one purposeful line of *why*;
  forcing an `Args:` block onto a small helper is the *what*-narration this repo avoids.
- **Never restate types.** Types live in the annotations (`mypy` is strict, `ruff`'s `ANN` rules are
  on), and the generator reads them from the signature. `Args:` / `Returns:` describe *meaning* —
  units, constraints, what `None` means — not the type.
- **Why, not what.** Rationale, invariants (especially anything protecting determinism),
  trade-offs, edge cases; tie a behavior's rationale to its `BE-NNNN` item. Match the surrounding
  density — short and purposeful, no narration.
- **Keep the per-field idiom.** For a `TypedDict` or a constant-holder class, the per-field inline
  comment carries each field's *why* better than a prose block — keep it rather than converting to
  `Args:`-style sections.

Example — a public function carries the structured sections (the determinism invariant leads, the
rationale ties to a BE item, and the types are *not* repeated):

```python
def resolve_unique(elements: list[Element], sel: Selector) -> Element:
    """Resolve a selector to exactly one element for a single action.

    A single action requires a unique match, so an ambiguous selector fails rather than acting on
    "whatever matched first" (the determinism core, BE-0001).

    Args:
        elements: One `query()` snapshot of the on-screen elements.
        sel: The selector to resolve. `index` is honored only as a last resort, picking the nth of
            several candidates.

    Returns:
        The one element the selector resolves to.

    Raises:
        ElementNotFound: Nothing matched, or `index` is out of range.
        AmbiguousSelector: Two or more matched and no `index` disambiguates.
    """
```

An internal helper stays one line of *why* — no `Args:` block:

```python
def _contains(outer: Frame, inner: Frame) -> bool:
    """Whether `inner`'s frame sits inside `outer`'s (edges inclusive)."""
```

**Migration is phased and incremental** ([BE-0065](../roadmaps/in-progress/BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md)):
the site renders today from the existing prose docstrings (typed signatures already give a useful
reference); public-API docstrings move to Google style module by module in small PRs, and the
scoped `ruff` `D` enforcement and Pages hosting land after. **Don't rewrite a whole module's
docstrings as a side effect of an unrelated change** — keep each migration its own small PR.

Build the reference locally with `make docs` (or `make docs-serve` to preview); it needs the `docs`
extra.
