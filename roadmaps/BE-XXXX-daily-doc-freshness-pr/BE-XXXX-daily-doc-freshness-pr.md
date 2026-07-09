**English** · [日本語](BE-XXXX-daily-doc-freshness-pr-ja.md)

# BE-XXXX — Scheduled daily workflow that refreshes roadmap and docs, then opens a review PR

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-daily-doc-freshness-pr.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Development infrastructure (contributor workflow) |
<!-- /BE-METADATA -->

## Introduction

Add a scheduled (daily) GitHub Actions workflow that reconciles the human-maintained parts of the
roadmap and docs with what has actually shipped, and — when it finds drift — opens a single **Draft**
pull request with the proposed updates for a human to review and merge. The workflow uses the Claude
Code action (the same AI provider the advisory review already uses, [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md))
to *author* the updates; a human is the only one who merges. No LLM ever touches the `run`/CI
verdict, and the generated PR is still gated by the deterministic `make check` like any other.

## Motivation

The repository already keeps the *mechanically derivable* parts of its documents honest: `make
roadmap-index` regenerates the index and `make test` fails if the committed tables drift; [BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md)
makes a broken `docs/` → roadmap link a gate failure; `roadmap-drift-check` ([BE-0149](../BE-0149-roadmap-placeholder-format-guardrail/BE-0149-roadmap-placeholder-format-guardrail.md))
re-checks open PRs against the template. What none of these can cover is the content that requires
*semantic judgment* to keep current, and which therefore rots silently:

- **BE item `Status` / `Progress` / `Implementing PR`.** The working agreement says a PR that
  advances an item ticks its `Progress` boxes and fills `Implementing PR` in the same change, and
  that `Status` is flipped when work starts or ships. In practice a PR sometimes lands code without
  updating the item, so an item stays `Proposal` after its code merged, or its `Progress` checklist
  lags the real state. Nothing catches this — it is prose-and-state reconciliation, not a format
  check.
- **`docs/architecture.md#implementation-status`** — the stated source of truth for "what already
  exists". It only stays accurate if a contributor remembers to update it when a capability lands.
- **`DESIGN.md` / `docs/architecture.md` in step with behavior ([BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)).**
  That rule is deliberately a review-time norm, not a CI gate, precisely because checking that a
  paragraph of prose still matches the code needs semantic judgment — which would put an LLM on the
  `run`/CI verdict path if it were a gate (prime directive 1). So it is enforced by attention, and
  attention lapses.

The common thread: these gaps are exactly the ones a deterministic gate *cannot* close without
violating prime directive 1, and so they accumulate between the moments a human happens to notice.
A scheduled agent that drafts the reconciliation — and leaves the merge decision to a human — closes
the latency gap without crossing that line. It is the authoring counterpart to the advisory reviewer:
BE-0203 added an AI *reviewer* that never gates a merge; this adds an AI *author* that never merges.

## Detailed design

A single scheduled workflow, `.github/workflows/daily-doc-refresh.yml`, plus a tracked instruction
file that is its contract. Every AI-authored change flows through a human-reviewed, gate-checked PR —
never onto `main` directly.

### 1. The scheduled workflow and its two-credential shape

Trigger on `schedule` (a daily cron) and `workflow_dispatch` (on-demand). The job needs **two**
independent credentials, and must be a green no-op unless *both* are present (matching the
all-or-nothing dormancy of `roadmap-id.yml` / `claude-review.yml` so a half-provisioned repo never
goes red):

- **An AI provider**, selected exactly as `claude-review.yml` does (BE-0203, [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)):
  the Claude Code subscription (`CLAUDE_CODE_OAUTH_TOKEN`) when present, else Amazon Bedrock via OIDC
  (`AWS_BEDROCK_ROLE_ARN` + `BEDROCK_MODEL_ID`), else dormant. This authors the updates.
- **The automation App token** (`AUTOMATION_BOT_APP_ID` / `AUTOMATION_BOT_PRIVATE_KEY`, via
  `actions/create-github-app-token`), as `roadmap-id.yml` and `roadmap-drift-check.yml` use it. It is
  required because a PR opened with the default `GITHUB_TOKEN` does **not** trigger other workflows,
  so the refresh PR's own `check` CI would never run — the App's installation token carries no such
  restriction. Opening the PR under the App identity is what makes the deterministic gate actually
  run on the AI's output.

Serialize with a `concurrency` group (`cancel-in-progress: false`) so a manual dispatch never races
the nightly run, and set a `timeout-minutes` fail-fast cap as `claude-review.yml` does.

### 2. The refresh contract (a tracked prompt file)

A tracked `.github/daily-doc-refresh-prompt.md` (mirroring `.github/claude-review-prompt.md`) is the
authoritative instruction the action runs. It scopes the task precisely:

- **What to reconcile:** BE items' `Status` / `Progress` / `Implementing PR` against merged PRs since
  the last run; `docs/architecture.md#implementation-status`; and `DESIGN.md` / `docs/architecture.md`
  prose against current behavior (BE-0113). When it flips a `Status` or edits an index-affecting
  field, it runs `make roadmap-index` so the deterministic index is regenerated in the same change.
