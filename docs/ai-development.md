**English** · [日本語](ja/ai-development.md)

# Developing with AI agents (and humans) in parallel

> How several sessions — humans and AI agents — work this repo at the same time without
> colliding or regressing each other. The short version lives in [`CLAUDE.md`](../CLAUDE.md);
> this page is the full operational guide.

> **New to contributing? Start with the [contributor workflow tutorial](contributor-workflow-tutorial.md)** —
> a hands-on walkthrough of your first proposal and first implementation. This page is the detailed
> reference it links to for the rules (the gate, branches, BE-ID lifecycle, model tiers, PR template).

The whole design rests on one property: **the deterministic gate is cheap, runs anywhere, and
mirrors CI exactly.** This property is what lets work fan out safely — every branch is independently
verifiable, so "green locally" reliably predicts "green in CI", and the test suite is a
regression net that catches one session breaking another's feature.

## The gate

```bash
make check
```

Its steps mirror [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) exactly; the current
step list lives in [`CLAUDE.md`](../CLAUDE.md), the single source of truth for the gate. The Python
core needs no Simulator, so it runs on Linux in seconds. Run it before you call a change done and
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

The same `core.hooksPath` also wires a tracked **commit-msg hook**
([`.githooks/commit-msg`](../.githooks/commit-msg), BE-0069): it blocks a commit whose subject isn't
a scoped conventional subject (`type(scope): …`, or `docs: …`), catching the mechanical convention at
commit time instead of in review. It is deliberately narrow — merge / revert / fixup / squash commits
pass, and it no-ops when `uv` isn't on PATH; bypass a one-off with `git commit --no-verify`.

When you change behavior, change a test with it — the suite is the contract that protects every
other session from your change.

The short form of this rule is in [`CLAUDE.md`](../CLAUDE.md).

## Rebase early, integrate small conflicts

```bash
make preflight   # git fetch origin && git rebase origin/main && make check, then a done-checklist
```

`make preflight` ([`scripts/preflight.sh`](../scripts/preflight.sh), BE-0069) is the run-it-early
version of the pre-push routine: it syncs, rebases onto `origin/main`, runs the gate, then prints
the "definition of done" reminder (both-language docs touched? a test changed with the behavior?
`Status` flipped if shipping?). It is **advisory and human-initiated** — the pre-push hook already
*gates* `make check`. Run it whenever; you don't need to remember the individual steps.

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

The short form of this rule is in [`CLAUDE.md`](../CLAUDE.md).

## Isolate concurrent sessions with worktrees

