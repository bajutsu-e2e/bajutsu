**English** · [日本語](BE-0222-daily-doc-freshness-pr-ja.md)

# BE-0222 — Scheduled daily workflows that refresh the roadmap and docs separately, each opening its own review PR

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0222](BE-0222-daily-doc-freshness-pr.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0222") |
| Implementing PR | [#882](https://github.com/bajutsu-e2e/bajutsu/pull/882) |
| Topic | Contributor workflow |
| Related | [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds **two** scheduled (daily) GitHub Actions workflows. They reconcile the
human-maintained parts of the repository with what has actually shipped. When a workflow finds drift,
it opens its **own** **Draft** pull request for a human to review and merge.

- A **roadmap-refresh** workflow keeps BE items (`Status` / `Progress` / `Implementing PR`) current.
- A **docs-refresh** workflow keeps the prose docs (`docs/` and `DESIGN.md`) in step with behavior.

The two share one design: the credential shape, the contract-file pattern, the in-job gate, and the
rolling Draft PR. They run and ship independently.

Both workflows use the Claude Code action to *author* the updates. It is the same AI provider that the
advisory review already uses ([BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md)).
A human alone merges. No LLM ever touches the `run`/CI verdict, and the deterministic `make check`
gates each generated PR like any other.

## Motivation

The repository already keeps the *mechanically derivable* parts of its documents honest.

- `make roadmap-index` regenerates the index, and `make test` fails if the committed tables drift.
- [BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md) makes a
  broken `docs/` → roadmap link a gate failure.
- `roadmap-drift-check`
  ([BE-0149](../BE-0149-roadmap-placeholder-format-guardrail/BE-0149-roadmap-placeholder-format-guardrail.md))
  re-checks open PRs against the template.

None of these can cover content that needs *semantic judgment* to stay current. That content rots
silently:

- **BE item `Status` / `Progress` / `Implementing PR`.** The working agreement says a PR that advances
  an item ticks its `Progress` boxes and fills `Implementing PR` in the same change. It also says
  `Status` flips when work starts or ships. In practice a PR sometimes lands code without updating the
  item. An item then stays `Approved` after its code merged, or its `Progress` checklist lags the real
  state. Nothing catches this, because it is prose-and-state reconciliation, not a format check.
- **`docs/architecture.md#implementation-status`.** This section is the stated source of truth for
  "what already exists". It stays accurate only if a contributor remembers to update it when a
  capability lands.
- **`DESIGN.md` / `docs/architecture.md` in step with behavior
  ([BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)).** That rule is
  deliberately a review-time norm, not a CI gate. Checking that a paragraph of prose still matches the
  code needs semantic judgment. A gate would therefore put an LLM on the `run`/CI verdict path (prime
  directive 1). Attention enforces the rule instead, and attention lapses.

The common thread is that a deterministic gate *cannot* close these gaps without violating prime
directive 1. They accumulate between the moments a human happens to notice them.

A scheduled agent drafts the reconciliation and leaves the merge decision to a human. It closes the
latency gap without crossing that line. It is the authoring counterpart to the advisory reviewer.
BE-0203 added an AI *reviewer* that never gates a merge. This item adds an AI *author* that never
merges.

**Why two workflows, not one.** The two kinds of drift are different work, and a reviewer should judge
them apart.

Roadmap reconciliation is small and near-mechanical: flip a `Status`, tick `Progress`, add an
`Implementing PR` row, and regenerate the index. A reviewer can confirm it against the merged PRs in a
minute.

Docs reconciliation is heavier. It rewrites prose to match behavior across two languages, so the
reviewer must actually read the paragraphs.

Bundling both into one PR forces the fast, low-risk roadmap fixes to wait behind the slow, careful
docs review, and vice versa. It also mixes two diffs that a reviewer would rather judge separately.

Splitting the workflows also isolates failure and noise. A day when only the docs drift produces one
focused docs PR, not a combined PR whose roadmap half is empty. A problem in one refresh never blocks
the other. The two can even run on different cadences later (for example, roadmap daily and docs
weekly) without disturbing each other.

## Detailed design

The design has two scheduled workflows, `.github/workflows/roadmap-refresh.yml` and
`.github/workflows/docs-refresh.yml`. Both build on one **shared shape**. They differ only in *what*
each reconciles and *which* files each may touch. Every AI-authored change flows through a
human-reviewed, gate-checked PR. Nothing lands on `main` directly.

**Keep the shared shape shared in code, not just in this doc.** The commonality below is a design
commitment. Two independent YAML files can still drift apart as each is edited, for example when a
later tweak to the two-credential gate lands in one file and not the other.

The implementation must therefore factor the shared shape into a single reusable unit. The unit is a
`workflow_call` reusable workflow or a composite action. The shared shape covers the dormancy gate,
concurrency and timeout, the in-job `make check`, the rolling-branch clobber guard, and PR open and
update. Both thin workflow files call the unit with only their differing parameters: branch, contract
path, and allowlist. The two then stay in lockstep by construction, not by remembering to edit both
identically.

### The shared shape (both workflows)

1. **Triggers, concurrency, and timeout.** The triggers are `schedule` (a daily cron) and
   `workflow_dispatch` (on-demand). Give the two workflows slightly offset cron times so they never
   contend for a runner. Each job carries two safeguards, both following the `concurrency:` /
   `timeout-minutes:` precedent of `claude-review.yml`.
   - A `concurrency` group (`cancel-in-progress: false`), so a manual dispatch never races that
     workflow's own nightly run.
   - A `timeout-minutes` fail-fast cap, so a hung provider call is killed instead of tying up a runner.

2. **Two credentials, all-or-nothing dormancy.** Each job needs two independent credentials. It must
   be a green no-op unless *both* are present, so a half-provisioned repo never goes red. The gate
   combines two existing precedents: the single-credential dormancy gate that `roadmap-id.yml` uses for
   its App token, and the one that `claude-review.yml` uses for its AI-provider credential. Neither
   precedent gates on both, so this proposal is the first to require the pair.
   - **An AI provider**, selected exactly as `claude-review.yml` does (BE-0203,
     [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)). The
     selection uses the Claude Code subscription (`CLAUDE_CODE_OAUTH_TOKEN`) when present. Otherwise
     it uses Amazon Bedrock via OIDC (`AWS_BEDROCK_ROLE_ARN` + `BEDROCK_MODEL_ID`). If neither is
     present, the workflow is dormant. The provider authors the updates.
   - **The automation App token** (`AUTOMATION_BOT_APP_ID` / `AUTOMATION_BOT_PRIVATE_KEY`, via
     `actions/create-github-app-token`), used as `roadmap-id.yml` and `roadmap-drift-check.yml` use it.
     A PR opened with the default `GITHUB_TOKEN` does **not** trigger other workflows, so the refresh
     PR's own `check` CI would never run. The App's installation token carries no such restriction.
     Opening the PR under the App identity is what makes the deterministic gate actually run on the
     AI's output.

3. **A tracked contract file** (`.github/*-refresh-prompt.md`, mirroring
   `.github/claude-review-prompt.md`) holds the authoritative instruction that the action runs. Each
   workflow has its own contract file. Each file states three things.
   - A hard path allowlist, enforced on the diff before the PR is opened.
   - A **conservatism rule**. Propose an update only where there is concrete evidence of drift, such
     as a merged PR or a shipped capability. When uncertain, leave the file unchanged and note the
     uncertainty in the PR body rather than guessing.
   - The bilingual rule, where it applies. Any Japanese follows the `japanese-tech-writing` skill and
     the ですます調 register, and English and Japanese are updated together.

4. **In-job `make check` before opening the PR.** After the agent edits files, the workflow runs the
   deterministic gate in-job, which holds the AI's output to the same bar as any human change. A red
   gate does not block the PR, because the PR opens as **Draft** regardless. The result appears in the
   PR body. The PR is *also* subject to the normal `check` CI, which is why the App token is required.

5. **One rolling, idempotent, always-Draft, human-merged PR per workflow.** If the reconciliation
   produces no diff, the job exits without opening or touching a PR, so a quiet week brings no daily
   noise. Otherwise the job pushes to that workflow's fixed branch. It reuses the existing open PR if
   there is one, rather than opening a new PR every day. The PR opens with `--draft`. The human
   reviews, and *only* the human marks it ready and merges. Neither branch has an auto-merge.

   **Never clobber a human's work on the rolling branch.** The daily run rewrites the branch only when
   its current tip is a commit that the automation itself last authored (the App/bot identity). A
   human may have pushed onto the branch since the last run, for example a reviewer's fixup commit or
   an edit made mid-review. In that case the run does **not** force-update over it. It leaves the
   branch untouched and surfaces the skip, with a `::warning::` in the job and a note on the PR. The
   human's work is never overwritten, and the skip is loud rather than silent.

   This mirrors the guard in `scripts/check_stale_roadmap_prs.py` (`open_or_update_fix_pr`). That
   script deliberately bases its branch on the exact SHA a fix was computed from, not the live tip. It
   thereby avoids clobbering a newer commit with content computed from a stale snapshot (see its
   comment at lines 177–180). A fresh branch is created only when no rolling branch exists.

### What differs

| | roadmap-refresh | docs-refresh |
|---|---|---|
| Branch / PR | `chore/roadmap-refresh` | `chore/docs-refresh` |
| Contract | `.github/roadmap-refresh-prompt.md` | `.github/docs-refresh-prompt.md` |
| Reconciles | BE items' `Status` / `Progress` / `Implementing PR` against merged PRs; runs `make roadmap-index` when it changes an index-affecting field | `docs/architecture.md#implementation-status`; `DESIGN.md` / `docs/architecture.md` prose vs behavior (BE-0113) |
| Path allowlist | `roadmaps/**` only | `docs/**` and `DESIGN.md` only |

Neither workflow ever edits product code (`bajutsu/`, `BajutsuKit/`, tests, config, demos). The
allowlist forbids it, mirroring the "authoring only, never implement" boundary of the ideation skill.

The docs-refresh allowlist deliberately **excludes** the top-level `README*` and `CLAUDE.md`. Those
files are the project's contract surface, not behavior-tracking prose. `CLAUDE.md` in particular
states the prime directives that the AI author is itself bound by. Letting the agent auto-draft edits
to its own operating contract crosses a boundary we do not want to cross, even behind a Draft PR.

Edits to those files stay human-authored. The remit of the workflow is `docs/` and `DESIGN.md`, which
is exactly the prose that drifts against behavior. The deterministic `docs/` → roadmap link repair for
`README*` / `CLAUDE.md` is a separate, LLM-free concern. It is already owned by
[BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md).

### Prime-directive compliance

The LLM runs purely on the *authoring* path. It drafts updates, exactly as `record` drafts scenarios,
and its output is a Draft PR that a human must review and merge.

No LLM call is added to `run` or to any `required_status_checks`. The deterministic `check` remains the
sole merge arbiter, and the human is the sole judge. Each workflow's path allowlist keeps it out of
drivers, the runner, and tests, so it cannot affect determinism or the app-agnostic core.

## Alternatives considered

- **One combined workflow that refreshes roadmap and docs in a single PR.** This was the original shape
  of this proposal. Rejected in favor of splitting, for three reasons (see *Why two workflows*).
  - It forces the fast, near-mechanical roadmap fixes to wait behind the slow, careful docs review.
  - It mixes two diffs that a reviewer would rather judge apart.
  - It couples the two failure modes and cadences.

  The shared design lives in one place (this item), so the duplication cost of two workflows is small.
- **Deterministic-only refresh (no LLM).** This would regenerate indexes, re-check links, and refresh
  tracking-issue URLs on a schedule. Rejected as the *whole* answer. `make check`, `make roadmap-index`,
  and BE-0096 already cover those parts, and they are not what rots. The drift this item targets is
  `Status`/`Progress` lag and prose that no longer matches behavior. That drift is inherently semantic
  and cannot be reconciled without judgment.
- **Commit the refresh straight to `main`.** Rejected. It removes the human from the loop (prime
  directive 1: the human is the judge here) and bypasses review of AI-authored prose. Draft PRs are
  the whole point.
- **Extend the advisory reviewer (BE-0203) instead of adding new workflows.** The shapes differ.
  BE-0203 *reviews* an existing PR and posts comments, while these workflows *author* PRs from scratch
  on a schedule. Reusing its provider-selection logic is the right amount of sharing. Merging the jobs
  would overload one workflow with unrelated triggers and scopes.
- **On-demand only (`workflow_dispatch`, no cron).** Kept as an additional trigger. The value lies in
  the unattended daily cadence ("always current"), which an on-demand-only button does not deliver.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `roadmap-refresh.yml` — daily workflow reconciling BE `Status` / `Progress` / `Implementing PR`, its own contract, branch, and rolling Draft PR
- [x] `docs-refresh.yml` — daily workflow reconciling `docs/` / `DESIGN.md` prose vs behavior, its own contract, branch, and rolling Draft PR
- [x] Shared shape wired into both: two-credential dormancy gate, concurrency + timeout, in-job `make check` surfaced in the PR body, idempotent no-diff exit
- [x] Shared shape factored into a reusable `workflow_call` workflow (or composite action) both thin workflow files call, so they can't drift apart
- [x] Rolling-branch clobber guard: force-update only over the automation's own tip; skip loudly if a human pushed onto the branch
- [x] Path allowlists enforced per workflow (roadmap-only; docs-only, excluding `README*` / `CLAUDE.md`) so neither touches product code or the contract surface

### Log

- [#882](https://github.com/bajutsu-e2e/bajutsu/pull/882): shipped in one PR — the reusable `refresh.yml` (shared shape — two-credential dormancy gate,
  concurrency/timeout, App-token checkout, AI-authoring step, in-job `make check`), the two thin
  callers `roadmap-refresh.yml` / `docs-refresh.yml`, their contracts
  (`.github/{roadmap,docs}-refresh-prompt.md`), and the deterministic `scripts/refresh_pr.py`
  (path-allowlist enforcement, idempotent no-diff exit, rolling-branch clobber guard, always-Draft
  human-merged PR) with `tests/test_refresh_pr.py`.

## References

`.github/workflows/claude-review.yml` (BE-0203 — AI provider selection + Claude Code action usage,
the pattern the shared shape reuses); `.github/workflows/roadmap-id.yml` (BE-0089) and
`.github/workflows/roadmap-drift-check.yml` ([BE-0149](../BE-0149-roadmap-placeholder-format-guardrail/BE-0149-roadmap-placeholder-format-guardrail.md))
(the automation App token that lets a bot-opened PR trigger its own `check` CI);
[BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md) (the
deterministic link-integrity guard this complements); [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)
(keep DESIGN.md / architecture.md in step with behavior — the review-time norm this automates a nudge
for); [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
(vendor-neutral AI provider); the `japanese-tech-writing` skill (the register the Japanese output
follows).