- **Bilingual rule:** any Japanese it writes follows the `japanese-tech-writing` skill and the
  ですます調 register, and English/Japanese docs are updated together (the working-agreement rule).
- **Hard scope boundary:** it edits *only* `roadmaps/**`, `docs/**`, `DESIGN.md`, and the top-level
  `README*` / `CLAUDE.md` — **never** product code (`bajutsu/`, `BajutsuKit/`, tests, config, demos),
  enforced by a path allowlist on the diff before the PR is opened. This mirrors the ideation skill's
  "authoring only, never implement" boundary.
- **Conservatism:** propose an update only where there is concrete evidence of drift (a merged PR, a
  shipped capability); when uncertain, leave the document unchanged and note the uncertainty in the
  PR body rather than guessing. The reviewer, not the agent, decides.

### 3. Verify with the deterministic gate before opening the PR

After the agent edits files, the workflow runs `make check` in-job. This keeps the AI's output held
to the same bar as any human change (format, lint, roadmap-format, index-drift, typecheck, tests). A
red gate does not block the PR from opening — it opens as **Draft** regardless — but the result is
surfaced in the PR body so the human sees immediately whether the refresh is mergeable as-is. The PR
is *also* subject to the normal `check` CI (that is why the App token is required, item 1).

### 4. One rolling Draft PR, human-merged, idempotent

- **Idempotent:** if the reconciliation produces no diff, the job exits without opening or touching a
  PR — no daily noise on a quiet week.
- **One rolling PR:** push to a fixed branch (e.g. `chore/daily-doc-refresh`) and reuse the existing
  open PR if there is one (force-update the branch), rather than opening a new PR every day. A single
  living PR is easier to review than a pile of stale ones.
- **Always Draft, never auto-merged:** the PR opens with `--draft`; the human reviews, and *only* the
  human marks it ready and merges. There is no auto-merge on this branch. The PR body summarizes what
  changed, why (which merged PRs / capabilities motivated each edit), and the `make check` result.

### Prime-directive compliance

The LLM is used purely on the *authoring* path — it drafts document updates, exactly like `record`
drafts scenarios — and its output is a Draft PR that a human must review and merge. No LLM call is
added to `run` or to any `required_status_checks`; the deterministic `check` remains the sole merge
arbiter, and the human is the sole judge. The workflow's path allowlist keeps it out of drivers, the
runner, and tests, so it cannot affect determinism or the app-agnostic core.

## Alternatives considered

- **Deterministic-only refresh (no LLM).** Regenerate indexes, re-check links, refresh tracking-issue
  URLs on a schedule. Rejected as the *whole* answer: those parts are already covered by `make check`
  / `make roadmap-index` / BE-0096, and they are not what rots. The drift this item targets —
  `Status`/`Progress` lag, prose vs behavior — is inherently semantic and cannot be reconciled without
  judgment.
- **Commit the refresh straight to `main`.** Rejected: it removes the human from the loop (prime
  directive 1 — the human is the judge here) and bypasses review of AI-authored prose. A Draft PR is
  the whole point.
- **Extend the advisory reviewer (BE-0203) instead of a new workflow.** Different shape: BE-0203
  *reviews* an existing PR and posts comments; this *authors* a PR from scratch on a schedule. Reusing
  its provider-selection logic (item 1) is the right amount of sharing; merging the two jobs would
  overload one workflow with two unrelated triggers and scopes.
- **On-demand only (`workflow_dispatch`, no cron).** Kept as an additional trigger, but the value is
  the unattended daily cadence — "always current" — which an on-demand-only button does not deliver.
- **A new PR every day instead of one rolling PR.** Rejected: it produces review backlog and stale
  duplicates; a single force-updated Draft PR is easier to keep on top of.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Scheduled workflow `daily-doc-refresh.yml` with the two-credential dormancy gate, concurrency, and timeout (item 1)
- [ ] Tracked refresh contract `.github/daily-doc-refresh-prompt.md` with scope, bilingual, and conservatism rules (item 2)
- [ ] In-job `make check` verification with the result surfaced in the PR body (item 3)
- [ ] One rolling, idempotent, always-Draft, human-merged PR via the App token (item 4)

## References

`.github/workflows/claude-review.yml` (BE-0203 — AI provider selection + Claude Code action usage,
the pattern item 1 reuses); `.github/workflows/roadmap-id.yml` (BE-0089) and
`.github/workflows/roadmap-drift-check.yml` ([BE-0149](../BE-0149-roadmap-placeholder-format-guardrail/BE-0149-roadmap-placeholder-format-guardrail.md))
(the automation App token that lets a bot-opened PR trigger its own `check` CI);
[BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md) (the
deterministic link-integrity guard this complements); [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)
(keep DESIGN.md / architecture.md in step with behavior — the review-time norm this automates a nudge
for); [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
(vendor-neutral AI provider); the `japanese-tech-writing` skill (the register the Japanese output
follows).
