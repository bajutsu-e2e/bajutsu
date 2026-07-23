**English** · [日本語](BE-0217-harden-review-prompt-ja.md)

# BE-0217 — Harden the automated PR review prompt with research-backed policy

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0217](BE-0217-harden-review-prompt.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0217") |
| Implementing PR | [#865](https://github.com/bajutsu-e2e/bajutsu/pull/865) |
| Topic | Contributor workflow |
| Related | [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md) made Claude Code
the automated PR reviewer and gave it a repo-flavored contract,
[`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md). That contract covers
the three prime directives, the lenses `implement-be` already trusts (silent failures, type
design, test coverage), and a handful of house conventions (bilingual docs, the docstring
standard, roadmap links). It has proven itself on real PRs, but its lens list was authored from
the project's own conventions, not from an inventory of what a reviewer actually needs to catch.

This item hardens the same contract using two kinds of evidence gathered directly rather than
guessed: (1) the pattern of what **GitHub Copilot** — the reviewer this project runs in parallel
with during BE-0203's migration — has actually flagged across this repository's own recent pull
requests, and (2) established, citable code-review standards (Google's Engineering Practices,
the Python PEPs, Bandit's rule catalogue, OWASP's secure-code-review guidance, and the
Conventional Comments specification). It changes only the review *contract*
(`.github/claude-review-prompt.md`) and, where the format changes, the workflow's identification
prefix; it does not touch the advisory/non-blocking guarantees BE-0203 established — this item
is bound by the same prime-directive-1 boundary.

## Motivation

- **The repo's own review history already shows the gaps.** Reading Copilot's inline comments
  across this repository's recent merged PRs surfaces the same few patterns recurring, none of
  which the current prompt names explicitly:
  - **Comment/docstring drift ("comment rot").** [#825](https://github.com/bajutsu-e2e/bajutsu/pull/825)'s
    `state.py` docstring undercounted the storage seams after a fourth one was added;
    [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816)'s `platform_lifecycle.py` docstring
    called an already-referenced platform a "future" one and miscounted the run predicates;
    [#813](https://github.com/bajutsu-e2e/bajutsu/pull/813)'s `record.py` docstring contradicted
    the code's actual return behavior.
  - **`Protocol` methods with docstring-only bodies.** In [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816),
    several `RunEnvironment`/`CrawlEnvironment` methods had only a docstring for a body — no
    `...`/`raise NotImplementedError` — which makes them concrete methods that silently return
    `None` despite a non-`None` return annotation. `mypy --strict` does not catch this.
  - **Unescaped string interpolation building structured data.** In
    [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811), three separate functions in
    `bajutsu/templates/serve.author.js` interpolated a raw selector id into a YAML flow mapping
    without escaping, so an id containing `:` produced invalid YAML.
  - **Brittle and flaky tests.** Also in [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811),
    a test asserted an exact quote-style substring that would fail on a harmless formatting
    change; in [#808](https://github.com/bajutsu-e2e/bajutsu/pull/808), a test's assertions
    depended on wall-clock time and could flake under a slow CI run.
  - **Stale claims in PR/roadmap prose.** [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811)'s
    own roadmap file still called its `Implementing PR` field "pending" after the PR already
    linked it.

  None of these are things the current prompt's three lens categories (prime directives,
  `implement-be` lenses, house conventions) point a reviewer at by name — the reviewer catches
  them only if it happens to notice, not because the contract asked it to look.

- **A generic security/quality checklist would mostly duplicate the gate — precision matters
  more than breadth.** The instinct is to bolt on a broad Bandit/OWASP-style checklist, but this
  repository's `ruff` configuration ([`pyproject.toml`](../../pyproject.toml)) already selects
  the `S` (Bandit-equivalent) rule family as part of `make lint`, so hardcoded secrets, unsafe
  `yaml.load`, `subprocess(shell=True)`, weak randomness, missing request timeouts, and disabled
  certificate verification are already gated on the Python side — a prompt that re-flags them
  duplicates a check that already blocks the PR, and one that flags `assert` usage or bare
  `subprocess` argv calls actively contradicts this repo's own `S101`/`S603` ignores, which carry
  documented rationale in `pyproject.toml`. The reviewer's distinct value is in what the gate
  structurally *cannot* see: cross-file semantic drift, a `Protocol` body that type-checks but is
  wrong, and — concretely, per the `#811` example above — injection-shaped bugs in
  `bajutsu/templates/serve.*.js`, which `make lint-js` only `node --check`s for syntax and has no
  security or escaping lint at all. This item's design leads with that distinction rather than
  importing a generic checklist wholesale.
- **The prompt has no design/architecture lens.** [Google's Engineering Practices reviewer
  guide](https://google.github.io/eng-practices/review/reviewer/looking-for.html) puts *design*
  above functionality, complexity, tests, naming, comments, and style in review priority — whether
  the change belongs where it is, whether it over-engineers or under-engineers the problem, and
  whether it fits the surrounding architecture. The current prompt is entirely rule/checklist
  shaped (prime directives, lenses, conventions) with nothing that asks the reviewer to weigh in
  on the shape of the change itself, which is the review dimension both Google's own guidance and
  this item's requester consider highest-value.
- **Comment severity is currently unlabeled prose.** Every finding today reads as undifferentiated
  text after the `🤖 **Claude Code** —` prefix; a human has to read each one to learn whether it is
  a nitpick or a real issue. The [Conventional Comments](https://conventionalcomments.org/)
  specification — a community-adopted micro-format (`label [decorations]: subject`, with labels
  like `issue`, `suggestion`, `nitpick`, `question`, `praise` and decorations like
  `(non-blocking)`) — gives findings a scannable severity signal for free, and doubles as a
  running, visible reminder that nothing the reviewer posts is a merge blocker, reinforcing prime
  directive 1 in the artifact itself rather than only in the surrounding prose.
- **Prose/wording consistency has no named standard to check against.** The current "house
  conventions" section flags bilingual-docs gaps and the docstring standard, but nothing about
  terminology drift (the same concept named two different ways across files) or a doc/comment
  making a claim the diff just falsified — exactly the comment-rot pattern above. [Google's
  developer documentation style guide](https://developers.google.com/style) and its [documentation
  best practices](https://google.github.io/styleguide/docguide/best_practices.html) name this
  class of defect directly and are citable, established references for it.

## Detailed design

The work is entirely inside the existing review contract and its identification prefix — no
workflow permissions, triggers, or gate semantics from BE-0203 change.

1. **A leading "review what the gate can't" principle.** Add a short paragraph near the top of
   `.github/claude-review-prompt.md`, before the lens sections, stating explicitly that the
   reviewer should not re-flag what `make check` already gates (ruff's selected rules including
   the `S`/Bandit family, `mypy --strict`, the docstring linter, the coverage floor) and must not
   re-litigate a documented, file-scoped ruff ignore (e.g. `S101`, `S603`, and the file-level
   ignores in `pyproject.toml`) — citing the rationale already recorded there. This is the
   organizing principle the rest of this item's additions follow: each new lens below targets
   something the deterministic gate structurally cannot see.
2. **A design & architecture lens.** A new lens instructing the reviewer to comment on whether the
   change belongs where it's placed, whether it over- or under-engineers the problem it solves,
   and whether it fits the surrounding module boundaries and existing seams — the same "design
   first" priority [Google's reviewer guide](https://google.github.io/eng-practices/review/reviewer/looking-for.html)
   documents. Framed as critique and suggestion, never a verdict, consistent with prime directive 1.
3. **A comment/docstring-drift ("comment rot") lens.** Flag a docstring, comment, or prose claim
   that contradicts the code it describes, contradicts another part of the same file/PR, or states
   something as pending/future that the diff itself just resolved — the pattern behind
   `#825`, `#816`, `#813`, and `#811` above.
4. **A `Protocol`/abstract-method body lens.** Flag a `Protocol` or `abc` method whose body is only
   a docstring (no `...`, `raise NotImplementedError`, or real implementation) when its return
   annotation is non-`None` — a `mypy --strict`-invisible bug class this repo's own `#816` review
   caught by hand.
5. **An unescaped-structured-data-interpolation lens, scoped to ungated surfaces.** Flag string
   concatenation/interpolation that builds YAML, JSON, HTML, or shell text from a variable without
   escaping — with an explicit callout that `bajutsu/templates/serve.*.js` (checked only by
   `node --check` in `make lint-js`, per [`Makefile`](../../Makefile)) is exactly this kind of
   ungated surface, per the `#811` precedent.
6. **A test-quality lens: brittleness and flakiness.** Flag a test assertion pinned to incidental
   formatting (exact quoting/whitespace) where behavior is what matters, and flag a test whose
   pass/fail depends on wall-clock time or another non-deterministic input — framed as extending
   this project's own "determinism first" ethos (prime directive 2) to the test suite itself, not
   only to `run`.
7. **Adopt Conventional Comments labeling.** Prefix every inline finding with a
   [Conventional Comments](https://conventionalcomments.org/) label — `issue`, `suggestion`,
   `nitpick`, `question`, or `praise` — plus the `(non-blocking)` decoration, ahead of the existing
   `🤖 **Claude Code** —` identification (e.g. `🤖 **Claude Code** — issue (non-blocking): ...`).
   Document in the prompt that `(non-blocking)` is not optional decoration here — every label this
   reviewer posts carries it, since the review is advisory by design (prime directive 1); no label
   is ever blocking.
8. **A wording/terminology-consistency lens.** Extend the existing "house conventions" section to
   flag the same concept named two different ways across files touched by the diff, an acronym
   used unexpanded on first appearance, and a PR body or roadmap `Progress` claim that doesn't
   match the diff it describes — grounded in [Google's developer documentation style
   guide](https://developers.google.com/style) and [documentation best
   practices](https://google.github.io/styleguide/docguide/best_practices.html), and in the stale
   `Implementing PR` field `#811`'s own review caught.
9. **Verification.** Same shape as BE-0203 item 9: `actionlint` (already in `make check`) validates
   no workflow YAML changes are needed since this item touches only the prompt markdown; the
   review *behavior* needs a live PR, so verification is manual — open a test PR that reproduces
   one instance of each new lens (a stale docstring claim, a docstring-only `Protocol` method, an
   unescaped YAML interpolation in a `serve.*.js` template, a wall-clock-dependent test assertion)
   and confirm the reviewer's inline comments catch each one with the new Conventional Comments
   label, and that the summary and every inline comment stay non-blocking.

## Alternatives considered

- **Import a generic Bandit/OWASP security checklist wholesale.** Rejected: this repo's `ruff`
  configuration already selects the `S` (Bandit-equivalent) rules as part of the gate, so most of
  that checklist would duplicate an existing blocking check; the parts that wouldn't
  (`S101`/`S603`) are deliberately ignored with documented rationale, and re-flagging them would
  contradict the repo's own recorded decision. Item 1's "review what the gate can't" principle
  replaces a checklist import with a scoping rule.
- **Make findings severity-weighted and treat `issue`-labeled comments as blocking.** Rejected
  outright: it would put an LLM judgment on the merge path, violating prime directive 1 the same
  way BE-0203 already ruled out a required status check. Every Conventional Comments label this
  item adds carries `(non-blocking)` — labeling is for human scannability, not for gating.
- **Leave comments as free-form prose (no Conventional Comments labels).** Considered as the
  lower-effort option, but rejected: an unlabeled finding forces a human to read the whole comment
  to learn its severity, and the labeled format's `(non-blocking)` decoration is a second, visible
  reinforcement of the advisory guarantee — a low-cost addition given the format is a citable,
  pre-existing standard rather than a bespoke vocabulary this item would need to invent and
  document from scratch.
- **Fold this into BE-0203 instead of a new item.** Considered, since every prior change to
  `.github/claude-review-prompt.md` (workflow hardening, mechanism fixes, wording corrections) has
  landed as a `(BE-0203)`-tagged commit against that item's own `Progress` log even after its
  `Status` became `Implemented`. This item is scoped differently, though: it is a substantial,
  externally-researched policy rewrite of the review lenses themselves — not an operational fix to
  the workflow BE-0203 shipped — so it is proposed as its own item, cross-linked via `Related`
  rather than folded into BE-0203's log.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add the "review what the gate can't" leading principle (item 1)
- [x] Add the design & architecture lens (item 2)
- [x] Add the comment/docstring-drift ("comment rot") lens (item 3)
- [x] Add the `Protocol`/abstract-method docstring-only-body lens (item 4)
- [x] Add the unescaped-structured-data-interpolation lens, scoped to ungated JS templates (item 5)
- [x] Add the test brittleness/flakiness lens (item 6)
- [x] Adopt Conventional Comments labeling in inline findings (item 7)
- [x] Add the wording/terminology-consistency lens (item 8)
- [ ] Verify each new lens on a live test PR (item 9) — manual, needs a live PR against the reviewer

Log:

- Proposal authored, grounded in a read of this repository's own recent Copilot review comments
  (`#808`, `#811`, `#813`, `#816`, `#825`) and in external standards research (Google Engineering
  Practices, the Python PEPs, Bandit's rule catalogue, OWASP's secure-code-review guidance, the
  Conventional Comments specification, Google's developer documentation style guide).
- Rewrote `.github/claude-review-prompt.md` to fold in items 1–8: added the "review what the gate
  can't see" leading principle, a design & architecture lens, the comment-rot / docstring-only
  `Protocol` body / unescaped-interpolation semantic lenses, a test brittleness/flakiness lens,
  Conventional Comments labeling on every inline finding, and a terminology-consistency clause under
  house conventions. The workflow YAML is unchanged (the identification prefix lives in the prompt).
  Item 9 stays open — it requires a live PR to exercise the reviewer.

## References

- [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md) — the item that
  established Claude Code as the automated PR reviewer and the contract this item hardens.
- [`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) — the file this item
  changes.
- [`pyproject.toml`](../../pyproject.toml) — the `ruff` `select`/`ignore` configuration item 1's
  "review what the gate can't" principle is scoped against.
- Pull requests read for this repository's own review-comment evidence:
  [#808](https://github.com/bajutsu-e2e/bajutsu/pull/808),
  [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811),
  [#813](https://github.com/bajutsu-e2e/bajutsu/pull/813),
  [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816),
  [#825](https://github.com/bajutsu-e2e/bajutsu/pull/825).
- [Google Engineering Practices — What to look for in a code
  review](https://google.github.io/eng-practices/review/reviewer/looking-for.html) and [The
  Standard of Code Review](https://google.github.io/eng-practices/review/reviewer/standard.html) —
  the design-first priority order behind item 2.
- [PEP 8](https://peps.python.org/pep-0008/), [PEP 257](https://peps.python.org/pep-0257/),
  [PEP 20](https://peps.python.org/pep-0020/) — Python style/docstring/design-philosophy
  references.
- [Bandit rule catalogue](https://bandit.readthedocs.io/en/latest/plugins/index.html) — the rule
  family this repo's `ruff` `S` select already covers, referenced in Motivation.
- [OWASP Secure Code Review Cheat
  Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secure_Code_Review_Cheat_Sheet.html) —
  general secure-review reference informing item 5's scoping.
- [Conventional Comments](https://conventionalcomments.org/) — the labeling specification item 7
  adopts.
- [Google developer documentation style guide](https://developers.google.com/style) and
  [Documentation Best Practices](https://google.github.io/styleguide/docguide/best_practices.html)
  — the wording/terminology-consistency references behind item 8.