Two agents must never edit the same checkout. Give each session its own
[worktree](https://git-scm.com/docs/git-worktree) + branch, all sharing one `.git`:

```bash
# from the main checkout
make worktree TOPIC=<topic>             # branch claude/<topic> at ../bajutsu-<topic>
make worktree TOPIC=<topic> PREFIX=<user>   # a human's <user>/<topic> branch
```

`make worktree` ([`scripts/worktree.sh`](../scripts/worktree.sh), BE-0069) does the whole recipe:
`git fetch origin`, `git worktree add ../bajutsu-<topic> -b claude/<topic> origin/main`, then
`make setup` in the new tree (deps + the self-healing git hooks). The branch prefix defaults to
`claude`; pass `PREFIX=<user>` for a human branch.

The `git fetch origin` is baked in and *not* optional: `origin/main` is a local tracking ref that
only advances when you fetch, so skipping it would branch the new worktree off whatever main
looked like last time — re-introducing conflicts that other sessions already merged away. The
command fetches first so that foot-gun cannot happen.

When the branch is merged (or abandoned), clean up:

```bash
git worktree remove ../bajutsu-<topic>
```

Generated and scratch output — `runs/`, `tmp/`, `.venv/`, build artifacts — is gitignored on
purpose; keep it out of commits so worktrees stay independent.

The short form of this rule is in [`CLAUDE.md`](../CLAUDE.md).

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
| Scenario schema | [`bajutsu/scenario/models/scenario.py`](../bajutsu/scenario/models/scenario.py) | the hub artifact; codegen/runner/report all read it |
| Config shape | [`bajutsu/config/`](../bajutsu/config/) | per-target layering every command resolves through |

## CI keeps the branches honest

CI runs the same gate on every PR and uses
`concurrency: ci-${{ github.ref }}` with `cancel-in-progress`, so re-pushes to the same branch
supersede stale runs instead of piling up. Two PRs that each pass independently can still
conflict in behavior — the merge is where they meet, which is exactly why the deterministic test
suite (not an LLM, not a human eyeball) is the arbiter. Keep the suite meaningful and your branch
rebased, and parallel work composes.

## Naming GitHub Actions workflows and jobs

A workflow's `name:` and each job's `name:` are all a reviewer sees in the Actions tab and a PR's
checks list — the YAML behind them is a click away, so each name has to stand on its own. Name both
in one shape: a short plain-language phrase for what the check does, plus a parenthetical for the
tool or scope when that adds information — `E2E (Simulator)`, `Swift (BajutsuKit)`,
`Web E2E (Playwright)`, `Dependency audit (pip-audit)`. Never leave a bare single word (`docs`,
`build`, `deploy`) that only makes sense once you open the run. `ios-e2e.yml` and `swift.yml` are the
canonical examples (BE-0122). A `name:` that itself contains a colon-space needs quoting so YAML
doesn't read it as a nested mapping — `name: "Roadmap: allocate BE IDs"`.

One constraint bounds any rename. A required status check's context is the **job's** `name:`
verbatim — not the workflow's — and `main`'s branch-protection ruleset pins a few of these by exact
string: `check` (`ci.yml`), `E2E` (`ios-e2e.yml`), and `require two approvals for BE proposals`
(`roadmap-proposal-approvals.yml`). Renaming one of those job names without editing the ruleset's
`required_status_checks` in the same instant strands every open PR on a check that no longer
reports, silently blocking merges. Ruleset edits are out-of-repo admin state a normal PR can't
carry, so leave those three names as they are; a deliberate rename must be paired with a human admin
edit to the ruleset.

### The per-platform E2E job set

Each backend has one on-device E2E workflow — [`ios-e2e.yml`](../.github/workflows/ios-e2e.yml)
(macOS / idb + XCUITest), [`android-e2e.yml`](../.github/workflows/android-e2e.yml) (Linux+KVM /
adb), [`web-e2e.yml`](../.github/workflows/web-e2e.yml) (Linux / Playwright). They share a job
vocabulary so a reviewer reads the same shape across platforms and a new backend has a shape to
match, rather than one bundled pass/fail per lane. The functional heart every lane carries is
**smoke** — a real `bajutsu run` over the showcase scenarios on the real backend, asserting
deterministic pass/fail ("does bajutsu drive this platform?"). Other jobs verify a specific
capability, and a platform carries one only where it applies:

| Job | What it verifies | iOS | Android | Web |
|---|---|:-:|:-:|:-:|
| `smoke` | functional `bajutsu run` over the showcase | ✓ | ✓ | ✓ |
| `golden` | element-tree (BE-0006) matches the committed baseline | ✓ | ✓ | — |
| `visual` | pixel VRT against the committed baseline | ✓ | ✓ | — |
| `conformance` | driver contract (BE-0114) on the real backend | ✓ | ✓ | ✓ |
| `codegen` / `gestures` | native-test output / multi-touch (idb can't) | ✓ | — | — |
| `fallback` | resident vs `uiautomator dump` read channels agree (BE-0245) | — | ✓ (step) | — |

Two rules keep the set honest. **Every lane is required, per-lane.** A required status check is a
job `name:` the ruleset pins (above); each lane carries its own always-reporting aggregator
(`E2E`, `E2E (android)`, `E2E (web)`, BE-0279) whose heavy jobs a `changes` job path-gates, so an
unrelated PR is neither run nor blocked — per-lane aggregators (rather than one aggregator across backends) keep
attribution: a red check names the backend that broke. **Host-specific or upstream-fragile checks
stay off the required gate.** `visual` is a pixel compare whose baseline varies by renderer, and the
element-tree `golden` runs against an upstream dependency (`idb_companion`, the on-device server)
whose drift is out of our control — both run per PR as signals but are excluded from each
aggregator's `needs:`, so a drift surfaces without blocking merges.

## Right-sizing the model and reasoning effort (BE-0103)

This repository is agent-driven, so a session's **model** and **reasoning effort** are a real,
recurring token cost. Match them to the task's cognitive load: pay for a capable model at high
effort where the work needs it, and downshift for mechanical chores. This is **advisory** — a human
can always upshift for a hard instance — and it never touches the deterministic `run` / CI gate,
which calls no model regardless of what a *development* session runs at.

The failure mode is asymmetric: over-provisioning wastes tokens invisibly (the output still looks
fine), while under-provisioning shows up loudly as a bad result. So the natural drift is toward
*always-max*, which is exactly the waste this convention removes — without downshifting so far that
quality suffers on the hard tasks.

### The task → capability matrix

This table is the single source of truth; the skill frontmatter (below) and the subagent guidance
reflect it. Tasks map to one of three tiers along two axes — model and reasoning effort:

| Tier | Model | Effort | Tasks |
|---|---|---|---|
| **Heavy** | `opus` | high | Implementing a BE item (`implement-be`), non-trivial refactors, architecture / design decisions, debugging a failing gate |
| **Medium** | `sonnet` | moderate | Roadmap ideation / authoring (`ideation`), technical writing and translation review (`english-document-writing`, `japanese-document-writing`), PR review |
| **Light** | `haiku` | low or none | Flipping a roadmap item's `Status`, doc formatting and link fixes, mechanical renames, lockfile / format chores, drafting a first-pass translation before the medium-tier review |

The tier → model-id mapping lives only here, so re-pointing a tier at a new Claude model is a
one-line change in one place. The model ids above are Claude Code aliases (`opus` / `sonnet` /
`haiku`), which stay stable as the underlying model versions advance.

### Where the default applies itself: skill frontmatter

Each in-repo skill declares its tier as a `model:` field in its `SKILL.md` frontmatter, so the
harness picks the right model when the skill runs — nothing to remember, still overridable:

- [`implement-be`](../.claude/skills/implement-be/SKILL.md) → `opus` (Heavy)
- [`propose-and-build`](../.claude/skills/propose-and-build/SKILL.md) → `opus` (Heavy) — it
  implements product code in its Phase B, so it carries the same tier as `implement-be`.
- [`ideation`](../.claude/skills/ideation/SKILL.md) → `sonnet` (Medium)
- [`document-writing`](../.claude/skills/document-writing/SKILL.md) → `sonnet` (Medium)
- [`english-document-writing`](../.claude/skills/english-document-writing/SKILL.md) → `sonnet` (Medium)
- [`japanese-document-writing`](../.claude/skills/japanese-document-writing/SKILL.md) → `sonnet` (Medium)
- [`roadmap-filter`](../.claude/skills/roadmap-filter/SKILL.md) → `haiku` (Light) — a read-only
  survey of the roadmap by `Status` (BE-0162): it wraps `make roadmap-status STATUS="…"` so a
  session lists just the items in one status (e.g. every open `Proposal`), with each item's file
  path to open next, instead of paging through the dashboard's rendered HTML or opening each item
  file to check its `Status`.

Most light-tier chores aren't skills, so that tier is otherwise reached interactively or by subagent
delegation, below — `roadmap-filter` is the exception, since its whole job is one light,
deterministic lookup. `tests/test_skill_models.py` checks that each skill's `model:` is a known,
valid id, so a typo fails the gate locally instead of silently falling back.

### Phases and subagent delegation

The frontmatter can't reach interactive and delegated work, so choose there by hand:

- **Phases within a session** — downshift (or `/fast`) for exploration, research, and mechanical
  chores; upshift for implementation and design. The `/model` and `/fast` controls switch model and
  effort mid-session.
- **Subagent delegation** — when spawning a subagent via the Agent tool, pass the `model` that
  matches the *delegated* task, not the driver's: a broad `Explore` fan-out or an index
  regeneration can run cheaper than the session driving it. This is also the only lever for the
  out-of-repo review plugins (`pr-review-toolkit`), whose frontmatter we don't own — set their model
  at spawn time.

Deliberately **not gate-enforced**: which model a session used isn't recoverable from the diff, and
hard-pinning would remove the human's judgment to upshift when a "light" task turns out hard. This
follows the same "procedures as commands, advisory not policy" precedent as the rest of the
contributor workflow ([BE-0069](../roadmaps/BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)).

## Authoring and shipping roadmap items: the three skills

Turning an idea into shipped code runs through three skills that form a triangle — author,
ship, or both:

- [`ideation`](../.claude/skills/ideation/SKILL.md) — **author only.** A sounding board that
  shapes an idea into a BE proposal and stops at the `roadmaps/` files (never touches product
  code). The proposal carries the `BE-XXXX` placeholder; CI allocates the real id after the PR
  merges.
- [`implement-be`](../.claude/skills/implement-be/SKILL.md) — **ship a numbered item.** Takes an
  already-allocated `BE-NNNN`, treats its proposal as the spec, implements it with tests, flips
  the item to `Status: Implemented`, and proves `make check` green.
- [`propose-and-build`](../.claude/skills/propose-and-build/SKILL.md) — **both, stacked.**
  Composes the other two for a small, settled item the author is ready to build now: it authors
  the proposal *and* implements it in parallel, landing them as a temporary two-PR stack (the
  proposal PR first, the implementation PR second). Once the proposal merges and the id is
  allocated, a hand-off rebases the implementation branch, rewrites its `BE-XXXX` references to
  the allocated `BE-NNNN`, retargets it onto `main`, and runs `implement-be`'s promotion + gate
  steps — so the stack collapses into an ordinary `implement-be`-shaped PR.

**Picking one.** The serial `ideation` → merge → allocate → `implement-be` path is the default:
it forces a design to clear review before code is written, and it keeps the `BE-NNNN` sequence
contiguous by only spending a number on an item that ships
([BE-0089](../roadmaps/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)).
Reach for `propose-and-build` only when that path's latency is pure overhead — a small,
well-scoped item whose design the author does not expect review to reshape. The parallelism buys
the dead time between "proposal opened" and "id allocated"; it costs rework on the
implementation branch if review changes the proposal, so fall back to the serial path whenever a
design is genuinely uncertain.

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
  scoped subject. A PR that *introduces* a new roadmap item also keeps the plain scoped subject — it
  carries **no** `[BE-NNNN]` prefix, because the id is allocated on `main` after the merge (see
  [Roadmap items](#roadmap-items-be-ids-strict)).
- **CI enforces the title.** The `pr-title` workflow (`.github/workflows/pr-title.yml`) runs
  `scripts/lint_pr.py --title-only` on every PR — and re-runs when the title is edited. It fails the
  check when the title is not a scoped conventional subject, and when the branch name encodes a
  roadmap id (`claude/be-0050-<slug>`) but the title doesn't lead with the matching `[BE-0050]`
  prefix (a missing or mismatched id). The branch — not the diff — is the authoritative id signal,
  so a copy-pasted `[BE-0046]` on a `be-0050` branch is caught.

### Body

The tracked [`.github/PULL_REQUEST_TEMPLATE.md`](../.github/PULL_REQUEST_TEMPLATE.md) is the canonical
form of this shape — GitHub pre-fills it into every new PR, and **when you (AI) draft a PR you follow
it**: fill the sections that apply and delete the rest. The recurring `## Prime-directive compliance`
and `## Verification` blocks it ships pre-filled are the canonical wording — trim them to what the
change bears on rather than re-inventing the phrasing. The rest of this section is the reference the
template's inline comments point back to.

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
  `BE-XXXX` placeholder note — the workflow numbers it on `main` after the merge; don't hand-edit the
  number).
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

[BE-NNNN]: roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

The short form of these rules is in [`CLAUDE.md`](../CLAUDE.md).

## Responding to PR review comments

**Whoever owns the pull request** answers reviews comment by comment — a human contributor
or an AI agent alike. When a reviewer (Claude Code — the automated reviewer, see below — or a
human) leaves comments, keep working until every comment is resolved, then **reply to each comment
individually**. A single summary reply on the PR is not enough: each comment thread gets its own
reply, so the thread that raised a point is the thread that records its resolution.

Every reply states two things:

- **that the comment is addressed** — fixed in code, or consciously declined; and
- **the grounds for it** — the concrete change that resolves it (what you altered, and where —
  cite the commit or the file/line), or, when you make no change, the specific reason the comment
  does not apply.

A bare "done" or a 👍 is not a reply under this rule; the grounds are what let a later reader
of the thread audit the resolution. Keep each reply short and factual — the point is evidence, not
narration.

**Every answered comment gets both a reply and a resolved thread** — whether you fixed it or
consciously declined it, leave the reply that records the grounds, then mark that thread
resolved. The reply says *why*; resolving the thread says it is *settled*, so the set of open
threads always reflects exactly what still needs attention rather than a pile of already-handled
comments. The one exception is the undecided case below: a thread you have left open on purpose
stays open — never resolve a comment whose question is still unanswered.

When you are unsure how a comment should be handled — the fix is ambiguous, or it touches
something architecturally significant — ask rather than guess (an AI agent checks with the human
driving it; a human contributor checks with the reviewer or a maintainer), and leave that thread
open until it is decided.

### The automated reviewer (Claude Code, BE-0203)

Once the `claude-review` Environment has a provider credential (a Claude Code subscription token, or an
Amazon Bedrock role plus a `BEDROCK_MODEL_ID` variable), **Claude Code** reviews every pull request
from a branch in this repository automatically, run from the
[`claude-review`](../.github/workflows/claude-review.yml) workflow. It reviews when a PR opens and
re-reviews on each push, against the
[`.github/claude-review-prompt.md`](../.github/claude-review-prompt.md) contract, and posts inline
line-level comments (with `suggestion` blocks where a fix is mechanical) — inline findings only, no
top-level summary, since the job re-runs on every push and a fresh summary each time would leave stale,
contradictory overviews on the PR. It aims to raise every finding in one pass rather than dribbling
them out across re-runs. Until
a credential is provisioned the workflow is a dormant green no-op — it posts nothing and never
blocks — so no review appearing on a PR yet just means the Environment isn't configured. The
prompt points the reviewer at *this repository's* contract — the three
[prime directives](../CLAUDE.md#prime-directives-do-not-violate), the docstring standard, the
bilingual-docs rule, the BE-ID lifecycle — so it catches what a generic reviewer cannot.

It is **advisory, never a gate.** It is deliberately not a required status check, and its job result
is decoupled from its findings (a review that found issues is a *successful* review, so the job goes
red only on an infrastructure failure). The deterministic `check` / `E2E` gates remain the only
merge arbiters — this is a reviewer, not a judge (prime directive 1). Treat its comments exactly as
you would any reviewer's, under the reply rules above.

- **On demand.** Beyond the auto-review, a maintainer or collaborator can write `@claude review` on
  a PR (or reply to a review thread) to request a fresh pass or a follow-up on a specific comment.
  This path is gated to trusted actors (OWNER / MEMBER / COLLABORATOR) — because comment events run
  with repo secrets even on fork PRs — so `@claude review` from anyone else is ignored.
- **Forks.** A plain `pull_request` event from a fork does not expose secrets (by GitHub's design),
  so auto-review covers same-repo `claude/<topic>` / `<user>/<topic>` branches; a fork PR is
  reviewed on demand by a maintainer instead.
- **Migration off Copilot (manual, out-of-repo).** The workflow lands alongside Copilot's review so
  the two run in parallel and can be compared; once Claude Code's review has proven itself, a
  maintainer **disables Copilot's automatic review in the repository / organization settings**.
  That is admin state a PR cannot carry (the same shape as the branch-protection ruleset edits
  BE-0122 and BE-0089 call out), so it is an explicit manual step.

### The scheduled refreshers (Claude Code, BE-0222)

Two daily scheduled workflows keep the human-maintained parts of the repo in step with what has
shipped, and are the **authoring** counterpart to the advisory reviewer above: BE-0203 added an AI
*reviewer* that never gates a merge; these add an AI *author* that never merges.

- [`roadmap-refresh`](../.github/workflows/roadmap-refresh.yml) reconciles each BE item's
  `Status` / `Progress` / `Implementing PR` against what has merged on `main`.
- [`docs-refresh`](../.github/workflows/docs-refresh.yml) reconciles the prose that drifts against
  behavior — `docs/architecture.md#implementation-status`, and `DESIGN.md` / `docs/architecture.md`
  prose vs the code (the [BE-0113](../roadmaps/BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)
  review-time norm).

Both are thin callers of one reusable workflow,
[`refresh.yml`](../.github/workflows/refresh.yml), which holds the shared shape so the two cannot
drift apart; they differ only in their branch, contract file
([`.github/roadmap-refresh-prompt.md`](../.github/roadmap-refresh-prompt.md),
[`.github/docs-refresh-prompt.md`](../.github/docs-refresh-prompt.md)), and **path allowlist**
(`roadmaps/**`; `docs/**` + `DESIGN.md`, deliberately excluding the `README*` / `CLAUDE.md` contract
surface). The key properties, all consistent with the sibling automations:

- **Dormant until provisioned.** Each run is a green no-op unless *both* an AI provider (the same
  `claude-review` Environment credential the reviewer uses) **and** the automation App token (as
  `roadmap-id.yml`) are present — the App identity is what lets the bot-opened PR trigger its own
  `check` CI. A half-configured repo never goes red.
- **AI authors, the gate and a human decide.** The Claude Code action only edits the working tree;
  a deterministic step then enforces the path allowlist (restoring any stray edit), runs `make check`
  in-job, and opens **one rolling Draft PR** per workflow. No LLM is on the `run`/CI verdict path
  (prime directive 1); only a human marks the PR ready and merges it.
- **Idempotent and non-clobbering.** A quiet day opens no PR. When there is drift, the run reuses the
  workflow's fixed branch and force-updates it only when its remote tip was committed by the bot
  itself (with `--force-with-lease`), so a reviewer's fixup pushed onto the branch is never
  overwritten — the run skips loudly instead.

## Roadmap items: BE IDs (strict)

The roadmap is **one directory per item** under [`roadmaps/`](../roadmaps/README.md). Each item lives in
`roadmaps/BE-NNNN-<slug>/`, which holds the English file `BE-NNNN-<slug>.md` and its
Japanese version `BE-NNNN-<slug>-ja.md` (same ID and slug). **BE** stands for *Bajutsu Evolution* and `NNNN`
is a **zero-padded, four-digit, monotonically increasing** ID. Every item lives directly under `roadmaps/`
in a flat layout: its path is fixed the moment its ID is allocated and never moves (BE-0159 retired the
per-`Status` folders BE-0078 introduced — `Status` now decides only the dashboard bucket, below).

When you add a roadmap item:

1. **Allocate the next ID** = the highest existing `BE-NNNN` + 1, over every item under `roadmaps/`. Find
   the current max with:
   ```bash
   ls -d roadmaps/BE-*/ | sort | tail -1
   ```
   Never reuse, skip, or guess a number.
2. **Create the item directory and both language files** directly under `roadmaps/` with `Status: Proposal` (a new item is always a
   proposal first) — `roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md`
   (English) and `roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>-ja.md` (Japanese, same ID & slug). Nothing
   else needs editing: the [roadmap dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html)
   reads the item's `Status` + `Topic` straight off its metadata on every docs build, so there is no
   index table anywhere in [en](../roadmaps/README.md) / [ja](../roadmaps/README-ja.md) to keep in sync.
3. **IDs are permanent.** Never renumber an existing item — not when its status changes, not when
   it is completed, not when it is removed from a table. A BE ID, once assigned, refers to that
   item forever.

The number is allocated **on `main`, after the PR merges** — not at PR-open
([BE-0089](../roadmaps/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)).
Drafting with the `BE-XXXX` placeholder is the norm: an item keeps `BE-XXXX` through authoring,
review, and the merge itself, and a **BE-creation PR carries no `[BE-NNNN]` prefix at all** — its
title stays a plain scoped subject, since the real number is not known until after the merge. The
merge is a push to `main`, which triggers the `roadmap-id` workflow; it runs the allocator against
`main`, renames each placeholder to the next free `BE-NNNN`, commits the rename directly to `main`,
and comments the allocated id on the merged PR. Because allocation runs in merge
order on `main`, the `BE-NNNN` sequence is **contiguous by construction** — a rejected PR never
merges, so it never spends a number.

Landing that commit on protected `main` needs a bypass identity: a dedicated GitHub App on `main`'s
ruleset bypass list, granted `contents: write` and `pull-requests: write` on this repository only,
whose id and private key are stored as the `AUTOMATION_BOT_APP_ID` / `AUTOMATION_BOT_PRIVATE_KEY` Actions
secrets. A maintainer sets this up once — see *Setting up the merge-time allocation App* below. Until
those secrets exist the workflow is a green no-op, so `main` stays green while the App is being
provisioned. The job only ever runs reviewed code post-merge (it checks out `main`), pins every
action to a full commit SHA, and runs `scripts/check_renumber_diff.py`, which fails the job if the
bypass commit touches anything outside `roadmaps/` — capping the token's blast radius to that tree.

You may still allocate a number by hand (the highest existing `BE-NNNN` + 1) when you want it fixed
up front; that path is unchanged. BE-0061's collision hardening — the atomic `refs/be-claims/*`
reservations and the `roadmap-id-repair` / `roadmap-claims-gc` workflows — has since been **retired**:
merge-time allocation runs at most one allocate at a time against the latest `main`, so the sequence
is contiguous by construction and two branches can no longer contend for the same number, making the
reservation ledger and its repair backstop redundant. See
[BE-0061](../roadmaps/BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md).

#### Setting up the merge-time allocation App

A maintainer with admin rights does this once, so the `roadmap-id` workflow can push the renumber
commit past `main`'s branch protection:

1. **Create a GitHub App** (org- or repo-owned) with no webhook and no callback URL. Grant it exactly
   **Repository permissions → Contents: Read and write** (to push the renumber) and **Pull requests:
   Read and write** (to comment the allocated id), and nothing else.
2. **Install it on this repository only**, so its reach is a single repo.
3. **Add the App to `main`'s ruleset bypass list** — it should be the only entry — so its
   installation token can push the renumber commit past branch protection.
4. **Generate a private key** and store it, with the App id, as the `AUTOMATION_BOT_PRIVATE_KEY` and
   `AUTOMATION_BOT_APP_ID` Actions secrets (scope them via an Environment tied to the `main` ref so no
   PR-triggered job can read them).

The workflow mints a short-lived (≈1 h) installation token from those secrets for checkout, push, and
`gh`; commits the App makes are signed and attributed to it, so every bypass push is auditable.

#### Tracking issues: who owns an open item (BE-0109)

Every **open** roadmap item — one whose `Status` is `Proposal` or `In progress` — has a GitHub
issue, and that issue's native **Assignees** are the single source of truth for who (if anyone) is
working on it. Because an item gets its issue the moment it exists as a proposal, an issue with **no**
assignee is exactly the "nobody has picked this up yet" signal the roadmap otherwise lacks. Two saved
filters turn the Issues list into the board:

- `label:roadmap-tracking no:assignee` — the **unclaimed backlog** (proposals and in-progress items
  with no one on them).
- `label:roadmap-tracking assignee:<user>` — one person's plate.

**Before you start an item, check its tracking issue** (search
`label:roadmap-tracking BE-NNNN in:title`); if it's unassigned, **self-assign it** when you pick the
work up — exactly as on any other GitHub issue. Don't close a tracking issue by hand: the sync does it.

The issues are created and closed automatically by the `roadmap-tracking-issues` workflow
(`scripts/sync_roadmap_tracking_issues.py`), which runs on `push: main` (paths `roadmaps/**`). The
lifecycle is a pure function of each item's current `Status` — an open item with no matching open
issue gets one; an issue whose item has since shipped (`Implemented`) or been shelved (`Proposal
(deferred)`) is closed — so the sync is idempotent and self-healing (BE-0043 / BE-0061), never
creating a second issue for an item on a re-run. GitHub is the source of truth for both facts —
ownership (Assignees) and whether an issue already exists (an open `roadmap-tracking` issue with the
item's `BE-NNNN` in its title) — so nothing is written back to the repo: the job needs only `issues:
write`, no commit to `main` and no bypass App. It runs on `main` (not the PR) and skips the `BE-XXXX`
placeholder, because a real-numbered issue can only be titled after `roadmap-id` allocates the number
on `main` (BE-0089); that allocation commit is itself a `roadmaps/**` push, which re-triggers the
sync and picks up the now-numbered item. The script calls the network (`gh`), so it never runs inside
`make check`; its read-only `--check` mode reports drift for a maintainer without mutating anything.

Each file follows the **Swift-Evolution proposal format** — a metadata block (`Proposal`,
`Author`, `Status`, `Topic`, plus the optional `Implementing PR`, the cross-item links `Related` /
`Superseded by`, and `Origin`) followed by `## Introduction` / `## Motivation` /
`## Detailed design` / `## Alternatives considered` / `## Progress` / `## References`. Fill what
you can and mark unknowns `TBD`. **The `-ja.md` file's title — its `# BE-NNNN — <title>` heading —
is written in Japanese, not copied verbatim from the English file's heading.** Translate it under
the same rule the rest of the prose follows: no forced translation, so an established term
(`selector`, `backend`) stays untranslated when a translation would read unnaturally, but the title
itself is Japanese. **`Detailed design` enumerates the work MECE** (mutually exclusive,
collectively exhaustive), and **`Progress` is a living section** (BE-0100) — a checklist mirroring
that breakdown (one `- [ ]` box per unit of work, ticked `- [x]` as it lands) plus a short
chronological PR-linked log — **kept current as work proceeds**: every PR that advances an item ticks
its boxes and adds a log entry in the same change, exactly as it fills `Implementing PR`. A
not-yet-started `Proposal` carries a single placeholder box; an `Implemented` item carries the
all-done checklist. `Related` / `Superseded by` are reciprocal — the superseding item lists the other
under `Related`, the superseded one names its successor under `Superseded by`. These two rules are
review-enforced, not machine-enforced: the gate confirms the `## Progress` section exists and the
fields keep their canonical order, but not that a breakdown is genuinely exhaustive or a box honest.
**Name the author by GitHub handle** —
`* Author: [@handle](https://github.com/handle)`, the account of whoever first authored the item
(for an AI-assisted draft, the person who drove and committed it). The **Status** field is the single
source of truth for the dashboard bucket an item appears under (BE-0078). It does **not** decide the
item's location: since BE-0159 every item lives in one flat `roadmaps/BE-NNNN-<slug>/` directory whose
path is permanent, so `Status` and directory can never disagree because the directory does not depend
on `Status` at all.

| Status | Dashboard bucket |
|---|---|
| `Implemented` | Implemented — shipped |
| `In progress` | In progress — accepted, actively being built |
| `Proposal` | Proposals — under consideration |
| `Proposal (deferred)` | Deferred — parked |

Every item, in every bucket, is browsable, grouped by Topic with live progress bars, on the
[roadmap dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html) — a page
`scripts/build_roadmap_dashboard.py` (BE-0094) generates from the same per-item metadata on every
docs build, published to GitHub Pages. `roadmaps/README.md` / `README-ja.md` carry no generated
status table of their own; the dashboard is the one place an item's status is browsable.

**The code decides the Status — a hard rule.** An item's `Status` tracks whether its implementation
exists, not a preference to keep the item reading as a forward-looking proposal. An item authored with
no code is `Proposal`; the PR that **ships its code** sets `Status` to `Implemented` (or `In progress`
when it lands a partial slice) in that same PR, ticks the matching `Progress` boxes, and records the PR
under `Implementing PR`. `Proposal` is never left standing on an item whose code has already shipped —
that is exactly the promotion the [`implement-be`](../.claude/skills/implement-be/SKILL.md) skill
performs, and it binds humans and agents alike. (The one exception is *authoring* a new item: an
`ideation`-style proposal that ships no code stays `Proposal`, since there is nothing implemented yet.)

As an item advances, **update its Status**; the dashboard picks up the new bucket on its next
regeneration, with nothing else to edit. The directory never moves (BE-0159): the same
`roadmaps/BE-NNNN-<slug>/` path holds the item for its whole life, so a promotion no longer rots any link
into or out of it — the concrete win over the folder scheme, which broke a link every time an item's
`Status` changed. **`make lint-roadmap`** (in `make check`) still guards cross-links: it fails if any
item's markdown link to another item does not resolve (a typo'd slug, a link to a renamed item), or if an
`Author` is not a `[@handle](…)` link; `make lint-roadmap ARGS="--fix"` rewrites a broken item link to
the target's current path. Milestones M1–M4 are `BE-0001`–`BE-0004` (implemented).

This is a hard rule agents must follow; the short form is in [`CLAUDE.md`](../CLAUDE.md).

## Documentation style (every document, both languages)

These rules apply to all documentation — English under `docs/` and the Japanese mirror under
`docs/ja/` — and to every future update, not just new files. Agents must follow them, and they
apply equally when reporting on or summarizing work.

- **Follow the [`document-writing`](../.claude/skills/document-writing/SKILL.md) skill.** It is the
  authoritative prose norm for every document here and every BE roadmap item, in both languages:
  the language-agnostic writing technique both languages share (draft top-down, state the
  contribution up front, reserve a sentence's end for its most important element, keep the verb near
  the subject, prefer the active voice, cut filler, and write one topic per paragraph with the
  argument moving in a single direction — paragraph writing). Invoke it *before* writing or revising,
  not after. It is the umbrella above two language layers: for English prose apply
  [`english-document-writing`](../.claude/skills/english-document-writing/SKILL.md) with it (serial comma,
  *that* / *which*, dashes, numbers, and the rest of the English mechanics), and for Japanese prose
  the [`japanese-document-writing`](../.claude/skills/japanese-document-writing/SKILL.md) skill (see below).
  The rules below are the specific expectations this section and those skills share.
- **Write natural prose.** A Japanese document must read as natural Japanese; an English document
  must read as natural English. A mirror conveys the same content naturally in its own language —
  it is not a word-for-word transliteration of the other.
- **No coined terms.** Use established, widely-used technical terms and ordinary words. Do not
  invent vocabulary, and do not stretch a word into a meaning it does not normally carry.
- **No forced or unnatural translation.** Use the conventional translation of a term. When
  translating it would read unnaturally, keep the original term instead — usually the English word
  (e.g. `selector`, `actuator`, `backend`, `assertion`) rather than a contrived literal rendering.
- **No omissions; be self-contained.** Follow the `document-writing` skill's
  [self-contained-prose norm](../.claude/skills/document-writing/SKILL.md#self-contained-prose-both-languages):
  a reader who has not read anything else in the repository must be able to follow the document start
  to finish, with every abbreviation spelled out and every term defined at first use, everywhere a term
  appears — including roadmap items, not only `docs/`.
- **Avoid anaphora that forces the reader to backtrack.** Follow the `document-writing` skill's
  [anaphora norm](../.claude/skills/document-writing/SKILL.md#minimize-anaphora-both-languages):
  repeat the noun instead of reusing a pronoun or demonstrative once the antecedent is more than one
  sentence back, crosses a paragraph, a list, or a heading, or could plausibly resolve to more than
  one nearby candidate.
- **Don't restate a cross-cutting norm — link it (BE-0284).** When a rule spans several documents
  (the gate's step list, the roadmap BE-ID lifecycle, the PR title-and-body shape, this
  documentation style), state it in full in **one** canonical home and point every other mention at
  that home with a short link, rather than copying the rule. Each restated copy is a second place a
  later edit can miss, and two copies drift into contradiction over time. The short, load-bearing
  prime directives are the deliberate exception: a document that must be self-contained on first
  read keeps a short, accurate copy rather than sending a first-time reader elsewhere.
- **Link a glossary term on first use rather than re-explaining it (BE-0286).** When prose in a BE
  roadmap item or a `docs/` page uses a term defined in [`glossary.md`](glossary.md) in its
  Bajutsu-specific sense, link its first substantive mention to the term's glossary entry
  (`glossary.md#anchor`, or `ja/glossary.md#anchor` on the Japanese side) instead of restating the
  definition inline. The anchor is the section that defines the term — for example
  [`glossary.md#driver-backend-actuator-platform`](glossary.md#driver-backend-actuator-platform) for
  any of *driver* / *backend* / *actuator* / *platform*. This is the term-level companion to the
  cross-cutting-norm rule above: point at the one canonical definition rather than growing a second
  copy that can drift. It is a review-time norm, not a CI gate — most glossary terms (*step*,
  *target*, *app*, *platform*) are also ordinary English words, so deciding whether a given mention
  invokes the Bajutsu-specific sense needs human judgment, which prime directive 1 keeps off the
  `run` / CI path. [`drivers.md`](drivers.md) is the model to follow.
- **Japanese prose follows the `japanese-document-writing` skill.** Whether you write the Japanese side
  fresh or translate the English mirror into `docs/ja/` (or a roadmap `*-ja.md`), apply
  [`japanese-document-writing`](../.claude/skills/japanese-document-writing/): it is the authoritative style
  for Japanese prose in this repo, and a translation must read as natural Japanese under those norms,
  not a literal rendering of the English. It sits beneath the
  [`document-writing`](../.claude/skills/document-writing/SKILL.md) umbrella (above); apply both for Japanese
  prose.
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

**Migration is phased and incremental** ([BE-0065](../roadmaps/BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md)):
the site renders today from the existing prose docstrings (typed signatures already give a useful
reference); public-API docstrings move to Google style module by module in small PRs, and the
scoped `ruff` `D` enforcement and Pages hosting land after. **Don't rewrite a whole module's
docstrings as a side effect of an unrelated change** — keep each migration its own small PR.

Build the reference locally with `make docs` (or `make docs-serve` to preview); it needs the `docs`
extra.
