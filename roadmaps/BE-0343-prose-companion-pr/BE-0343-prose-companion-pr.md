**English** · [日本語](BE-0343-prose-companion-pr-ja.md)

# BE-0343 — Add a companion-PR workflow so Claude review's wording-only findings never need a code PR's CI cycle

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0343](BE-0343-prose-companion-pr.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0343") |
| Implementing PR | [#1498](https://github.com/bajutsu-e2e/bajutsu/pull/1498) |
| Topic | Contributor workflow |
| Related | [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md), [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a **companion-PR** mechanism to the advisory reviewer
([BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md)). When that reviewer
posts a wording-only finding — Japanese prose quality, or its English `docs/*.md`/roadmap-prose
counterpart, which this item also adds as a lens — on a pull request that also touches non-prose
files, a new workflow mechanically applies the finding's own suggestion to a companion branch based
on the pull request's current head, and opens or updates a companion pull request targeting that
branch. The source pull request's branch and CI cycle stay untouched until a human reviews and
merges the small companion pull request; the wording fix then lands as an ordinary push to the
source branch, reviewed on its own cheap terms, and it is available while the source pull request is
still open rather than only after it merges. The mechanism calls no LLM of its own: BE-0203 already
drafted the fix at review time, so this item only automates applying and shipping it.

## Motivation

BE-0203 re-reads a pull request's entire diff on every push and posts inline findings; one lens is
Japanese prose quality. English `docs/*.md` and roadmap prose deserve the same wording-quality bar
under the bilingual-docs house convention, but the reviewer has no equivalent lens for it today —
this item adds one, alongside the Japanese lens it already has. Both classes of finding are pure
wording suggestions, never a correctness bug — yet fixing one today means pushing a commit to that
same pull request, and the push re-triggers the full CI matrix (`ci.yml`, plus whichever end-to-end
lane the changed files select) for a change with no behavioral risk. Because the reviewer re-reads
the whole diff on every push, a wording finding can surface partway through a pull request's life
rather than all at once, so a contributor pays this cost more than once per pull request.

The repository already treats prose quality as a review-time norm, not a gate, for the same reason
it treats `DESIGN.md` alignment that way: judging wording needs semantic judgment, and putting that
judgment on the gate would put an LLM on the `run`/CI verdict path (prime directive 1). textlint
enforces the `document-writing` skill's rules, but running it to zero findings against the existing
roadmap corpus is not required, and
[BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md) keeps design-doc
alignment out of `make check` on the same grounds. A wording finding on a pull request is therefore
already advisory; nothing requires fixing it before merge.

That advisory status is exactly why the fix should not wait until merge. A wording finding left unfixed
for the rest of a pull request's review sits uncorrected in the very diff a human is reading and
about to approve — the reviewer never actually sees the corrected wording before the pull request
merges. Deferring the fix to *after* merge, the way [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md)'s
scheduled refreshers reconcile other kinds of drift, trades the CI-cost problem for a visibility
problem instead of solving the original one. What is needed is a way to apply the fix immediately,
while the source pull request is still open, without routing it through that pull request's own
branch and CI cycle. A companion pull request, reviewable on its own small and cheap terms, does
exactly that.

## Detailed design

### Marking a wording-only finding on the advisory reviewer

[`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) names a Japanese-prose-
quality lens today but no English counterpart — only the separate bilingual-docs (sync) and
wording/terminology-consistency (naming drift) lenses, neither of which judges English wording
quality. This item adds that missing lens: a new house-convention bullet instructing the reviewer to
hold English `docs/*.md` and roadmap prose to the `document-writing`/`english-document-writing`
skills, the same way the existing bullet already holds Japanese prose to
`japanese-document-writing`. The reviewer already decorates every inline finding with a
`(non-blocking)` label (BE-0203); this item adds one more decoration, for example
`(non-blocking, prose)`, to a finding from either prose-quality lens — and only from those two
lenses, never to a design, security, or correctness `suggestion` on code. The marker is what lets
the companion-PR job (below) recognize a wording-only finding without judging its content.

### A companion PR, applied mechanically at review time

A new job — either a dedicated workflow or an additional job in `claude-review.yml` itself, ordered
with `needs: review` so it only runs after that push's findings have posted — reads the pull
request's current inline comments through the same `gh api repos/{owner}/{repo}/pulls/{pr}/comments`
call `claude-review.yml` already uses for its own dedup, and filters to the ones decorated
`(non-blocking, prose)` that carry a GitHub `suggestion` block. Finding none, it is a no-op. Finding
at least one, it:

1. Reads the source pull request's current head, under the same automation App token
   `roadmap-id.yml` and `refresh.yml` already use to push and open a pull request that triggers its
   own `check` CI (a plain `GITHUB_TOKEN` push does not). The job reads the head as data at a
   resolved commit and never checks it out — the Log below records why it holds no working tree.
   This job
   carries the same trust boundary `claude-review.yml`'s own review step already draws: it runs only
   for a `pull_request` event from a same-repo `claude/<topic>`/`<user>/<topic>` branch, never a
   fork, since pushing to an arbitrary branch under a privileged App token is a materially different
   risk from `roadmap-id.yml`'s and `refresh.yml`'s own use of that token, which only ever touches
   the already-trusted `main`. A fork pull request's wording findings stay unautomated, the same
   on-demand-only gap the existing review already accepts for forks.
2. Rebuilds the companion branch **fresh from the source pull request's current head** every run,
   rather than incrementally amending a prior version of it, and reapplies every currently open
   `(non-blocking, prose)` finding's `suggestion` block as a mechanical patch at its file and line —
   not only the ones newly posted since the last run. Rebuilding fresh each time, rather than patching
   forward, is what absorbs a rebase on the source branch: `CLAUDE.md`'s own "Rebase, don't drift"
   convention has contributors routinely rewrite their branch's history before merge, and a
   companion branch built forward from a now-superseded commit would silently lose ancestry with it.
   Starting fresh from the current head every run sidesteps that; there is nothing to re-sync,
   because nothing carries over. A suggestion whose target line no longer matches — the source pull
   request edited that line since the finding posted — is skipped with a note in the companion pull
   request's body, never guess-applied, the same conservatism this repository already holds in
   `docs-refresh-prompt.md` and elsewhere.
3. Force-pushes the rebuilt companion branch, named deterministically from the source pull request's
   number (for example `prose-fix/pr-<N>`), guarded like [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md)'s
   rolling branches: it force-updates only over the bot's own prior commit on that branch, never over
   a human's own edit to the companion branch itself.
4. Opens the companion pull request if none is open yet, or leaves the existing one in place if the
   force-push only updated it, then replies to each applied finding's thread on the source pull
   request naming the companion pull request's number, and resolves that thread — mechanically,
   since no judgment is needed once the finding's own suggestion has decided the fix.

### Basing the companion PR on the source branch, not `main`

The companion pull request's base is the **source pull request's own branch**, not `main`: a fix to
text the source pull request itself introduces — a new file, a new paragraph — does not exist on
`main` yet, so a `main`-based companion pull request could not carry it until the source pull
request merges. Basing it on the source branch is what lets the fix ship while the source pull
request is still open.

This repository already has **delete head branches on merge** enabled (confirmed via
`gh api repos/bajutsu-e2e/bajutsu --jq '.delete_branch_on_merge'`, which returns `true`), and GitHub
retargets any open pull request whose base branch is deleted onto that branch's own base. Once the
source pull request merges and its branch is deleted, the still-open companion pull request is
therefore retargeted to `main` automatically, with no action from this workflow — merging it after
that point is an ordinary, independent merge to `main`.

### Prime-directive compliance

This mechanism calls no LLM of its own. BE-0203 already drafted the wording fix, as a `suggestion`
block, at review time; this item's job only applies that exact text when it still matches, and skips
it, never guessing, when it does not. Reading the posted comments, writing a branch, and opening a
pull request are not judgment calls, so this stays further from the `run`/CI verdict path than either
BE-0203's own review (which does call an LLM, to find the finding in the first place) or
[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md)'s AI-authored
refreshers. No LLM call is added to `run` or to any required status check; a human remains the sole
reviewer and merger of both the source pull request and the companion pull request.

### Documenting the mechanism

Nothing here needs the reply-and-resolve exception `pr-followup` carries for other findings: with
the fix already applied and the thread already resolved automatically, a contributor answering
review comments never needs to know a companion pull request exists — until one shows up in their
pull request's timeline, unexplained. `docs/ai-development.md`'s "Responding to PR review comments"
section (and its `docs/ja/ai-development.md` mirror) gains a short paragraph next to the automated
reviewer's own description, naming the companion-PR mechanism and what a contributor should do with
one (review and merge it like any other small pull request, on its own schedule). A `CLAUDE.md`
bullet points at that paragraph, the same way it already points at the automated reviewer's own
entry.

## Alternatives considered

- **Reuse [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md)'s scheduled
  refresh design, deferring the fix to after the source pull request merges.** This item's original
  shape: a third `refresh.yml` caller that rereads `docs/**` and roadmap prose on a daily cron and
  opens one rolling Draft PR against `main`. Rejected: waiting until the source pull request merges
  means the wording issue sits uncorrected for the rest of that pull request's review, and the human
  reviewing it never sees the corrected text before approving — the opposite of the immediacy this
  item exists to provide. It would also re-derive each finding independently on a fresh pass rather
  than applying the exact suggestion already drafted, so a specific fix is only likely, not
  guaranteed, to land. If the companion pull request's own CI cost or its cross-branch trust surface
  ever proves worse in practice than this trade-off, revisit this alternative.
- **Base the companion PR on `main` instead of the source branch.** Rejected: a fix to text the
  source pull request itself introduces does not exist on `main` until that pull request merges, so
  a `main`-based companion pull request could not carry such a fix at all while the source pull
  request is still open — it collapses into the post-merge alternative above.
- **An on-demand trigger only (a `@claude prose-pr` pull-request comment).** Rejected: it
  reintroduces the manual step this item exists to remove — a human would still have to remember to
  ask for the companion pull request.
- **Leave wording-only findings unmarked and keep fixing them inline.** The status quo, and the pain
  point motivating this item: every wording fix still costs a full CI cycle on the pull request that
  raised it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] The `prose-companion` job — reads posted `(non-blocking, prose)` findings, applies each
  suggestion mechanically, force-pushes the companion branch, and opens or updates the companion
  pull request based on the source branch
- [x] `.github/claude-review-prompt.md` — the new English-prose-quality lens (mirroring the
  existing Japanese one) and the `(non-blocking, prose)` decoration on both
- [x] Auto-reply and auto-resolve on the source pull request's finding thread, naming the companion
  pull request
- [x] `docs/ai-development.md` and its `docs/ja/ai-development.md` mirror — documenting the
  companion-PR mechanism under "Responding to PR review comments"
- [x] A short `CLAUDE.md` bullet pointing at the documented mechanism

### Log

- Shipped whole. The decision logic lives in `scripts/prose_companion_pr.py` rather than inline YAML,
  so it is unit-tested (`tests/test_prose_companion_pr.py`): which findings qualify, what each one
  expects its lines to read, and whether that still matches the head. The job is a second job in
  [`claude-review.yml`](../../.github/workflows/claude-review.yml) with `needs: review`, and it runs
  the script from a **default-branch** checkout — so a same-repo branch cannot rewrite what executes
  under the automation App token. The script is stdlib-only and runs on the runner's `python3`, which
  keeps a pull-request-authored `pyproject.toml` out of the privileged job entirely.
- Dropped the second checkout, of the pull request's head, that the job originally edited in place.
  CodeQL's `actions/untrusted-checkout-toctou` query flagged it. The job is reachable from
  `issue_comment`, so it runs with repository secrets, and checking out contributor-authored code
  beside the automation App token is the pattern that query exists to catch. The gate admitting the
  run judges the *comment*; it pins nothing about the code the run then materializes. The head now
  arrives as data. The script reads each file a finding names from GitHub's contents endpoint at the
  resolved head commit, then builds the companion commit through GitHub's Git Data endpoints — blob,
  tree, commit, and a forced update of the branch reference. Two consequences are worth recording.
  Pinning every read to the resolved commit rather than the branch stops a push landing mid-run from
  shifting the text a finding matches against, which the working-tree version left open. Against
  that, those endpoints offer no compare-and-swap to stand in for `git push --force-with-lease`, so
  the clobber guard now reads the companion branch's tip one round trip before overwriting it, where
  the lease left no window at all. The guard itself is unchanged: a human-committed tip still stops
  the run.

## References

[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md) (the advisory reviewer
whose findings this item applies); [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md)
(the rolling-branch clobber guard this item's companion branch reuses, and the scheduled-refresh
alternative this item rejects); [`roadmap-id.yml`](../../.github/workflows/roadmap-id.yml) and
[`refresh.yml`](../../.github/workflows/refresh.yml) (the automation-App push-and-open-a-pull-request
pattern this item's job reuses); [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)
(the review-time, not-a-gate precedent for prose judgment this item's motivation draws on); the
`document-writing`, `english-document-writing`, and `japanese-document-writing` skills (the
wording-quality norms the new English lens and the existing Japanese lens apply).
