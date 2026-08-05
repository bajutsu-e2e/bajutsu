**English** · [日本語](BE-XXXX-prose-refresh-ja.md)

# BE-XXXX — Add a scheduled prose-refresh workflow so Claude review's wording-only findings never need a code PR's CI cycle

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-prose-refresh.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Contributor workflow |
| Related | [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md), [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds **prose-refresh**, a third scheduled workflow beside `roadmap-refresh` and
`docs-refresh` ([BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md)),
that reconciles the wording quality of `docs/**` and roadmap prose, in both languages, against the
`document-writing` / `english-document-writing` / `japanese-document-writing` skills' norms, and
opens its own rolling Draft PR when it finds something worth fixing. Alongside it, the advisory
reviewer ([BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md)) marks a
wording-only finding distinctly, and `pr-followup` defers that finding — a reply and a resolved
thread, no fix commit — on any pull request that also touches non-prose files, trusting
prose-refresh to catch it once the pull request merges.

## Motivation

BE-0203 re-reads a pull request's entire diff on every push and posts inline findings; one lens is
Japanese prose quality. English `docs/*.md` and roadmap prose deserve the same wording-quality bar
under the bilingual-docs house convention, but the reviewer has no equivalent lens for it today —
this item adds one, alongside the Japanese lens it already has. Both classes of finding are pure
wording suggestions, never a correctness bug — yet fixing one today means pushing a commit to that
same pull request, and the
push re-triggers the full CI matrix (`ci.yml`, plus whichever end-to-end lane the changed files
select) for a change with no behavioral risk. Because the reviewer re-reads the whole diff on every
push, a wording finding can surface partway through a pull request's life rather than all at once,
so a contributor pays this cost more than once per pull request.

The repository already treats prose quality as a review-time norm, not a gate, for the same reason
it treats `DESIGN.md` alignment that way: judging wording needs semantic judgment, and putting that
judgment on the gate would put an LLM on the `run`/CI verdict path (prime directive 1). textlint
enforces the `document-writing` skill's rules, but running it to zero findings against the existing
roadmap corpus is not required, and
[BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md) keeps design-doc
alignment out of `make check` on the same grounds. A wording finding on a pull request is therefore
already advisory; nothing requires fixing it before merge. What is missing is a place for a
deferred finding to land — today, deferring one loses it outright, since no later mechanism revisits
it.

[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md) already solves the
adjacent problem — content that "requires semantic judgment to keep current" and "therefore rots
silently" — for two other kinds of drift: a BE item's `Status`/`Progress` against what merged, and
`docs/`/`DESIGN.md` prose against shipped behavior. Wording quality is a third kind of drift with
the same shape: it needs a human-equivalent judgment call that no deterministic gate can check, and
it rots the same way once a contributor moves on to the next task. Reusing BE-0222's
scheduled-refresh design — author on a schedule, gate with `make check`, open one rolling Draft PR,
never merge without a human — closes this gap the way BE-0222 already closes its own, rather than
inventing a new mechanism for a problem BE-0222's shape already fits.

## Detailed design

### `prose-refresh`, a third thin caller of the shared `refresh.yml`

A new workflow, `.github/workflows/prose-refresh.yml`, calls the reusable
[`refresh.yml`](../../.github/workflows/refresh.yml) exactly as `roadmap-refresh.yml` and
`docs-refresh.yml` already do: the same two-credential dormancy gate (an AI provider plus the
`AUTOMATION_BOT_APP_ID`/`AUTOMATION_BOT_PRIVATE_KEY` automation App), the same App-token checkout of
`main`, the same AI-authoring step bounded by a path allowlist, the same in-job `make check`, and
the same rolling, idempotent, clobber-guarded, always-Draft PR. Nothing in `refresh.yml` itself
changes; `prose-refresh.yml` only supplies its own `label`, `branch`
(`chore/prose-refresh`), `contract`, `title`, `allow`, and `allowed_tools` inputs, the same shape
`docs-refresh.yml` already demonstrates.

Its contract, `.github/prose-refresh-prompt.md`, instructs the AI author to reread `docs/**`
(English and the `docs/ja/` mirror) and every roadmap item's prose body — never its `<!--
BE-METADATA -->` block, its H1, or its `Progress` checklist, which stay `roadmap-refresh`'s
territory — against the `document-writing`, `english-document-writing`, and
`japanese-document-writing` skills, and to fix a concrete violation of one of those norms directly
in the file. It carries the same **conservatism rule** `docs-refresh-prompt.md` already states:
propose a change only where a norm is concretely violated, and leave prose unchanged rather than
rewrite on a guess. Its path allowlist is `docs/**`, the top-level `DESIGN.md`, and
`roadmaps/**/*.md` / `roadmaps/**/*-ja.md` — the union of `docs-refresh`'s tree (which already
includes `DESIGN.md`) and `roadmap-refresh`'s tree, scoped to prose bodies only — and, like
`docs-refresh-prompt.md`, it excludes the top-level `README*` and `CLAUDE.md`: those state the
prime directives the AI author is itself bound by, so they stay human-authored.

### Why a third workflow rather than extending the two existing contracts

[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md) already gives the
reason for splitting a workflow by kind of drift, not only by which tree it touches: bundling two
distinct sorts of review into one PR forces a reviewer to judge a fast, mechanical change and a
slow, careful one together, and couples their failure modes and cadences. Wording-quality review is
that slow, careful kind of judgment, and it spans both existing trees — `docs/**` and
`roadmaps/**` — rather than fitting inside either single-tree contract. Folding it into
`docs-refresh-prompt.md` would still leave roadmap prose uncovered; folding it into
`roadmap-refresh-prompt.md` would dilute that contract's current near-mechanical
`Status`/`Progress` reconciliation with a heavier prose-judgment task on every run. A third,
narrowly scoped contract keeps every one of the three refreshes reviewable on its own terms, the
same rationale [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md)
already applied to split roadmap-refresh from docs-refresh.

### Coordinating with the two existing refreshers on overlapping trees

`prose-refresh`'s allowlist genuinely overlaps both existing refreshers' trees — `docs/**` with
`docs-refresh`, and roadmap prose bodies with `roadmap-refresh` — which is new: `roadmap-refresh.yml`
and `docs-refresh.yml` already avoid contending for a runner by giving their daily crons offset
times (17:07 vs 17:37 UTC), and `prose-refresh.yml` takes a third offset time in the same spirit.
That avoids a *runner* collision, but not a *content* one: `docs-refresh` can rewrite a paragraph
for behavior drift the same day `prose-refresh` rewrites it for wording, and each opens its own
rolling Draft PR from its own branch. When both touch the same lines, the human reviewing the two
Draft PRs merges one first and the other's Draft PR then needs an ordinary rebase to pick up the
merged change — the same kind of conflict a human editing that paragraph while a refresh PR is
already open would create today, not a new failure mode this item introduces. Accepting that
occasional rebase is the trade-off for keeping each refresh's contract narrowly scoped and
independently reviewable (the property the previous section argues for); it never risks a wrong
merge, since a human reviews and merges each Draft PR individually.

### Marking a wording-only finding on the advisory reviewer

[`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) names a Japanese-prose-
quality lens today but no English counterpart — only the separate bilingual-docs (sync) and
wording/terminology-consistency (naming drift) lenses, neither of which judges English wording
quality. This item adds that missing lens: a new house-convention bullet instructing the reviewer to
hold English `docs/*.md` and roadmap prose to the `document-writing`/`english-document-writing`
skills, the same way the existing bullet already holds Japanese prose to
`japanese-document-writing`. [`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md)
already decorates every inline finding with a `(non-blocking)` label (BE-0203); this item adds one
more decoration, for example `(non-blocking, prose)`, to a finding from either prose-quality lens — and
only from those two lenses, never to a design, security, or correctness `suggestion` on code. The
marker is what lets `pr-followup` recognize a wording-only finding without re-reading its full
body.

### Deferring a marked finding in `pr-followup`

[`.agent-workflows/pr-followup/workflow.md`](../../.agent-workflows/pr-followup/workflow.md) step 3
gains one exception to its reply-and-resolve loop: on a pull request that also touches a file
outside `docs/**`/roadmap prose, a `prose`-marked finding is replied to and its thread resolved
without a fix commit, noting that the next `prose-refresh` run will catch it once this pull request
merges. A pull request that is itself docs-only already merges through the lightweight
Ready-for-review path `CLAUDE.md` defines, so its CI cost is already low; a `prose`-marked finding
there is still fixed inline, as today. The reply changes from "fixed in commit X" to "deferred to
the next scheduled prose-refresh" — the human obligation to answer the finding does not disappear,
only the obligation to fix it in this specific pull request.

Deferring hands off to `prose-refresh` rediscovering the same violation on its own, not to a record
of the specific finding — `prose-refresh` re-derives its findings independently each run, the same
way `docs-refresh` does today, rather than replaying a stored list. That record-keeping is exactly
the added state the live companion PR discussed under "Alternatives considered" below would need,
and is one reason this item rejects that alternative. This is a deliberate bet, not a gap: a
`prose`-marked finding was, by construction, a concrete violation of the same
`document-writing`/`english-document-writing`/`japanese-document-writing` norms `prose-refresh`
itself applies, on text that does not change between the pull request's merge and `prose-refresh`'s
next run — so the same judgment applied to the same input is likely to reach the same conclusion.
The conservatism rule ("leave prose unchanged rather than rewrite on a guess") guards against
*inventing* a finding, not against missing a genuine one, so it should not suppress a re-detection.
The bet can still miss — an AI-authored refresh is not deterministic — and if a specific deferred
wording issue matters enough that it must not be missed, fixing it inline in the original pull
request remains available; the deferral is only ever offered for a finding that was already
advisory and non-blocking.

### Prime-directive compliance

The LLM is used purely on the *authoring* path, exactly as [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md)
already established: it drafts an edit into the working tree, and a human must review and merge the
resulting Draft PR. No LLM call is added to `run` or to any required status check — the
deterministic `check` remains the sole merge arbiter, and a human is the sole judge of the drafted
wording. The path allowlist keeps `prose-refresh` out of `bajutsu/`, `BajutsuKit/`, tests, config,
and demos, so it cannot affect determinism or the app-agnostic core.

## Alternatives considered

- **A live companion PR that tracks the source pull request via force-push.** Instead of a
  scheduled, post-merge refresh, a job triggered on the same `pull_request` events as
  `claude-review.yml` could rebuild a companion branch from the source pull request's current head
  on every push, applying each wording finding's suggestion and force-pushing the result. It would
  stay in sync with the source pull request in near real time, rather than waiting for it to merge,
  but it is rejected as this item's primary design for three reasons. It needs a new credentialed
  job that pushes onto a branch derived from a contributor's own branch, a larger trust surface than
  today's automation App, which only pushes to `main` or its own rolling branches. It needs a
  rebuild-and-force-push cycle on every source push. And it needs per-finding staleness detection for
  the case where the source pull request edits the very lines a finding targeted.
  `prose-refresh`'s post-merge design has none of these failure modes, at the cost of a fix landing
  after the source pull request merges rather than while it is still open. If that lag proves too
  long in practice, this alternative is the natural next escalation.
- **Extend `roadmap-refresh-prompt.md` and `docs-refresh-prompt.md` with a wording-quality lens
  instead of a third workflow.** Rejected for the reason given above under "Why a third workflow":
  wording review is a different, heavier kind of judgment than either contract's current mechanical
  or behavior-drift reconciliation, and it spans both contracts' trees rather than fitting either
  one.
- **An on-demand trigger only (a `@claude prose-pr` pull-request comment).** Rejected: it reintroduces
  the manual re-collection step this item exists to remove — a human would still have to remember to
  ask for the deferred findings to be applied.
- **Leave wording-only findings unmarked and keep fixing them inline.** The status quo, and the pain
  point motivating this item: every wording fix still costs a full CI cycle on the pull request that
  raised it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `prose-refresh.yml` — third thin caller of the shared `refresh.yml`, its own contract,
  branch, and rolling Draft PR
- [ ] `.github/prose-refresh-prompt.md` — the wording-quality contract, path-allowlisted to
  `docs/**`, `DESIGN.md`, and roadmap prose bodies only, excluding `README*`/`CLAUDE.md` and each
  roadmap item's
  metadata/H1/`Progress` block
- [ ] `.github/claude-review-prompt.md` — the new English-prose-quality lens (mirroring the
  existing Japanese one) and the `(non-blocking, prose)` decoration on both
- [ ] `.agent-workflows/pr-followup/workflow.md` — the defer-on-a-code-touching-PR exception in
  step 3
- [ ] `docs/ai-development.md` and its `docs/ja/ai-development.md` mirror — documenting the new
  exception to "treat every reviewer comment the same way"
- [ ] A short `CLAUDE.md` bullet pointing at the documented exception

### Log

## References

[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md) (the advisory reviewer
whose Japanese-prose-quality lens and bilingual-docs convention this item defers);
[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md) (the scheduled-refresh
design and the `refresh.yml` machinery this item reuses as its third caller);
[BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md) (the review-time,
not-a-gate precedent for prose judgment this item's motivation draws on); the `document-writing`,
`english-document-writing`, and `japanese-document-writing` skills (the wording-quality norms
`prose-refresh` applies).
