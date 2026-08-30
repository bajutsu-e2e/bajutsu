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

## The coverage ratchet (BE-0385)

The gate enforces two coverage floors, and neither one moves on its own.

The **total floor** is `fail_under` in `[tool.coverage.report]` ([`pyproject.toml`](../pyproject.toml)).
`make test` fails when coverage over the whole `bajutsu` package falls below the total floor. Keeping
the floor in coverage.py's own configuration, rather than in a `--cov-fail-under` flag on the
`Makefile`'s pytest line, gives every reader of the floor one declarative source instead of a value
scraped from a shell recipe, where a later edit could change the floor without anyone noticing.

A single number over the whole package has two weaknesses, and the first is that the number drifts
below what the suite actually delivers. To catch the drift, `make lint-pr` runs a **drift advisory**
that compares the measured total against the floor and prints a reminder once the gap passes two
points. The advisory never fails the build, because failing it would block a pull request that
happens to add well-tested code, purely for widening the gap further. When the reminder appears, raise `fail_under`
and commit the new number.

The second weakness is that a single number hides where coverage is weak. The **per-file floors**
close that gap: they live in [`coverage-floors.json`](../coverage-floors.json), a committed snapshot
recording the branch coverage each source file was last measured at, and `make check` runs
`make lint-coverage-floors`, which fails when any file drops below its own recorded number. A file
can fall from 65% to 40% while the total stays above the total floor, because the rest of the tree
absorbs the loss; only the per-file floors notice it.

The per-file check only ever blocks a drop. A rise passes without anyone touching the snapshot,
because failing a pull request for having improved coverage would punish the behavior the
ratchet exists to encourage. The check also never writes the snapshot, mirroring the `format` /
`format-check` split: a gate that rewrote the bar it enforces would ratchet in both directions.

```bash
make coverage-floors   # rewrite coverage-floors.json to what the suite just measured
```

Run that command deliberately and commit the result once coverage has risen. The same command is
the escape hatch for a drop you have decided to accept. It prints rises and drops separately, so
you see an accepted drop before committing it.

A file with fewer than ten statements carries no floor at all. One missed branch would move such a
file's percentage by whole points, failing the gate on measurement noise rather than on a
regression. Coverage can also differ slightly between environments — a different operating system,
or a machine missing a tool some test needs — so a floor recorded on one machine can read as a drop
on another. `make coverage-floors` is the answer there too.

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
automatically via [`.claude/hooks/session-start.sh`](../.claude/hooks/session-start.sh).

**`git push --no-verify` is strictly forbidden, with no exception for emergencies.** Bypassing the
hook does not save time — it only defers the same `make check` result from your terminal to CI,
after the push has already reached a shared branch other sessions rebase against. If the hook fails
on what looks like a false positive rather than a real regression — for example, `lint-skills`
reddening from a concurrent session's worktree state under `.claude/worktrees/` — fix the underlying
cause, or reproduce the check in a clean sibling worktree to confirm before pushing. Never use
`--no-verify` to force a push through a failing gate.

No mechanism inside git can enforce that rule by itself. `--no-verify` skips every hook
unconditionally, at the point git parses the flag, before `.githooks/pre-push` or any other hook
ever runs. A `push` git alias cannot close that gap either: git's own documentation states that
"aliases that hide existing Git commands are ignored" (`git help config`), and testing it against
this repo's own `push` confirms it — `git push` and `git push --no-verify` alike ignore the alias
and run the built-in command unchanged. The only point left that ever sees the raw flag before git
acts on it is command-name resolution itself, which only a real `git` wrapper controls.

`scripts/install-no-verify-guard.sh` installs that wrapper: a `git()` shell function, appended to
your shell rc, that refuses `--no-verify` on `push` inside any repository whose toplevel carries
[`.githooks/no-verify-guard-marker`](../.githooks/no-verify-guard-marker) — this one included, and
never elsewhere. `make setup` runs it automatically, best-effort, on a fresh checkout — the whole
point is that protection starts the moment you begin developing, not after a separate step a
session under time pressure can forget. `make git-guard-install` runs the same installer standalone,
for a clone set up before this existed, after the installed block was deleted, or with a non-default
rc file via `BAJUTSU_GUARD_RC_FILE`.

Treat it as a personal convenience, not a repository-wide guarantee: removing the block, or calling
`command git push --no-verify` directly, still gets through. CI's independent `make check` re-run
before merge is what makes the rule hold regardless of what ran locally — this installer only saves
the round trip to that gate.

The same `core.hooksPath` also wires a tracked **commit-msg hook**
([`.githooks/commit-msg`](../.githooks/commit-msg), BE-0069): it blocks a commit whose subject isn't
a scoped conventional subject (`type(scope): …`, or `docs: …`), catching the mechanical convention at
commit time instead of in review. It is deliberately narrow — merge / revert / fixup / squash commits
pass, and it no-ops when `uv` isn't on PATH; bypass a one-off with `git commit --no-verify`.

When you change behavior, change a test with it — the suite is the contract that protects every
other session from your change.

The short form of this rule is in [`CLAUDE.md`](../CLAUDE.md).

## Block a secret before it's committed

