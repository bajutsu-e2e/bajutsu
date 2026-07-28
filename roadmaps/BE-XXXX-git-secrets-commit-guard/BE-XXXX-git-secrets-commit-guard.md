**English** · [日本語](BE-XXXX-git-secrets-commit-guard-ja.md)

# BE-XXXX — Block secrets from being committed with git-secrets

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-git-secrets-commit-guard.md) |
| Author | [@akira-matsuda](https://github.com/akira-matsuda) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | TBD — filled in once the PR is opened (this is a BE-creation PR; the id and PR are not opened by this session) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This item adds an automated guard, backed by [git-secrets](https://github.com/awslabs/git-secrets),
that blocks an API key, a cloud credential, or a private key from being committed to this
repository. The guard runs in two layers: a tracked local git hook gives a contributor immediate
feedback at commit time, and a CI step re-scans every tracked file independently, so a bypassed or
never-installed local hook still gets caught before a PR merges. Both layers self-heal the same way
the existing `core.hooksPath` wiring does — `make hooks` re-registers everything on every session,
so no contributor has to remember a manual setup step. This continues the *Contributor workflow*
line that turned the review checklist for a scoped commit subject into an executable
[commit-msg hook](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
and the review checklist for a conflict-free `uv.lock` merge into an executable
[merge driver](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md):
a guardrail that only prose enforces is a guardrail a session can forget, so this item makes
"don't commit a secret" a command instead.

## Motivation

[`SECURITY.md`](../../SECURITY.md) already tells a contributor never to commit or share an API key
and to keep one in the gitignored `.env` file, and [`.gitignore`](../../.gitignore) keeps that one
file out of the tree. Neither backstop reaches further than its own narrow case: `.gitignore`
protects exactly the files it names, and a `SECURITY.md` instruction is read, not run, so nothing
stops a credential pasted directly into a tracked scenario fixture, a config file, or a commit
message. The repository already treats this exact gap as unacceptable for its other guardrails —
[BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)'s
own argument is that "a recipe that is only read, never executed, rots", and turned the scoped-commit-
subject convention into a hook precisely so it could not be silently skipped. A secret is a sharper
case than a style convention: a leaked `ANTHROPIC_API_KEY` or an AWS credential (the kind
[`deploy/self-host/README.md`](../../deploy/self-host/README.md) asks a self-hoster to export) is a
security incident, not a style nit, so it deserves at least the same enforcement a non-scoped commit
subject already gets.

An AI session compounds the risk this item closes. A human contributor pastes a credential rarely
and usually notices; an agent authoring or debugging a scenario can copy a value it just read from
`.env`, a terminal, or a captured network exchange straight into a file it is about to commit,
with no habitual pause to reconsider. Because this project runs many AI sessions in parallel
(`CLAUDE.md`), the guard has to hold for every one of them equally, which is exactly what a
command-backed hook (checked out of the box, not opted into) provides and a documentation-only
warning does not.

## Detailed design

**Choice of tool.** [git-secrets](https://github.com/awslabs/git-secrets) is a small, dependency-free
bash script that adds `git secrets <command>` subcommands and a handful of git hooks. It needs no
Python dependency (so it never touches `uv.lock` or the `pyproject.toml` dependency graph) and
stores its configuration in ordinary `git config`, which this repository already manipulates for
the `uv.lock` merge driver and `rerere` (BE-0043). Every design decision below follows from reading
its actual source at the pinned commit CI installs (see *C* below), not from its README summary
alone, because two details it does not advertise up front shape the design:

- The hook files `git secrets --install` writes are exactly two lines —
  `#!/usr/bin/env bash` followed by `git secrets --<hook-name>_hook -- "$@"`. This item writes that
  same two-line core directly into the tracked `.githooks/` files itself (each wrapped in a comment
  header and a `command -v git-secrets` guard, per *A* below) rather than running `--install`,
  which sidesteps a real mismatch: `--install <target-directory>` treats the directory as a hook
  destination only when it already looks like a `.git` directory, and otherwise creates a nested
  `<target-directory>/hooks/` under it — the wrong shape for this repository's flat, tracked
  `.githooks/` layout.
- `git secrets --add` and `--register-aws` never duplicate an already-registered pattern (each
  checks the existing `git config` entries first), so calling them on every `make hooks` run, the
  same way the merge driver and `rerere` are re-wired today, is always safe *for the resulting
  config*. `--add` alone still exits non-zero on that already-registered no-op (`--register-aws`
  does not), which would make `set -e` fail every `make hooks` run after the first one that ever
  registers a custom pattern — so [`scripts/git-secrets-setup.sh`](../../scripts/git-secrets-setup.sh)
  treats that specific exit as expected rather than a real failure.

**A. Tracked hooks, wired the same way as the existing ones.** Two new files join
[`.githooks/`](../../.githooks/):

- [`.githooks/pre-commit`](../../.githooks/pre-commit) scans every staged file for a prohibited
  pattern and refuses the commit on a match, via `git secrets --pre_commit_hook`.
- [`.githooks/prepare-commit-msg`](../../.githooks/prepare-commit-msg) runs the same check against
  a merge's incoming history, via `git secrets --prepare_commit_msg_hook`, so merging in a branch
  that already carries a secret does not silently reintroduce it.

The existing [`.githooks/commit-msg`](../../.githooks/commit-msg) hook gains a
`git secrets --commit_msg_hook` call ahead of its scoped-subject check, so a secret pasted into the
commit message body itself is caught too. Git executes only one script per hook name, so the two
checks share that one file rather than each getting its own. Every hook degrades gracefully when
`git-secrets` is not yet on `PATH` — it skips with no effect on the commit, the same way the
existing commit-msg hook already no-ops when `uv` is absent — so a contributor who has not
installed it yet is never blocked from committing, only left unprotected until they do.

**B. Self-healing pattern registration.** `git secrets --register-aws` adds the built-in AWS
credential patterns; this repository additionally handles four shapes `--register-aws` doesn't
cover: an Anthropic API key or OAuth token (`.env.example`'s `ANTHROPIC_API_KEY` /
`CLAUDE_CODE_OAUTH_TOKEN`, both `sk-ant-`-prefixed), a pasted PEM private-key block (also covers a
GitHub App or GCS service-account key, the same PEM format), a GitHub token (`ghp_`/`gho_`/`ghu_`/
`ghs_`/`ghr_`/`github_pat_`-prefixed — this repository integrates deeply with GitHub, from
[`bajutsu/github/app.py`](../../bajutsu/github/app.py) to the CI automation bot), and
`BAJUTSU_SERVE_TOKEN` / `GRAFANA_ADMIN_PASSWORD` hardcoded by key name rather than value shape (the
actual deploy-time secrets [`deploy/self-host/README.md`](../../deploy/self-host/README.md) has an
operator set, following the same `KEY=value` shape `--register-aws`'s own AWS-secret-key pattern
already uses for a value with no distinctive shape of its own). Because `git-secrets`
stores every pattern in this clone's local `git config` — a setting clone/pull never carries over,
the same problem `core.hooksPath` already has — the patterns live in a tracked file,
[`.githooks/git-secrets-patterns.txt`](../../.githooks/git-secrets-patterns.txt), and
[`scripts/git-secrets-setup.sh`](../../scripts/git-secrets-setup.sh) registers them into local
`git config` every time `make hooks` runs (idempotently, per the tool behavior noted above). A
fresh clone, or a session that skipped `make setup` and only ran `make check`, self-heals the
moment the gate next runs `hooks` — its existing first prerequisite. When `git-secrets` itself is
not installed, the script prints how to install it (`brew install git-secrets`, also added to the
[`Brewfile`](../../Brewfile), or building from source) and exits cleanly rather than failing
`make hooks`.

**C. A CI re-scan closes the local-hook gap.** A local hook only protects a clone that has it
wired and a commit that was not made with `--no-verify`; neither holds unconditionally; the same
reasoning already justifies why `make check` runs identically in the pre-push hook and in CI. A new
`make lint-secrets` target re-scans every tracked file (`git secrets --scan`) and joins `make check`,
following the exact skip-with-a-notice pattern `lint-actions` already uses for `actionlint`: it
skips locally with a message when `git-secrets` is not on `PATH`, and CI always installs it first.
Because upstream git-secrets has cut no GitHub Release since its `1.3.0` tag (2019), CI checks out
that tag's exact 40-character commit SHA rather than a floating branch — the same immutability
`actionlint`'s SHA-pinned installer script gets, achieved here by pinning the source checkout itself
instead of verifying a separately-downloaded release asset.

**D. An escape valve for a legitimate false match.** `git-secrets` reads a tracked
[`.gitallowed`](../../.gitallowed) file at the repository root as its own exemption mechanism, so a
string that matches a pattern without being a secret (a fixture value, a documented placeholder, a
shell variable reference) can be exempted there instead of by loosening a pattern. Running
`make lint-secrets` against this repository's existing tree surfaced five such cases, so
`.gitallowed` ships with five narrow, reviewable entries rather than an empty file:
[`tests/test_github_app.py`](../../tests/test_github_app.py)'s deliberately malformed PEM fixture
(the literal string `nope` as the key body, used to test the error path for a key that fails to
parse) matches the private-key pattern without being a private key; the private-key pattern's own
source line in [`.githooks/git-secrets-patterns.txt`](../../.githooks/git-secrets-patterns.txt)
matches itself, since its literal regex text contains the string it's written to detect; and the
new `BAJUTSU_SERVE_TOKEN` / `GRAFANA_ADMIN_PASSWORD` pattern (*B* above) matches three more places
in the existing tree that reference the variable name without a literal secret value —
[`deploy/self-host/.env.example`](../../deploy/self-host/.env.example)'s self-documenting
`change-me` placeholder (the same convention `.env.example`'s own `sk-ant-...` already uses),
[`deploy/self-host/docker-compose.yml`](../../deploy/self-host/docker-compose.yml)'s
`${BAJUTSU_SERVE_TOKEN:?…}` / `${GRAFANA_ADMIN_PASSWORD:-…}` shell variable interpolation, and
[`bajutsu/serve/launchagent.py`](../../bajutsu/serve/launchagent.py)'s
`"BAJUTSU_SERVE_TOKEN": token`, which assigns from an already-validated variable, not a literal.

**E. Documentation.** [`docs/ai-development.md`](../../docs/ai-development.md) (and its
`docs/ja/` mirror) gains a section describing the two-layer guard, and
[`SECURITY.md`](../../SECURITY.md) (and its Japanese mirror) notes it alongside the existing
API-key guidance it backs up.

## Alternatives considered

- **Run `git secrets --install` instead of hand-writing the hook wrapper files.** Rejected: as
  detailed in *Detailed design*, `--install`'s target-directory handling assumes either a real
  `.git` directory or an empty template directory to populate with a fresh `hooks/` subdirectory,
  neither of which matches this repository's flat, tracked `.githooks/` layout; writing the same
  two-line core `--install` itself would have written, directly into the tracked files, sidesteps
  the mismatch entirely and keeps every hook file as ordinary, reviewable, tracked text — exactly
  like the existing `pre-push` and `commit-msg` hooks.
- **Leave the custom patterns as an ad hoc, undocumented `git secrets --add` a contributor runs by
  hand.** Rejected: local `git config` is not shared across clones or CI, so every session would
  have to remember and re-type the exact patterns; a tracked patterns file plus self-healing
  registration in `make hooks` keeps one source of truth, the same shape BE-0043's merge driver
  already takes for a different per-clone `git config` setting.
- **Scan the entire commit history in CI (`git secrets --scan-history`).** Rejected for this item's
  scope: the goal here is to block a secret going forward, matching the request that motivated it;
  scanning all of history is slower, and a historical false positive found that way would need its
  own triage pass across every past commit before the check could go green. Left as a candidate for
  a future, separate item, not folded into this one.
- **Rely on the local pre-commit hook alone, with no CI step.** Rejected: a hook can be bypassed
  with `--no-verify`, or simply never wired by a clone that skipped `make setup`, so only an
  independent CI re-scan actually gates a PR — the same reason `make check` itself runs in both the
  pre-push hook and CI rather than trusting the hook alone.

## Progress

- [x] A — tracked `pre-commit` / `prepare-commit-msg` hooks; `commit-msg` extended with the same
      scan.
- [x] B — self-healing pattern registration (`make hooks` → `scripts/git-secrets-setup.sh` +
      `.githooks/git-secrets-patterns.txt`).
- [x] C — `make lint-secrets` folded into `make check`; CI installs a pinned git-secrets and runs it.
- [x] D — the `.gitallowed` escape valve, documented.
- [x] E — `docs/ai-development.md` (+ `ja`), `SECURITY.md` (+ `ja`), `CLAUDE.md`, `Brewfile` updated.

## References

- [git-secrets](https://github.com/awslabs/git-secrets) — the tool this item wires in.
- [BE-0069 — Executable contributor guardrails](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
  — the *Contributor workflow* precedent this item continues: a prose recipe becomes a self-healing
  command, and the existing `commit-msg` hook this item extends.
- [BE-0043 — Conflict-resistant file flow](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
  — the per-clone local `git config` self-healing pattern (`make hooks`) this item's pattern
  registration reuses.
- [`SECURITY.md`](../../SECURITY.md), [`.env.example`](../../.env.example) — the existing
  prose-only secret-handling guidance this item backs with an executable check.
- [`docs/ai-development.md`](../../docs/ai-development.md) — the parallel-work guide; see
  "Block a secret before it's committed" for the mechanism.
