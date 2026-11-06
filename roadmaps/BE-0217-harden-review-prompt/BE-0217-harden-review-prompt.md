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

[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md) made Claude Code the
automated PR reviewer. It gave the reviewer a repo-flavored contract,
[`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md). The contract covers
three groups of checks:

- the three prime directives;
- the lenses that `implement-be` already trusts (silent failures, type design, test coverage);
- a handful of house conventions (bilingual docs, the docstring standard, roadmap links).

The contract has proven itself on real PRs. But its lens list came from the conventions of the project.
It did not come from an inventory of what a reviewer needs to catch.

This item hardens the same contract with two kinds of evidence, gathered directly rather than
guessed:

1. The pattern of what **GitHub Copilot** has actually flagged across the recent pull requests of this
   repository. This project runs Copilot in parallel with Claude Code during the migration of BE-0203.
2. Established, citable code-review standards: Google's Engineering Practices, the Python PEPs, Bandit's rule
   catalogue, OWASP's secure-code-review guidance, and the Conventional Comments specification.

The item changes only the review *contract* (`.github/claude-review-prompt.md`). Where the comment
format changes, it also changes the identification prefix of the workflow. It does not touch the
advisory, non-blocking guarantees that BE-0203 established. The same prime-directive-1 boundary binds
this item.

## Motivation

- **The review history of the repo already shows the gaps.**
  Copilot's inline comments on recent merged PRs repeat a few patterns.
  The current prompt names none of them explicitly:
  - **Comment/docstring drift ("comment rot").**
    - The `state.py` docstring of [#825](https://github.com/bajutsu-e2e/bajutsu/pull/825)
      undercounted the storage seams after a fourth one was added.
    - The `platform_lifecycle.py` docstring of [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816)
      called an already-referenced platform a "future" one and miscounted the run predicates.
    - The `record.py` docstring of [#813](https://github.com/bajutsu-e2e/bajutsu/pull/813)
      contradicted the actual return behavior of the code.
  - **`Protocol` methods with docstring-only bodies.** In [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816),
    several `RunEnvironment`/`CrawlEnvironment` methods had only a docstring for a body, with no
    `...` or `raise NotImplementedError`. Such a method is a concrete method that silently returns
    `None` despite a non-`None` return annotation. `mypy --strict` does not catch this.
  - **Unescaped string interpolation that builds structured data.** In
    [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811), three separate functions in
    `bajutsu/templates/serve.author.js` interpolated a raw selector id into a YAML flow mapping
    without escaping. An id containing `:` produced invalid YAML.
  - **Brittle and flaky tests.** Also in [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811), a
    test asserted an exact quote-style substring, which would fail on a harmless formatting change.
    In [#808](https://github.com/bajutsu-e2e/bajutsu/pull/808), the assertions of a test depended on
    wall-clock time and could flake under a slow CI run.
  - **Stale claims in PR/roadmap prose.** The roadmap file of [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811)
    still called its `Implementing PR` field "pending" after the PR already linked it.

  The current prompt has three lens categories: prime directives, `implement-be` lenses, and house
  conventions. None of them points a reviewer at these patterns by name. The reviewer catches them
  only if it happens to notice, not because the contract asked it to look.

- **A generic security checklist would mostly duplicate the gate.**
  Precision matters more than breadth.
  The instinct is to bolt on a broad Bandit/OWASP-style checklist.
  But `make lint` already selects the `S` (Bandit-equivalent) rule family of `ruff`
  ([`pyproject.toml`](../../pyproject.toml)).
  On the Python side, the gate already blocks these defects:
  hardcoded secrets, unsafe `yaml.load`, `subprocess(shell=True)`, weak randomness,
  missing request timeouts, and disabled certificate verification.
  - A prompt that re-flags these findings duplicates a check that already blocks the PR.
  - A prompt that flags `assert` usage or bare `subprocess` argv calls contradicts the `S101`/`S603`
    ignores of this repo. Those ignores carry a documented rationale in `pyproject.toml`.
  - The distinct value of the reviewer lies in what the gate structurally *cannot* see.
    One case is cross-file semantic drift.
    Another is a `Protocol` body that type-checks but is wrong.
    A third is injection-shaped bugs in `bajutsu/templates/serve.*.js`, per the `#811` example above.
    `make lint-js` runs `node --check` on those files for syntax alone.
    The repo has no security or escaping lint for them at all.

  The design of this item leads with that distinction rather than importing a generic checklist
  wholesale.
- **The prompt has no design/architecture lens.**
  The [reviewer guide of Google's Engineering
  Practices](https://google.github.io/eng-practices/review/reviewer/looking-for.html)
  puts *design* above functionality, complexity, tests, naming, comments, and style.
  Design covers three points.
  One is where the change belongs.
  Another is whether it over-engineers or under-engineers the problem.
  The third is whether it fits the surrounding architecture.
  The current prompt is entirely rule- and checklist-shaped (prime directives, lenses, conventions).
  Nothing in it asks the reviewer to weigh in on the shape of the change itself.
  Both Google's guidance and the requester of this item consider that dimension highest-value.
- **Comment severity is currently unlabeled prose.** Every finding today reads as undifferentiated
  text after the `🤖 **Claude Code** —` prefix. A human has to read each finding to learn whether it
  is a nitpick or a real issue. The [Conventional Comments](https://conventionalcomments.org/)
  specification is a community-adopted micro-format: `label [decorations]: subject`. Its labels
  include `issue`, `suggestion`, `nitpick`, `question`, and `praise`. Its decorations include
  `(non-blocking)`. The format gives findings a scannable severity signal for free. It also acts as a
  running, visible reminder that nothing the reviewer posts is a merge blocker. That reinforces prime
  directive 1 in the artifact itself, not only in the surrounding prose.
- **Prose/wording consistency has no named standard to check against.** The current "house
  conventions" section flags bilingual-docs gaps and the docstring standard. It says nothing about
  terminology drift (the same concept named two different ways across files). It also says nothing
  about a doc or comment that makes a claim the diff just falsified.
  That is exactly the comment-rot pattern above. [Google's developer documentation style guide](https://developers.google.com/style)
  and its [documentation best
  practices](https://google.github.io/styleguide/docguide/best_practices.html) name this class of
  defect directly. Both are citable, established references.

## Detailed design

The work stays entirely inside the existing review contract and its identification prefix. No
workflow permission, trigger, or gate semantics from BE-0203 change.

1. **A leading "review what the gate can't" principle.** Add a short paragraph near the top of
   `.github/claude-review-prompt.md`, before the lens sections. The paragraph states two rules.
   - The reviewer should not re-flag what `make check` already gates.
     That covers the selected ruff rules, including the `S`/Bandit family.
     It also covers `mypy --strict`, the docstring linter, and the coverage floor.
   - The reviewer must not re-litigate a documented, file-scoped ruff ignore (for example `S101`,
     `S603`, and the file-level ignores in `pyproject.toml`). The paragraph cites the rationale
     already recorded there.

   This is the organizing principle that the rest of the additions follow. Each new lens below
   targets something that the deterministic gate structurally cannot see.
2. **A design & architecture lens.**
   The new lens instructs the reviewer to comment on three points.
   One is whether the change belongs where it is placed.
   Another is whether it over- or under-engineers the problem it solves.
   The third is whether it fits the surrounding module boundaries and existing seams.
   This is the "design first" priority that [Google's reviewer
   guide](https://google.github.io/eng-practices/review/reviewer/looking-for.html) documents.
   The lens is framed as critique and suggestion, never as a verdict (prime directive 1).
3. **A comment/docstring-drift ("comment rot") lens.** Flag a docstring, comment, or prose claim in
   any of three cases. It contradicts the code it describes. It contradicts another part of the same
   file or PR. It states something as pending or future that the diff itself just resolved. This is
   the pattern behind `#825`, `#816`, `#813`, and `#811` above.
4. **A `Protocol`/abstract-method body lens.**
   Flag a `Protocol` or `abc` method whose body is only a docstring, when its return annotation is
   non-`None`.
   The body has no `...`, no `raise NotImplementedError`, and no real implementation. This bug class is invisible to `mypy --strict`. The `#816` review of this
   repo caught it by hand.
5. **An unescaped-interpolation lens, scoped to ungated surfaces.**
   Flag string concatenation or interpolation that builds structured text from a variable.
   The structured text is YAML, JSON, HTML, or shell, and the variable is not escaped. The lens carries an explicit callout. `bajutsu/templates/serve.*.js` is exactly this kind
   of ungated surface, because `make lint-js` checks it only with `node --check` (see the
   [`Makefile`](../../Makefile)). The `#811` precedent shows the risk.
6. **A test-quality lens: brittleness and flakiness.** Flag a test assertion pinned to incidental
   formatting (exact quoting or whitespace) where behavior is what matters. Flag a test whose
   pass/fail depends on wall-clock time or another non-deterministic input. The lens extends the
   "determinism first" ethos (prime directive 2) to the test suite itself, not only to `run`.
7. **Adopt Conventional Comments labeling.** Prefix every inline finding with a
   [Conventional Comments](https://conventionalcomments.org/) label (`issue`, `suggestion`,
   `nitpick`, `question`, or `praise`) plus the `(non-blocking)` decoration. The prefix goes ahead of
   the existing `🤖 **Claude Code** —` identification, for example
   `🤖 **Claude Code** — issue (non-blocking): ...`. The prompt documents that `(non-blocking)` is not
   optional decoration here. Every label that this reviewer posts carries it, because the review is
   advisory by design (prime directive 1). No label is ever blocking.
8. **A wording/terminology-consistency lens.** Extend the existing "house conventions" section to
   flag three things in the files touched by the diff:
   - the same concept named two different ways;
   - an acronym used unexpanded on first appearance;
   - a PR body or roadmap `Progress` claim that does not match the diff it describes.

   The lens is grounded in [Google's developer documentation style
   guide](https://developers.google.com/style) and [documentation best
   practices](https://google.github.io/styleguide/docguide/best_practices.html). It is also grounded
   in the stale `Implementing PR` field that the review of `#811` caught.
9. **Verification.** The shape is the same as item 9 of BE-0203. `actionlint` (already in `make
   check`) validates that no workflow YAML changes are needed.
   This item touches only the prompt markdown. The review *behavior* needs a live PR, so verification is manual. Open a test PR
   that reproduces one instance of each new lens:
   - a stale docstring claim;
   - a docstring-only `Protocol` method;
   - an unescaped YAML interpolation in a `serve.*.js` template;
   - a wall-clock-dependent test assertion.

   Then confirm two things. The inline comments of the reviewer catch each instance with the new
   Conventional Comments label. The summary and every inline comment stay non-blocking.

## Alternatives considered

- **Import a generic Bandit/OWASP security checklist wholesale.** Rejected. The `ruff` configuration
  of this repo already selects the `S` (Bandit-equivalent) rules as part of the gate, so most of that
  checklist would duplicate an existing blocking check. The parts that would not duplicate it
  (`S101`/`S603`) are deliberately ignored with a documented rationale. Re-flagging them would
  contradict the recorded decision of the repo. The "review what the gate can't" principle of item 1
  replaces a checklist import with a scoping rule.
- **Make findings severity-weighted and treat `issue`-labeled comments as blocking.** Rejected
  outright. It would put an LLM judgment on the merge path, which violates prime directive 1 the same
  way that a required status check would. BE-0203 already ruled out that check. Every Conventional
  Comments label that this item adds carries `(non-blocking)`. Labeling serves human scannability,
  not gating.
- **Leave comments as free-form prose, without Conventional Comments labels.** Considered as the lower-effort option.
  Rejected. An unlabeled finding forces a human to read the whole comment to
  learn its severity. The `(non-blocking)` decoration of the labeled format also reinforces the advisory guarantee.
  The reinforcement is visible, and it is a second one. The addition costs little, because the format is a citable,
  pre-existing standard. It is not a bespoke vocabulary that this item would need to invent and
  document from scratch.
- **Fold this into BE-0203 instead of a new item.** Considered. Every prior change to
  `.github/claude-review-prompt.md` (workflow hardening, mechanism fixes, wording corrections) landed
  as a `(BE-0203)`-tagged commit against the own `Progress` log of that item, even after its `Status`
  became `Implemented`. This item is scoped differently. It is a substantial, externally researched
  policy rewrite of the review lenses themselves, not an operational fix to the workflow that BE-0203
  shipped. It is therefore proposed as its own item, cross-linked via `Related` rather than folded
  into the log of BE-0203.

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