`core.hooksPath` also wires a tracked **pre-commit hook**
([`.githooks/pre-commit`](../.githooks/pre-commit)) and a **prepare-commit-msg hook**
([`.githooks/prepare-commit-msg`](../.githooks/prepare-commit-msg)), both backed by
[gitleaks](https://github.com/gitleaks/gitleaks): the pre-commit hook scans every staged file for
a prohibited pattern (an AWS credential, a GitHub token, an Anthropic API key, a pasted
private-key block) and refuses the commit on a match; the prepare-commit-msg hook does the same
for a merge that would otherwise pull a secret in from another branch's history. The existing
commit-msg hook ([`.githooks/commit-msg`](../.githooks/commit-msg)) gained the same scan for the
message body itself, alongside its scoped-subject check — git runs only one script per hook name,
so both checks share that one file.

The patterns themselves live in a tracked file, [`.gitleaks.toml`](../.gitleaks.toml) — an
ordinary tracked file gitleaks reads directly, unlike a tool that stores its configuration in local
`git config` (a per-clone setting clone/pull never carries over, the same problem `core.hooksPath`
has), so there's no per-clone registration step to self-heal. It extends gitleaks' own built-in
ruleset (AWS credentials, GitHub tokens, PEM private keys) with the two shapes specific to this
repository: an Anthropic API key or OAuth token, and the `BAJUTSU_SERVE_TOKEN` /
`GRAFANA_ADMIN_PASSWORD` deploy-time secrets from `deploy/self-host/`. Everything degrades
gracefully when `gitleaks` isn't installed yet — the hooks and `make lint-secrets` (below) skip
with a notice rather than blocking a commit or the gate; install it with `brew install gitleaks`
(macOS, also in the [`Brewfile`](../Brewfile)) or from a
[release binary](https://github.com/gitleaks/gitleaks/releases). A string that legitimately
matches a pattern but isn't a secret (a fixture, a documented placeholder, a shell variable
reference) can be exempted with a scoped `[[allowlists]]` entry in `.gitleaks.toml` — gitleaks'
own escape valve — rather than by loosening a pattern.

A local hook only helps a clone that has one wired and isn't bypassed with `--no-verify`, so CI
runs the same scan independently: `make lint-secrets` re-scans every tracked file and is folded
into `make check`, mirroring the way `make check` itself runs both locally and in CI so neither a
skipped `make setup` nor a `--no-verify` commit slips a secret past review.

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

`make hooks` also self-heals three local git settings that take the sting out of the conflicts that
remain (BE-0043), so you don't have to configure them by hand:

- a **`uv.lock` merge driver** ([`scripts/merge-uv-lock.sh`](../scripts/merge-uv-lock.sh), mapped via
  [`.gitattributes`](../.gitattributes)) that **regenerates the lockfile from `pyproject.toml`** on a
  conflict instead of line-merging resolver output. If `pyproject.toml` itself conflicts, `uv lock`
  fails and git leaves `uv.lock` conflicted — resolve `pyproject.toml` first, then re-merge.
- an **APM generated-output merge driver**
  ([`scripts/merge-apm-generated.sh`](../scripts/merge-apm-generated.sh), mapped the same way) that
  **regenerates from `.apm/skills/`** on a conflict, for the same reason (BE-0390). It covers both
  committed products of `apm install`: `apm.lock.yaml` and the deployed `.claude/skills/**` tree.
  Neither is hand-resolvable — the lockfile is a flat list of per-file SHA-256 hashes, and both
  carry generated bytes whose correct value sits on neither side of the merge, being whatever a
  re-install writes from the *merged* source. The deployed tree is where most of the volume lands
  (fourteen `SKILL.md` files and five `references/` files against the lockfile's few hundred lines),
  and it is the more dangerous of the two by hand: a resolution there looks like it worked while
  leaving a deployment its source no longer matches, which resurfaces later as a `make lint-skills`
  drift failure nobody connects to the merge. So **you hand-resolve only `.apm/skills/`** — the
  source — and let the driver handle the rest. What it writes is provisional, exactly as the
  `uv.lock` driver's output is: git runs every merge driver before it writes any merged file, so the
  `apm install` inside reads the pre-merge working tree. `make lint-skills` closes that gap the way
  `uv lock --check` backs the `uv.lock` driver — after resolving a skill conflict, run `make skills`
  and commit the lockfile and the deployed tree it rewrote.
- **`rerere`** (reuse recorded resolution), so a conflict you have resolved once replays
  automatically the next time the same conflict appears.

Like `core.hooksPath`, these are per-clone local git settings that clone/pull never carry over, so
`make check` / `make setup` re-wire them every time.

The short form of this rule is in [`CLAUDE.md`](../CLAUDE.md).

## Isolate concurrent sessions with worktrees

Two agents must never edit the same checkout. Give each session its own
[worktree](https://git-scm.com/docs/git-worktree) + branch, all sharing one `.git`.

How you create that worktree depends on the agent environment. Claude Code may use its own
worktree tooling, which places trees under `.claude/worktrees/`. Other environments create
topic worktrees through `make worktree` (below). That includes Cursor, Codex, a plain shell,
and future agents. Do not call `git worktree add` with a hand-picked path in those
environments. The Makefile target owns fetch, branch naming, path layout
(`../bajutsu-<topic>`), and `make setup`.

```bash
# from the main checkout (required outside Claude Code)
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

## Agent skills: one source, one deployment (BE-0390)

A skill is one source directory, `.apm/skills/<name>/`, holding a `SKILL.md` and — where the
procedure has depth the body shouldn't carry — a `references/` directory. The `SKILL.md` carries
both the procedure and the Claude Code tools it uses; there is no separate per-host adapter to keep
in step, and no second hand-maintained copy of the procedure — the committed deployment below is
generated.

[APM](https://microsoft.github.io/apm/) resolves the tree. The root `apm.yml` names the package and
pins `targets: [claude]`, so APM deploys only to `.claude/skills/` and never writes the trees it
produces for other harnesses. `apm.lock.yaml` records a SHA-256 for every deployed file.

```bash
make skills        # uv run apm install --no-policy — deploy .apm/skills/ to .claude/skills/
make lint-skills   # uv run python scripts/audit_skills.py — fail on drift (part of `make check`)
```

`lint-skills` goes through a wrapper rather than calling `apm audit` directly. APM governs the
whole of `.claude/`, which is also where Claude Code parks a concurrent session's worktree, so a
direct audit walks every other session's checkout and fails over vendored files this repository
never wrote. [`scripts/audit_skills.py`](../scripts/audit_skills.py) audits a scratch mirror of
the files git sees instead — what a fresh clone holds plus the work in hand.

**Both sides are committed** — the source and the deployed `.claude/skills/` tree — so a fresh
clone has a working skill set before anyone installs APM. The cost is that each skill's bytes are
tracked twice; keeping `SKILL.md` inside APM's size budget (roughly 500 lines and 5,000 tokens)
bounds it.

So: edit the source, run `make skills`, and commit what it rewrote. A `make skills` that changes
nothing leaves the lockfile untouched: APM 0.28.0 preserves the `generated_at` timestamp of an
existing `apm.lock.yaml` and stamps a new one only when it writes the lockfile from scratch. A
timestamp-only diff therefore means the lockfile was regenerated rather than updated; the audit
ignores the line, so discarding that diff is fine.

`make lint-skills` catches the drift either way round — a deployed file edited by hand, and a source
edit whose `make skills` was forgotten. Unlike `lint-actions` and `lint-secrets`, it has **no skip
branch**: `apm-cli` is a `dev` dependency pinned in [`pyproject.toml`](../pyproject.toml), so
`uv sync --group dev` installs it on any clone and the check cannot pass by not running. The pin is
exact rather than a floor, and `uv.lock` records it, so the version writing `apm.lock.yaml` is the
same locally and in CI. (The bare `0.28.0` elsewhere on this page records what the behavior was
*verified* against, which no bump changes.)

The check stays clear of prime directive 1: `apm audit` compares recorded hashes against the working
tree, so no language model reaches the gate. `--no-policy` keeps it offline as well — organization
policy discovery would otherwise reach `api.github.com`. Every `apm install` this repository runs
passes the same flag, for two reasons: it puts the deploy and the audit that replays it under one
configuration, and it makes "resolves from the working tree, no network" true of the deploy as
well. Without the flag a disconnected `apm install` still succeeds — 0.28.0 warns that it could not
reach the policy and proceeds — but it is a network round trip on every run, and the warning reads
like a failure.

**Renaming or retiring a skill needs no extra step.** `apm install` prunes the deployment it no
longer owns, so `git mv`-ing a source directory and running `make skills` deletes the old
`.claude/skills/<name>/` for you. Forget the `make skills` and the gate still catches it: the
renamed source has no deployment, which `apm audit` reports as drift.

**Where the depth goes.** `SKILL.md` points at a `references/` file *at the step that needs it*, so
a session loads only what it uses. A norm set is the exception: `document-writing`,
`english-document-writing`, and `japanese-document-writing` are applied in full rather than step by
step, so where one splits, its `SKILL.md` opens by telling the host to load every `references/` file
before drafting. No step *needs* a norm, so a host left to load on demand could apply a subset and
still believe it followed the skill.

The short form of this rule is in [`CLAUDE.md`](../CLAUDE.md#agent-skill-layout).

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
string: `check` (`ci.yml`), `E2E (iOS)` (`ios-e2e.yml`), and `require two approvals for BE proposals`
(`roadmap-proposal-approvals.yml`). Renaming one of those job names without editing the ruleset's
`required_status_checks` in the same instant strands every open PR on a check that no longer
reports, silently blocking merges. Ruleset edits are out-of-repo admin state a normal PR can't
carry, so leave those three names as they are; a deliberate rename must be paired with a human admin
edit to the ruleset.

### The per-platform E2E job set

Each backend has one on-device E2E workflow — [`ios-e2e.yml`](../.github/workflows/ios-e2e.yml)
(macOS / XCUITest), [`android-e2e.yml`](../.github/workflows/android-e2e.yml) (Linux+KVM /
adb), [`web-e2e.yml`](../.github/workflows/web-e2e.yml) (Linux / Playwright). They share a job
vocabulary so a reviewer reads the same shape across platforms and a new backend has a shape to
match, rather than one bundled pass/fail per lane. The functional heart every lane carries is
**smoke** — a real `bajutsu run` over the showcase scenarios on the real backend, asserting
deterministic pass/fail ("does bajutsu drive this platform?"). Other jobs verify a specific
capability, and a platform carries one only where it applies:

| Job | What it verifies | iOS | Android | Web |
|---|---|:-:|:-:|:-:|
| `smoke` | functional `bajutsu run` over the showcase | ✓ (folded into `run`) | ✓ | ✓ |
| `golden` | element-tree (BE-0006) matches the committed baseline | ✓ | ✓ | — |
| `visual` | pixel VRT against the committed baseline | ✓ | ✓ | — |
| `conformance` | driver contract (BE-0114) on the real backend | ✓ | ✓ | ✓ |
| `codegen` | native-test output compiles and runs against the real backend | ✓ | ✓ | ✓ |
| `gestures` | multi-touch (pinch/rotate) | ✓ (in `run`) | — | — |
| `fallback` | resident vs `uiautomator dump` read channels agree (BE-0245) | — | ✓ (step) | — |

Two rules keep the set honest. **Every lane is required, per-lane.** A required status check is a
job `name:` the ruleset pins (above); each lane carries its own always-reporting aggregator
(`E2E (iOS)`, `E2E (android)`, `E2E (web)`, BE-0279) whose heavy jobs a `changes` job path-gates, so an
unrelated PR is neither run nor blocked — per-lane aggregators (rather than one aggregator across backends) keep
attribution: a red check names the backend that broke. **Host-specific or upstream-fragile checks
stay off the required gate.** `visual` is a pixel compare whose baseline varies by renderer, and the
element-tree `golden` can drift with an upstream on-device dependency
out of our control — both run per PR as signals but are excluded from each
aggregator's `needs:`, so a drift surfaces without blocking merges. `codegen` follows the same
signal-then-required path on a per-platform schedule: iOS's and web's codegen jobs already gate
their aggregator, while Android's (BE-0294) still lands as a per-PR signal and joins `needs:` only
once it proves stable.

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

### Where Claude Code applies the default: skill frontmatter

Each skill declares its tier as a `model:` field in its `SKILL.md` frontmatter — in the source at
`.apm/skills/<name>/SKILL.md`, whose frontmatter `apm install` deploys verbatim to the tree linked
below (BE-0390). The Claude Code harness picks the right model when the skill runs. The default remains
overridable:

- [`implement-be`](../.claude/skills/implement-be/SKILL.md) → `opus` (Heavy)
- [`propose-and-build`](../.claude/skills/propose-and-build/SKILL.md) → `opus` (Heavy) — it
  implements product code in its Phase B, so it carries the same tier as `implement-be`.
- [`fix-issue`](../.claude/skills/fix-issue/SKILL.md) → `opus` (Heavy) — it ships product code for a
  plain GitHub issue, so it carries the same tier as `implement-be` for the same reason.
- [`ideation`](../.claude/skills/ideation/SKILL.md) → `sonnet` (Medium)
- [`document-writing`](../.claude/skills/document-writing/SKILL.md) → `sonnet` (Medium)
- [`english-document-writing`](../.claude/skills/english-document-writing/SKILL.md) → `sonnet` (Medium)
- [`japanese-document-writing`](../.claude/skills/japanese-document-writing/SKILL.md) → `sonnet` (Medium)
- [`record-issue`](../.claude/skills/record-issue/SKILL.md) → `sonnet` (Medium) — it files a minor
  finding as a GitHub Issue
  ([BE-0384](../roadmaps/BE-0384-record-issue-skill/BE-0384-record-issue-skill.md)). Classifying the
  finding, weighing a duplicate search's candidates, and drafting a body from an issue template are
  prose judgments the light tier handles poorly, and the skill writes no product code, so the heavy
  tier would be waste.
- [`roadmap-filter`](../.claude/skills/roadmap-filter/SKILL.md) → `haiku` (Light) — a read-only
  survey of the roadmap by `Status` (BE-0162): it wraps `make roadmap-status STATUS="…"` so a
  session lists just the items in one status (e.g. every open `Proposal`), with each item's file
  path to open next, instead of paging through the dashboard's rendered HTML or opening each item
  file to check its `Status`.

Most light-tier chores aren't skills, so that tier is otherwise reached interactively or by subagent
delegation, below — `roadmap-filter` is the exception, since its whole job is one light,
deterministic lookup. `tests/test_skill_models.py` checks that each skill's `model:` — in the
`.apm/skills/` source as well as the deployed tree — is a known, valid id, so a typo fails the gate locally instead of silently falling back.

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

Passing `model` at spawn time is a thing to remember, not a mechanism, and it gets forgotten.
A discovery fan-out that nobody routed runs at whatever the driver is paying for.
[`.claude/agents/scout.md`](../.claude/agents/scout.md) closes that gap for discovery.
The agent pins `fable` in its own frontmatter and holds its reply to paths and line ranges.
Naming `scout` buys the cheap path without anyone choosing it.

Deliberately **not gate-enforced**: which model a session used isn't recoverable from the diff, and
hard-pinning would remove the human's judgment to upshift when a "light" task turns out hard. This
follows the same "procedures as commands, advisory not policy" precedent as the rest of the
contributor workflow ([BE-0069](../roadmaps/BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)).

### The local self-review's two roles (BE-0347)

The pre-push self-review that mirrors the CI review contract runs as two roles on two models, not as
one agent on one. A **`fable`** review/plan pass judges the diff against
[`.github/claude-review-prompt.md`](../.github/claude-review-prompt.md) and writes fix instructions;
it never edits a file. A separate implement pass applies those instructions, on **`sonnet`** when the
fix stays inside `roadmaps/` or `docs/` and **`opus`** when it touches product code — the same
task-weight rule the tier table above applies everywhere else. The canonical procedure lives in
[`ideation`](../.apm/skills/ideation/SKILL.md) step 5, which `pr-followup`,
`propose-and-build`, and `implement-be` all run rather than restate.

The split is the point; the two models make it concrete. An agent that fixes the finding it raised
has every incentive to patch enough to silence its own comment, leaving something adjacent
for the next cold look to raise — a likely reason the live reviewer kept finding something new after
each push.

This does not contradict the table's **PR review → medium (`sonnet`)** entry. That row names the task
of reviewing someone else's pull request when asked, weighing a whole change on its merits. The
review/plan role is narrower: it classifies findings against a fixed contract inside the local loop,
and never reviews a pull request as a whole. The two rows answer different questions.

**The CI trigger narrows to match.** Because the local pass now converges before a push, the
[`claude-review`](../.github/workflows/claude-review.yml) workflow no longer re-reviews on every
push. It runs automatically when a pull request opens or reopens, and every later pass is requested
with an `@claude review` comment — typically by `pr-followup` itself, once its own self-review comes
back clean. The workflow stays rather than being removed, because two paths never reach the local
pass and would otherwise get no review at all: a fork pull request, whose `pull_request` run carries
no secrets by design, and any commit pushed outside these skills.

## Authoring and shipping roadmap items: the three skills

Turning an idea into shipped code runs through three skills that form a triangle — author,
ship, or both:

- [`ideation`](../.apm/skills/ideation/SKILL.md) — **author only.** A sounding board that
  shapes an idea into a BE proposal and stops at the `roadmaps/` files (never touches product
  code). The proposal carries the `BE-XXXX` placeholder; CI allocates the real id after the PR
  merges.
- [`implement-be`](../.apm/skills/implement-be/SKILL.md) — **ship a numbered item.** Takes an
  already-allocated `BE-NNNN`, treats its proposal as the spec, implements it with tests, flips
  the item to `Status: Implemented`, and proves `make check` green.
- [`propose-and-build`](../.apm/skills/propose-and-build/SKILL.md) — **both, in one PR.**
  Composes the other two for a small, settled item the author is ready to build now: it authors
  the proposal *and* implements it on a single branch, landing them as one BE-creation PR that
  carries the roadmap item, the code, and the tests together. The item keeps the `BE-XXXX`
  placeholder and reaches `Status: Implemented` in the PR — `Status` and the PR number do not
  depend on the id — and CI allocates the real `BE-NNNN` on merge, rewriting the placeholder
  inside the item's own files
  ([BE-0089](../roadmaps/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)).
  The one invariant is that the placeholder id appears nowhere but the item's own files, because
  the allocator rewrites only that directory.

**Picking one.** The serial `ideation` → merge → allocate → `implement-be` path is the default:
it forces a design to clear review before code is written, and it keeps the `BE-NNNN` sequence
contiguous by only spending a number on an item that ships
([BE-0089](../roadmaps/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)).
Reach for `propose-and-build` only when that path's latency is pure overhead — a small,
well-scoped item whose design the author does not expect review to reshape. One PR fuses the
design checkpoint with code review, so merging it accepts the proposal and the implementation at
once; that is honest for a settled design but costs a rework if review reshapes the proposal, so
fall back to the serial path whenever a design is genuinely uncertain.

**When the work never earns a roadmap item at all**, none of the three applies, and the sibling path
is [`fix-issue`](../.apm/skills/fix-issue/SKILL.md)
([BE-0380](../roadmaps/BE-0380-fix-issue-skill/BE-0380-fix-issue-skill.md)). That skill ships a
plain GitHub issue — a small bug, a papercut, or a scoped improvement — through `implement-be`'s own
implementation, review, gate, and follow-up steps. Two things differ: `fix-issue` claims the work
through the issue's native assignee field, and closes the loop with a `Closes #<N>` line in the PR
body rather than a `Status` flip. `fix-issue` also judges the boundary itself — a fix that turns out
to need a design decision escalates to `ideation` or `propose-and-build` instead of shipping.

**When the finding has no issue yet**, the entry point one step earlier is
[`record-issue`](../.apm/skills/record-issue/SKILL.md)
([BE-0384](../roadmaps/BE-0384-record-issue-skill/BE-0384-record-issue-skill.md)). That skill only
files: it classifies a minor bug or a small, bounded improvement, searches the open issues and the
roadmap for a duplicate, drafts a body from this repository's own issue templates, and creates the
issue once the invoker explicitly approves the draft. It ships no fix and opens no branch. What
`record-issue` files is what `task-select` later ranks and `fix-issue` later ships, so the three
skills carry a finding noticed in passing all the way to a merged fix instead of losing it or
letting it swell the change in hand. Any skill may call `record-issue` as a sub-step —
[`pr-followup`](../.apm/skills/pr-followup/SKILL.md) is the caller wired today — and no caller may
waive the approval step. When that call happens unattended, inside `implement-be`'s hands-free loop,
`record-issue` files nothing: it returns the finished draft in the iteration's summary, in a field
kept separate from an escalation so the loop runs on, and the human approves the draft on a later
turn.

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
[`claude-review`](../.github/workflows/claude-review.yml) workflow. It reviews when a PR opens or
reopens, and after that whenever someone requests a pass (BE-0347 — see **On demand** below; it
deliberately does **not** re-review on every push). It reviews against the
[`.github/claude-review-prompt.md`](../.github/claude-review-prompt.md) contract, and posts inline
line-level comments (with `suggestion` blocks where a fix is mechanical) — inline findings only, no
top-level summary, since a fresh summary from each of a PR's runs would leave stale,
contradictory overviews on it. To stop it repeating the same findings across those runs while still
missing nothing, each run reads the **full diff** (so no changed line goes unreviewed) and is also
handed every finding **already posted** on the PR (via the API, no PR-head checkout), with instruction
never to re-post one — it dedupes by suppressing repeats, not by narrowing what it looks at, so a
genuinely new issue is still raised even on code an earlier run overlooked. Until
a credential is provisioned the workflow is a dormant green no-op — it posts nothing and never
blocks — so no review appearing on a PR yet just means the Environment isn't configured. The
prompt points the reviewer at *this repository's* contract — the three
[prime directives](../CLAUDE.md#prime-directives-do-not-violate), design/maintenance debt with a
concrete future-bug cost, security, silent failures, and the two prose-quality lenses — so it
catches what a generic reviewer cannot. The floor is deliberately functional-impact only: a
finding whose only cost is style, naming, or documentation-formatting taste (docstring format,
bilingual-doc sync, terminology consistency, roadmap-link hygiene) is left to human review instead,
since posting it would cost the author a fix-and-rerun cycle for no functional gain. It runs on
Opus (not the action's default Sonnet) for sharper severity triage, and posts only `issue`,
`suggestion`, and `question` findings — `nitpick` and `praise` are suppressed so an advisory review
doesn't accrete low-value noise across a PR's runs.

It is **advisory, never a gate.** It is deliberately not a required status check, and its job result
is decoupled from its findings (a review that found issues is a *successful* review, so the job goes
red only on an infrastructure failure). The deterministic `check` / `E2E` gates remain the only
merge arbiters — this is a reviewer, not a judge (prime directive 1). Treat its comments exactly as
you would any reviewer's, under the reply rules above.

- **On demand.** Every pass after the open/reopen review is requested: a maintainer or collaborator
  writes `@claude review` on the PR (or in a review-thread reply), and `pr-followup` does the same
  automatically once its own self-review of a pushed fix comes back clean. A requested pass is the
  same review as an automatic one — it gets the same contract, the same severity floor, and the same
  prior-findings dedup — so a thread reply also runs a full pass rather than answering that thread
  alone. This path is gated to trusted actors (OWNER / MEMBER / COLLABORATOR), because comment events
  run with repo secrets even on fork PRs, so `@claude review` from anyone else is ignored. GitHub
  shows nothing on the PR when a request is dropped that way, so whoever asks should confirm the
  review ran rather than read the silence as a clean review. Check the **job**, not the run: the gate
  is a job-level `if:`, so a dropped request still creates a run that completes green with its
  `claude review` job `skipped`. Find the run with `gh run list --workflow "Claude review" --event
  issue_comment --user <account> --created ">TIMESTAMP"` (a timestamp captured before posting; a
  comment-triggered run executes against the default branch, so filtering by the PR branch can never
  match it), then read that job's conclusion with `gh run view <id> --json jobs`.
- **Forks.** A plain `pull_request` event from a fork does not expose secrets (by GitHub's design),
  so auto-review covers same-repo `claude/<topic>` / `<user>/<topic>` branches; a fork PR is
  reviewed on demand by a maintainer instead.
- **Migration off Copilot (manual, out-of-repo).** The workflow lands alongside Copilot's review so
  the two run in parallel and can be compared; once Claude Code's review has proven itself, a
  maintainer **disables Copilot's automatic review in the repository / organization settings**.
  That is admin state a PR cannot carry (the same shape as the branch-protection ruleset edits
  BE-0122 and BE-0089 call out), so it is an explicit manual step.

### The companion PR for wording-only findings (BE-0343)

Two of the reviewer's lenses judge nothing but wording: Japanese prose quality, and its English
`docs/*.md`/roadmap-prose counterpart. A finding from either carries the decoration
`(non-blocking, prose)` rather than the plain `(non-blocking)`, and no other lens ever does. That
marker is read mechanically by a second job in the same
[`claude-review`](../.github/workflows/claude-review.yml) workflow, ordered after the review itself:
for each such finding that carries a `suggestion` block, the job applies that block's exact text to a
companion branch (`prose-fix/pr-<N>`) rebuilt from your pull request's current head, and opens a
small companion pull request **based on your branch**. It then replies on the finding's thread naming
that pull request, and resolves the thread.

**What you do with one:** review and merge it like any other small pull request, on its own schedule.
Merging it lands the wording fix on your branch as an ordinary push. The point of the detour is that
your pull request never pays a full CI cycle for a change with no behavioral risk, and — unlike
deferring the fix until after merge — the corrected wording is there while a human is still reading
your diff. You do not need to reply to the prose threads yourself; the job already has.

The properties worth knowing:

- **No LLM runs in this job.** The reviewer drafted the fix at review time; this only decides whether
  that exact text still applies, by comparing the finding's own `diff_hunk` against the file at your
  current head. A suggestion whose line you have edited since is **refused and listed in the
  companion pull request's body**, never guess-applied — as is every other finding it cannot apply,
  since a job that hides a failure is worse than none.
- **Two gates bound what it can write**, because it writes under the automation App's token. It
  applies a finding only when GitHub says the *reviewer account* posted it — the `🤖 **Claude Code**`
  prefix and the marker are text any commenter could type, so they classify a finding rather than
  authenticate it — and only when the path is a `docs/` or `roadmaps/` markdown file. A finding
  mismarked onto product code is therefore refused, and stays an ordinary inline comment for you to
  weigh.
- **The branch is rebuilt fresh every run**, from your current head, and every currently posted prose
  finding is reapplied — not just the new ones. So a rebase on your branch needs no action on the
  companion: nothing carries over between runs.
- **It never clobbers you.** The forced update is guarded exactly as the refreshers below guard
  their rolling branches: it moves the companion branch only when the bot's own prior commit sits at
  the tip. Your own push onto that branch therefore makes the next run skip rather than overwrite it.
- **It never checks out your branch.** Your branch reaches the job as data alone: the files a finding
  names, read back at the head commit the run resolved. The job then builds the companion commit
  through GitHub's Git Data endpoints rather than in a working tree. CodeQL's
  `actions/untrusted-checkout-toctou` query flags a job that checks out a contributor's branch while
  holding a privileged token; the only branch this job checks out is the default one, carrying the
  script it runs.
- **Same-repo branches only.** The job writes to a pull-request branch while holding the automation
  App token, so it is confined to branches only an owner, member, or collaborator can push. A fork
  pull request's wording findings stay unautomated, the same gap the review itself accepts for forks.
- **It closes itself out.** Once your pull request merges, its branch is deleted, and GitHub
  retargets any still-open companion pull request onto `main` — merging it after that point is an
  ordinary, independent merge.

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
issue gets one; an issue whose item has since shipped (`Implemented`) or been shelved (`Deferred` or
`Rejected`) is closed — so the sync is idempotent and self-healing (BE-0043 / BE-0061), never
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
| `Deferred` | Deferred — parked, with a named condition that would revive it |
| `Rejected` | Rejected — decided against, with no condition expected to reopen it |

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
that is exactly the promotion the [`implement-be`](../.apm/skills/implement-be/SKILL.md) skill
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

- **Follow the [`document-writing`](../.apm/skills/document-writing/SKILL.md) skill.** It is the
  authoritative prose norm for every document here and every BE roadmap item, in both languages:
  the language-agnostic writing technique both languages share (draft top-down, state the
  contribution up front, reserve a sentence's end for its most important element, keep the verb near
  the subject, prefer the active voice, cut filler, and write one topic per paragraph with the
  argument moving in a single direction — paragraph writing). Invoke it *before* writing or revising,
  not after. It is the umbrella above two language layers: for English prose apply
  [`english-document-writing`](../.apm/skills/english-document-writing/SKILL.md) with it (serial comma,
  *that* / *which*, dashes, numbers, and the rest of the English mechanics), and for Japanese prose
  the [`japanese-document-writing`](../.apm/skills/japanese-document-writing/SKILL.md) skill (see below).
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
  [self-contained-prose norm](../.apm/skills/document-writing/SKILL.md#self-contained-prose-both-languages):
  a reader who has not read anything else in the repository must be able to follow the document start
  to finish, with every abbreviation spelled out and every term defined at first use, everywhere a term
  appears — including roadmap items, not only `docs/`.
- **Avoid anaphora that forces the reader to backtrack.** Follow the `document-writing` skill's
  [anaphora norm](../.apm/skills/document-writing/SKILL.md#minimize-anaphora-both-languages):
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
  [`japanese-document-writing`](../.apm/skills/japanese-document-writing/SKILL.md): it is the authoritative style
  for Japanese prose in this repo, and a translation must read as natural Japanese under those norms,
  not a literal rendering of the English. It sits beneath the
  [`document-writing`](../.apm/skills/document-writing/SKILL.md) umbrella (above); apply both for Japanese
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

## Cite an argument instead of repeating it (comments and docstrings)

Bajutsu's comments already favor *why* over *what* (see *Conventions* in [`CLAUDE.md`](../CLAUDE.md));
this section adds the discipline for *where the why lives*. When a comment's reasoning already
exists as an argued case in a roadmap item or a `docs/` page, the comment should point to it, not
restate it. A comment that repeats an argument drifts from its source the moment someone edits
either side, and it inflates the file with reasoning a reader can look up.

- **Cite, then keep only the result.** State the invariant or constraint the reader needs at this
  line, in one clause. Close with a citation: `(BE-NNNN)` for a roadmap item, `(docs/<page>.md)` for
  a prose page. Drop the derivation — the alternatives considered, the counterfactual ("were X ever
  true, Y would break"), the full chain from premise to conclusion. That belongs in the cited
  document.
- **A citation is not a substitute for content.** A fact the comment's own sentence rests on belongs
  in the comment; a pointer never stands in for it. The comment must make sense on its own, to
  someone who has not opened the cited document.
- **Distinguish three shapes before trimming:**
  - **A repeated argument.** The comment re-derives a decision the cited document already argues in
    full (a rejected alternative, a "because … because …" chain, a worked example). Trim to the
    result and cite.
  - **A missing abstraction.** The comment documents a contract — an invariant a value or a type
    must uphold — rather than explaining a line of code. A comment that keeps growing to cover more
    of that contract is a sign the contract wants a name. Extract a class or a function and move the
    explanation into its docstring (see BE-0065 above) instead of trimming the comment further.
  - **Independent facts, not one argument.** A comment can run long because it lists unrelated,
    fixed facts (for example, a schema version's per-revision compatibility notes) rather than
    pursuing a single argument. Do not force a citation onto this shape, and do not cut a fact to
    shorten it — every line still carries information a reader would otherwise have to reconstruct.
- **A comment's own length is a symptom to check, not a target to hit.** A comment past a handful of
  lines fits one of the first two shapes above far more often than not. Check whether the reasoning
  already lives in a roadmap item before crediting the length to thoroughness.

Example — trimmed to its result, with the derivation left in the item:

```python
# A retry forces the device recovery `erase: true` would give. Skipped on `reinstall: overwrite`
# (the scenario needs its app data preserved) and on the operator's `--no-erase`; a CLI-resolved
# `erase: false` is NOT the same signal (BE-0353).
```

not:

```python
# Skipped when the scenario declares `reinstall: overwrite` — its explicit declaration that it
# needs its app's data container preserved across a lease — since forcing `erase` would silently
# override exactly the precondition the scenario was written against. NOT skipped on
# `preconditions.erase is False`: by the time a scenario reaches here, the CLI has already resolved
# every scenario's `erase` to a concrete bool — most commonly `False`, the built-in default a
# scenario never asked for — so a guard on that value would silently disable this whole unit on the
# one path it was written for (see *Alternatives considered*: only `reinstall: overwrite` actually
# protects app data; a bare `erase: false` does not, since `reinstall`'s own default `"clean"` wipes
# the app's data regardless of `erase`).
```

Judging whether a comment repeats an argument or lists independent facts needs semantic
understanding, so — like the rest of this repo's comment and prose norms — it stays a review-time
expectation, not a `make check` gate (prime directive 1).

## Inline code comments

The docstring rule above governs a function's documentation; this is the companion rule for
**inline comments** — `#` lines in Python, `//` lines in Swift. It distills the standard
literature (the references close the section) and this codebase's own best idioms. Like the
documentation-style rules, it is a review-time norm, not a CI gate: judging whether a comment earns
its place needs semantic judgment, which prime directive 1 keeps off the `run` / CI verdict path.

### What a comment is for

A comment carries what the code cannot say: the rationale, the invariant, the constraint, the
non-obvious consequence — information that was in the author's head and would otherwise be lost.
Write it at a different level than the code. Two levels work; the third does not:

- **Precision** — sharpen the adjacent line with what the code leaves open: units, inclusive or
  exclusive bounds, what `None` means, where a magic number comes from. The house form is the
  trailing one-liner ([`bajutsu/totp.py`](../bajutsu/totp.py)):

  ```python
  cleaned += "=" * (-len(cleaned) % 8)  # b32decode requires the padding authenticators omit
  ```

- **Intent** — why this way, and, wherever a reader would plausibly "simplify" the code back into
  the bug it avoids, why not the other way ("Deliberately not the app's own external files
  directory: `adb` cannot read …"). The review test decides when intent is worth writing: if you
  would have to explain a line at the next code review, comment it now.
- **Restating** — a comment a reader could write from the adjacent line alone (`i += 1  # add
  one`) says nothing; delete it. This codebase has essentially none — keep it that way.

Comments are English, like all code. Write complete sentences, capitalized, with a period on block
comments; a one-line trailing fragment may drop the period.

### When a comment runs long, cite instead of arguing

A comment that keeps growing past a handful of lines is usually holding an argument that belongs
elsewhere. [*Cite an argument instead of repeating it*](#cite-an-argument-instead-of-repeating-it-comments-and-docstrings)
above is the rule: state the conclusion, cite the `BE-NNNN` item or `docs/` page that carries the
alternatives considered, the scope decision, and the rest of the derivation, and trim to that.
Some genuinely long comments still earn their length — the Device Farm bootstrap block in
[`bajutsu/cloud/devicefarm.py`](../bajutsu/cloud/devicefarm.py) is one, holding operational facts a
maintainer needs at that exact line rather than one argument to trim — so check the comment's
shape (the three shapes in *Cite an argument instead of repeating it*) before cutting.

### Describe the code as it is, never the change that produced it

A comment must make sense to a reader who never saw the PR, the review thread, or the session that
wrote the code. "Out of scope for this item", "tracked as a follow-up", "per PR review", and
"changed X to Y" are commit-message or review-reply content misfiled into source — put them in the
PR, and write the comment about the code as it now stands. The same rule fixes the provenance
format:

- Cite a roadmap item as the plain id, `(BE-0319)`. Never write a bare work-breakdown word — "unit
  1", "Half 2", "phase 5" — without the id; those names mean nothing once the item is merged.
  Prefer stating the constraint itself and letting the id point to the record.
- Cite a PR (`PR #1492`) only when no BE item covers the fact; a BE id is the more durable
  reference.

### The docstring boundary

What a function or class does — its contract — belongs in its docstring, even before the module
joins the `lint-docstrings` allowlist. A leading `#` block that answers "what this does" is a
docstring wearing the wrong syntax; write it as a docstring. Inline comments then carry only
what the docstring should not: line-level precision and intent. Per-field trailing comments on a
`TypedDict` or a dataclass remain the right idiom (BE-0065 above), but drop any half that restates
the type annotation (`line: int | None  # None otherwise` restates; "1-based source line" earns
its place).

### Dividers and typography

New Python code that needs section markers uses the one-line form, `# --- label ---`; Swift uses
`// MARK: -`. Do not add multi-line banner boxes, and do not reformat an existing file's dividers
as a side effect of an unrelated change. Comment prose follows the same typography as the docs: an
em dash (`—`), not a double hyphen.

### Markers and suppressions

- **TODO** is `# TODO(BE-NNNN): <what unblocks it>` — a tracked id, never a person's name, never a
  bare `TODO`. A temporary workaround names the event that retires it ("delete this block once
  Device Farm ships Python >= 3.13"), not "someday".
- **A suppression names its code — and its reason wherever the code alone does not explain it**:
  `# noqa: S310  # scheme validated above`, `# type: ignore[attr-defined]  # boto3 ships no
  stubs`. Stale suppressions are already pruned mechanically (`ruff`'s RUF100, and strict `mypy`
  warns on unused ignores); the reason is the human half, and the `ignore =` block in
  [`pyproject.toml`](../pyproject.toml) shows the shape.
- **Commented-out code: never.** Delete it — version control remembers. The codebase is at zero;
  keep it there.

### Density and scope discipline

Match the surrounding density: short and purposeful, concentrated where the code is least obvious.
Do not add comments to code you did not otherwise change, and when refactoring, carry the existing
why-comments over — silently dropping earned rationale is how a workaround gets "simplified" back
into the bug it avoided.

### References

- John Ousterhout, *A Philosophy of Software Design*, ch. 12–13 — comments capture what the code
  cannot express, written at a lower (precision) or higher (intent) level than the code, never the
  same one.
- Steve McConnell, *Code Complete*, 2nd ed., ch. 32 — the six kinds of comments; only summary,
  intent, and information the code itself cannot express survive.
- [Python Enhancement Proposal (PEP) 8, "Comments"](https://peps.python.org/pep-0008/#comments) —
  a comment that contradicts the code is worse than none; updating comments is part of changing
  the code.
- [Google Python Style Guide §3.8](https://google.github.io/styleguide/pyguide.html) (the review
  test) and §3.12 (TODO cites a tracked reference, not a person);
  [Google's reviewer guide](https://google.github.io/eng-practices/review/reviewer/looking-for.html)
  ("mostly explain *why* instead of *what*").
- Ellen Spertus,
  ["Best practices for writing code comments"](https://stackoverflow.blog/2021/12/23/best-practices-for-writing-code-comments/)
  (Stack Overflow blog, 2021) — explain unidiomatic code so it is not "fixed" into a bug; link the
  source of copied code.
