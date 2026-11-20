**English** · [日本語](BE-0343-prose-companion-pr-ja.md)

# BE-0343 — Add a companion-PR workflow so Claude review's wording-only findings never need a code PR's CI cycle

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0343](BE-0343-prose-companion-pr.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0343") |
| Implementing PR | [#1498](https://github.com/bajutsu-e2e/bajutsu/pull/1498), [#1565](https://github.com/bajutsu-e2e/bajutsu/pull/1565) |
| Topic | Contributor workflow |
| Related | [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md), [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md) |
| Superseded by | [BE-0419](../BE-0419-retire-prose-companion-pr/BE-0419-retire-prose-companion-pr.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a **companion-PR** mechanism to the advisory reviewer
([BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md)). The mechanism fires
when the reviewer posts a wording-only finding on a pull request that also touches non-prose files.
A wording-only finding is a Japanese prose-quality finding, or a finding from the English
counterpart that covers `docs/*.md` and roadmap prose. This item also adds that English lens.

A new workflow then applies the finding's own suggestion mechanically to a companion branch, based
on the pull request's current head. It opens or updates a companion pull request that targets that
branch.

The source pull request's branch and CI cycle stay untouched until a human reviews and merges the
small companion pull request. The wording fix then lands as an ordinary push to the source branch,
reviewed on its own cheap terms. It is available while the source pull request is still open, not
after it merges.

The mechanism calls no LLM of its own. BE-0203 already drafted the fix at review time, so this item
automates applying and shipping it.

## Motivation

BE-0203 re-reads the entire diff of a pull request on every push and posts inline findings. One lens
covers Japanese prose quality. English `docs/*.md` and roadmap prose deserve the same
wording-quality bar under the bilingual-docs house convention, but the reviewer has no equivalent
lens for them today. This item adds one, alongside the Japanese lens.

Both classes of finding are pure wording suggestions, never a correctness bug. Fixing one today
still means pushing a commit to the same pull request. The push re-triggers the full CI matrix
(`ci.yml`, plus whichever end-to-end lane the changed files select) for a change with no behavioral
risk. The reviewer re-reads the whole diff on every push, so a wording finding can surface partway
through the life of a pull request, not all at once. A contributor therefore pays this cost more
than once per pull request.

The repository already treats prose quality as a review-time norm, not a gate. It treats `DESIGN.md`
alignment the same way, for the same reason. Judging wording needs semantic judgment, and putting
that judgment on the gate would put an LLM on the `run`/CI verdict path (prime directive 1).

textlint enforces the rules of the `document-writing` skill, but running it to zero findings against
the existing roadmap corpus is not required.
[BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md) keeps design-doc
alignment out of `make check` on the same grounds. A wording finding on a pull request is therefore
already advisory. Nothing requires fixing it before merge.

That advisory status is why the fix should not wait until merge. An unfixed wording finding sits
uncorrected for the rest of the review, in the diff that a human is reading and about to approve.
The reviewer never sees the corrected wording before the pull request merges.

Deferring the fix to *after* merge trades the CI-cost problem for a visibility problem instead of
solving the original one.
[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md)'s scheduled
refreshers take that route to reconcile other kinds of drift.

What is needed is a way to apply the fix immediately, while the source pull request is still open.
The fix must not route through that pull request's own branch and CI cycle. A companion pull
request, reviewable on its own small and cheap terms, does exactly that.

## Detailed design

### Marking a wording-only finding on the advisory reviewer

[`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) names a Japanese
prose-quality lens today. It names no English counterpart. It has two other lenses: bilingual-docs
(sync) and wording/terminology-consistency (naming drift). Neither judges English wording quality.

This item adds the missing lens, as a new house-convention bullet. The bullet instructs the reviewer
to hold English `docs/*.md` and roadmap prose to the `document-writing`/`english-document-writing`
skills. The existing bullet already holds Japanese prose to `japanese-document-writing` in the same
way.

The reviewer already decorates every inline finding with a `(non-blocking)` label (BE-0203). This
item adds one more decoration, for example `(non-blocking, prose)`. A finding from either
prose-quality lens carries it. A design, security, or correctness `suggestion` on code never does.
The marker lets the companion-PR job (below) recognize a wording-only finding without judging its
content.

### A companion PR, applied mechanically at review time

A new job reads the inline comments of the pull request. The job is either a dedicated workflow or
an additional job in `claude-review.yml` itself. It is ordered with `needs: review`, so it runs only
after the findings of that push have posted.

The job reads the comments through the same `gh api repos/{owner}/{repo}/pulls/{pr}/comments` call
that `claude-review.yml` already uses for its own dedup. It filters to the comments decorated
`(non-blocking, prose)` that carry a GitHub `suggestion` block. If it finds none, it is a no-op. If
it finds at least one, it performs four steps:

1. Reads the source pull request's current head. The job uses the same automation App token that
   `roadmap-id.yml` and `refresh.yml` already use. That token pushes and opens a pull request that
   triggers its own `check` CI (a plain `GITHUB_TOKEN` push does not). The job reads the head as
   data at a resolved commit and never checks it out. The Log below records why the job holds no
   working tree.

   The job carries the trust boundary that the review step of `claude-review.yml` already draws. It
   runs only for a `pull_request` event from a same-repo `claude/<topic>`/`<user>/<topic>` branch,
   never a fork. Pushing to an arbitrary branch under a privileged App token is a materially
   different risk from the use of that token by `roadmap-id.yml` and `refresh.yml`, which only ever
   touch the already-trusted `main`. The wording findings of a fork pull request stay unautomated.
   The existing review already accepts the same on-demand-only gap for forks.
2. Rebuilds the companion branch **fresh from the source pull request's current head** on every run.
   It does not amend a prior version of the branch incrementally. It reapplies the `suggestion`
   block of every currently open `(non-blocking, prose)` finding as a mechanical patch at its file
   and line, not only the findings posted since the last run.

   Rebuilding fresh absorbs a rebase on the source branch. The "Rebase, don't drift" convention of
   `CLAUDE.md` has contributors routinely rewrite their branch's history before merge. A companion
   branch built forward from a superseded commit would silently lose ancestry with it. Starting
   fresh from the current head sidesteps that: nothing carries over, so nothing needs to re-sync.

   A suggestion whose target line no longer matches — because the source pull request edited that
   line after the finding posted — is skipped, with a note in the body of the companion pull
   request. The job never guess-applies a suggestion. This is the same conservatism that the repository already
   holds in `docs-refresh-prompt.md` and elsewhere.
3. Force-pushes the rebuilt companion branch. The branch is named deterministically from the number
   of the source pull request (for example `prose-fix/pr-<N>`). The push is guarded like the rolling
   branches of [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md). It
   force-updates only over the bot's own prior commit on that branch, never over a human's own edit
   to the companion branch.
4. Opens the companion pull request if none is open yet. If the force-push merely updated an
   existing one, the job leaves it in place. The job then replies to the thread of each applied
   finding on the source pull request, naming the number of the companion pull request, and resolves
   that thread. Both actions are mechanical, because the suggestion of the finding has already
   decided the fix.

### Basing the companion PR on the source branch, not `main`

The base of the companion pull request is the **source pull request's own branch**, not `main`. A
fix to text that the source pull request introduces, such as a new file or a new paragraph, does not
exist on `main` yet. A `main`-based companion pull request could not carry such a fix until the
source pull request merges. Basing the companion pull request on the source branch lets the fix ship
while the source pull request is still open.

This repository already has **delete head branches on merge** enabled. `gh api
repos/bajutsu-e2e/bajutsu --jq '.delete_branch_on_merge'` confirms it by returning `true`. GitHub
retargets any open pull request whose base branch is deleted onto the base of that branch. Once the
source pull request merges and its branch is deleted, GitHub retargets the still-open companion pull
request to `main` automatically. The workflow takes no action. Merging the companion pull request
after that point is an ordinary, independent merge to `main`.

### Prime-directive compliance

This mechanism calls no LLM of its own. BE-0203 already drafted the wording fix, as a `suggestion`
block, at review time. This item's job applies that exact text when it still matches. When it does
not match, the job skips the text and never guesses. Reading the posted comments, writing a branch,
and opening a pull request are not judgment calls.

The mechanism therefore stays further from the `run`/CI verdict path than BE-0203's own review,
which calls an LLM to find the finding in the first place. It also stays further than the
AI-authored refreshers of
[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md). No LLM call is added
to `run` or to any required status check. A human remains the sole reviewer and merger of both the
source pull request and the companion pull request.

### Documenting the mechanism

Nothing here needs the reply-and-resolve exception that `pr-followup` carries for other findings.
The fix is already applied and the thread is already resolved automatically. A contributor who
answers review comments never needs to know that a companion pull request exists, until one shows up
unexplained in the timeline of their pull request.

The "Responding to PR review comments" section of `docs/ai-development.md` (and its
`docs/ja/ai-development.md` mirror) gains a short paragraph next to the description of the automated
reviewer. The paragraph names the companion-PR mechanism and says what a contributor should do with
one: review and merge it like any other small pull request, on its own schedule. A `CLAUDE.md`
bullet points at that paragraph, the same way it already points at the entry of the automated
reviewer.

## Alternatives considered

- **Reuse [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md)'s scheduled
  refresh design, deferring the fix to after the source pull request merges.** This was the original
  shape of this item. A third `refresh.yml` caller rereads `docs/**` and roadmap prose on a daily
  cron and opens one rolling Draft PR against `main`. Rejected, for two reasons:
  - Waiting until the source pull request merges leaves the wording issue uncorrected for the rest
    of the review. The human reviewer never sees the corrected text before approving. That is the
    opposite of the immediacy this item exists to provide.
  - The scheduled pass would re-derive each finding independently, rather than apply the exact
    suggestion already drafted. A specific fix would then be likely to land, but not guaranteed.

  If the CI cost of the companion pull request, or its cross-branch trust surface, ever proves worse
  in practice than this trade-off, revisit this alternative.
- **Base the companion PR on `main` instead of the source branch.** Rejected. A fix to text that the
  source pull request introduces does not exist on `main` until that pull request merges. A
  `main`-based companion pull request could not carry such a fix at all while the source pull
  request is still open. It collapses into the post-merge alternative above.
- **An on-demand trigger only (a `@claude prose-pr` pull-request comment).** Rejected. It
  reintroduces the manual step that this item exists to remove. A human would still have to remember
  to ask for the companion pull request.
- **Leave wording-only findings unmarked and keep fixing them inline.** This is the status quo, and
  the pain point that motivates this item. Every wording fix still costs a full CI cycle on the pull
  request that raised it.

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
- [x] Superseded by [BE-0419](../BE-0419-retire-prose-companion-pr/BE-0419-retire-prose-companion-pr.md):
  the companion-PR job, its script, and the `(non-blocking, prose)` marker are retired. A wording-only
  finding is fixed like any other, with a normal push to the source pull request.

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
