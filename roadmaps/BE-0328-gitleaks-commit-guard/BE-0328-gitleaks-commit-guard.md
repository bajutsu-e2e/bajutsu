**English** · [日本語](BE-0328-gitleaks-commit-guard-ja.md)

# BE-0328 — Block secrets from being committed with gitleaks

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0328](BE-0328-gitleaks-commit-guard.md) |
| Author | [@akira-matsuda](https://github.com/akira-matsuda) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0328") |
| Implementing PR | TBD — filled in once the PR is opened (this is a BE-creation PR; the id and PR are not opened by this session) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This item adds an automated guard, backed by [gitleaks](https://github.com/gitleaks/gitleaks). The guard blocks an API key, a cloud credential, or a private key from being committed to this repository. The guard has two layers. A tracked local git hook gives a contributor immediate feedback at commit time. A CI step re-scans every tracked file independently. A bypassed or never-installed local hook still gets caught before a PR merges.

The item continues the *Contributor workflow* line. That line turned the review checklist for a scoped commit subject into an executable [commit-msg hook](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md). It also turned the review checklist for a conflict-free `uv.lock` merge into an executable [merge driver](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md). A session can forget a guardrail that only prose enforces, so this item makes "don't commit a secret" a command instead.

The proposal originally picked [git-secrets](https://github.com/awslabs/git-secrets). PR review ([@hirosassa](https://github.com/hirosassa)) pointed out that git-secrets has had no release in seven years, while [gitleaks](https://github.com/gitleaks/gitleaks) is actively maintained. The item was reworked to gitleaks before merging. *Detailed design* below describes the implemented gitleaks design directly. *Alternatives considered* records why git-secrets was rejected on reconsideration, because the item made that choice and then reversed it.

## Motivation

[`SECURITY.md`](../../SECURITY.md) already tells a contributor never to commit or share an API key and to keep one in the gitignored `.env` file. [`.gitignore`](../../.gitignore) keeps that one file out of the tree. Neither backstop reaches beyond its own narrow case. `.gitignore` protects the files it names, and a `SECURITY.md` instruction is read, not run. Nothing stops a credential pasted directly into a tracked scenario fixture, a config file, or a commit message.

The repository already treats this gap as unacceptable for its other guardrails. [BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md) argues that "a recipe that is only read, never executed, rots". It turned the scoped-commit-subject convention into a hook precisely so that nobody could skip the convention silently. A secret is a sharper case than a style convention. A leaked `ANTHROPIC_API_KEY` or an AWS credential (the kind [`deploy/self-host/README.md`](../../deploy/self-host/README.md) asks a self-hoster to export) is a security incident, not a style nit. A secret deserves at least the enforcement that a non-scoped commit subject already gets.

An AI session compounds the risk that this item closes. A human contributor pastes a credential rarely and usually notices. An agent authoring or debugging a scenario can copy a value into a file that it is about to commit. The value may come from `.env`, a terminal, or a captured network exchange. The agent has no habitual pause to reconsider. This project runs many AI sessions in parallel (`CLAUDE.md`), so the guard has to hold equally for every session. A command-backed hook provides that, because the hook is checked out of the box and no one opts in. A documentation-only warning does not.

## Detailed design

**Choice of tool.** [gitleaks](https://github.com/gitleaks/gitleaks) is a single Go binary. It ships a curated, actively maintained default ruleset. The ruleset covers AWS credentials, GitHub tokens including fine-grained PATs, PEM private keys, and hundreds more. It also reads a tracked TOML config for anything project-specific. Two properties shape the design below.

- The config file, `.gitleaks.toml`, is an ordinary tracked file that gitleaks reads directly. A tool that stores its configuration in local `git config` has a per-clone setting that clone and pull never carry over. gitleaks has no such setting to self-heal per clone, so `make hooks` needs no gitleaks-specific step at all.
- gitleaks matches with Go's own regex engine (compiled into the binary), not the system `grep`. A pattern behaves identically on every contributor's machine, regardless of OS. This eliminates a whole class of bug by construction. An earlier revision of this item still used git-secrets, which shells out to `grep -E`. That revision shipped two GNU-only regex constructs: `\s` outside a bracket expression, and `\b`. PR review caught them, because a POSIX-only `grep -E` (BSD grep, the macOS default) treats both as literal characters. The constructs silently weakened the local hook on every contributor's Mac. gitleaks cannot have that failure mode.

**A. Tracked hooks.** Two new files join [`.githooks/`](../../.githooks/).

- [`.githooks/pre-commit`](../../.githooks/pre-commit) scans every staged file for a prohibited pattern and refuses the commit on a match. It runs `gitleaks git --pre-commit --staged --redact`, which is the pre-commit invocation that gitleaks documents.
- [`.githooks/prepare-commit-msg`](../../.githooks/prepare-commit-msg) runs the same scan for a merge commit. The staged index of a merge commit already holds the incoming tree by the time this hook fires. Merging in a branch that already carries a secret does not reintroduce the secret unnoticed.

The existing [`.githooks/commit-msg`](../../.githooks/commit-msg) hook gains a `gitleaks stdin --redact` call, which pipes in the commit-message file, ahead of its scoped-subject check. A secret pasted into the message body itself is caught too. Git executes one script per hook name, so the two checks share the one file instead of each getting its own file.

Every hook degrades gracefully when `gitleaks` is not yet on `PATH`. The hook skips with no effect on the commit, the same way the existing commit-msg hook already no-ops when `uv` is absent. A contributor who has not installed gitleaks yet is never blocked from committing. The contributor stays unprotected until the installation. `--redact` keeps a real match from ever printing in cleartext to a terminal or CI log.

**B. `.gitleaks.toml` extends, rather than replaces, the default ruleset.** [`.gitleaks.toml`](../../.gitleaks.toml) sets `[extend] useDefault = true` and adds exactly two things that the defaults don't cover.

- An Anthropic API key or OAuth token (`.env.example`'s `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`).
- `BAJUTSU_SERVE_TOKEN` / `GRAFANA_ADMIN_PASSWORD` hardcoded, matched by key name rather than value shape. These are the actual deploy-time secrets that [`deploy/self-host/README.md`](../../deploy/self-host/README.md) has an operator set. They have no distinctive value shape of their own to match on.

The Anthropic rule needed one more step than a plain addition. The default ruleset already ships `anthropic-api-key` (`sk-ant-api03-…`) and `anthropic-admin-api-key` (`sk-ant-admin01-…`). Neither covers the `sk-ant-oat01-` prefix of `CLAUDE_CODE_OAUTH_TOKEN`. Registering a *third*, separate rule alongside those two made gitleaks silently stop matching any of the three. The item reproduced this empirically against gitleaks v8.30.1 and did not assume it from the docs.

The fix overrides the `anthropic-api-key` rule **by id** with one broadened regex that covers all three prefixes. The fix also disables `anthropic-admin-api-key`, so only one `sk-ant-`-matching rule is ever registered at once. A scratch scan verified every regex in the config against real, correctly shaped secrets. The regexes were not merely read for plausibility.

**C. A CI re-scan closes the local-hook gap.** A local hook protects a commit under two conditions. The clone must have the hook wired. The commit must not be made with `--no-verify`. Neither condition holds unconditionally. The same reasoning already justifies why `make check` runs identically in the pre-push hook and in CI.

A new `make lint-secrets` target re-scans every tracked file with `gitleaks dir . --redact`, which respects `.gitignore`. The target joins `make check` and follows the skip-with-a-notice pattern that `lint-actions` already uses for `actionlint`. Locally, the target skips with a message when `gitleaks` is not on `PATH`. CI always installs gitleaks first, pinned to the `v8.30.1` release tarball with its published checksum verified. The `actionlint` install step already uses the same pattern. The pattern fits gitleaks better than it fit git-secrets, which had cut no release to pin a checksum against.

**D. `[[allowlists]]` in `.gitleaks.toml` is the escape valve for a legitimate false match.** Running `gitleaks dir .` against the existing tree surfaced real cases. Each case is now a narrow, `targetRules`-scoped allowlist entry rather than a loosened pattern.

- [`tests/test_github_app.py`](../../tests/test_github_app.py) holds a deliberately malformed PEM fixture (the literal string `nope` as the key body). The fixture matches the default `private-key` rule of gitleaks without being a private key.
- `deploy/self-host/.env.example` holds a self-documenting `change-me` placeholder. `deploy/self-host/docker-compose.yml` holds `${VAR:?…}` / `${VAR:-…}` shell interpolation. Both reference `BAJUTSU_SERVE_TOKEN` / `GRAFANA_ADMIN_PASSWORD` without a literal value.
- [`bajutsu/serve/launchagent.py`](../../bajutsu/serve/launchagent.py) assigns from an already-validated `token` variable, not a literal.
- The `generic-api-key` default rule of gitleaks uses an entropy heuristic. The rule flags an Android AVD device name (`.github/workflows/android-e2e.yml`), a JS metrics key literal (`bajutsu/templates/serve.metrics.mjs`), and a TOTP test fixture secret (`tests/test_totp.py`, `tests/test_totp_step.py`). None of these is an API key.

**E. Documentation.** [`docs/ai-development.md`](../../docs/ai-development.md) (and its `docs/ja/` mirror) gains a section that describes the two-layer guard. [`SECURITY.md`](../../SECURITY.md) (and its Japanese mirror) notes the guard alongside the existing API-key guidance that the guard backs up.

## Alternatives considered

- **git-secrets, the original choice of this item.** Rejected on reconsideration during review, for three reasons.
  - git-secrets has had no release in seven years (the pinned commit that this item briefly used was from 2019), while gitleaks is actively maintained.
  - git-secrets shells out to the system `grep -E`. That reintroduced a GNU-vs-POSIX regex portability bug twice during review (see *Detailed design*). gitleaks cannot have this class of bug, because it matches with its own bundled regex engine on every platform.
  - git-secrets stores its patterns in local `git config`, which needed a self-healing `make hooks` step. This item no longer needs that step, because the gitleaks config is a plain tracked file.
- **Scan the entire commit history in CI (`gitleaks git`, unrestricted).** Rejected for the scope of this item. The goal here is to block a secret going forward, which matches the request that motivated the item. Scanning all of history is slower. A historical false positive found that way would need its own triage pass across every past commit before the check could go green. A future, separate item may take this up. This item does not fold it in.
- **Rely on the local pre-commit hook alone, with no CI step.** Rejected. A contributor can bypass a hook with `--no-verify`, and a clone that skipped `make setup` never wires the hook. Only an independent CI re-scan actually gates a PR. `make check` itself runs in both the pre-push hook and CI for the same reason, rather than trusting the hook alone.
- **Register the Anthropic OAuth-token prefix as a separate rule alongside the defaults.** Rejected after it was shown, empirically, to make gitleaks silently stop matching any `sk-ant-`-prefixed rule at all (see *Detailed design*, *B*). The version verified to work overrides the existing `anthropic-api-key` rule by id with one combined regex, and disables the now-redundant `anthropic-admin-api-key`.

## Progress

- [x] A — tracked `pre-commit` / `prepare-commit-msg` hooks; `commit-msg` extended with the same
      scan.
- [x] B — `.gitleaks.toml` extending the default ruleset with an Anthropic key/OAuth-token rule
      and a `BAJUTSU_SERVE_TOKEN` / `GRAFANA_ADMIN_PASSWORD` rule.
- [x] C — `make lint-secrets` folded into `make check`; CI installs a pinned, checksum-verified
      gitleaks release and runs it.
- [x] D — `.gitleaks.toml`'s `[[allowlists]]` entries for every false positive found by actually
      scanning the tree.
- [x] E — `docs/ai-development.md` (+ `ja`), `SECURITY.md` (+ `ja`), `CLAUDE.md`, `Brewfile` updated.

## References

- [gitleaks](https://github.com/gitleaks/gitleaks) — the tool this item wires in.
- [BE-0069 — Executable contributor guardrails](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
  — the *Contributor workflow* precedent this item continues: a prose recipe becomes a self-healing
  command, and the existing `commit-msg` hook this item extends.
- [BE-0043 — Conflict-resistant file flow](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
  — the per-clone local `git config` self-healing pattern this item's tracked `.gitleaks.toml`
  config sidesteps needing at all.
- [`SECURITY.md`](../../SECURITY.md), [`.env.example`](../../.env.example) — the existing
  prose-only secret-handling guidance this item backs with an executable check.
- [`docs/ai-development.md`](../../docs/ai-development.md) — the parallel-work guide; see
  "Block a secret before it's committed" for the mechanism.
