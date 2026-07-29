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

This item adds an automated guard, backed by [gitleaks](https://github.com/gitleaks/gitleaks),
that blocks an API key, a cloud credential, or a private key from being committed to this
repository. The guard runs in two layers: a tracked local git hook gives a contributor immediate
feedback at commit time, and a CI step re-scans every tracked file independently, so a bypassed or
never-installed local hook still gets caught before a PR merges. This continues the *Contributor
workflow* line that turned the review checklist for a scoped commit subject into an executable
[commit-msg hook](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
and the review checklist for a conflict-free `uv.lock` merge into an executable
[merge driver](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md):
a guardrail that only prose enforces is a guardrail a session can forget, so this item makes
"don't commit a secret" a command instead.

This proposal originally picked [git-secrets](https://github.com/awslabs/git-secrets); PR review
([@hirosassa](https://github.com/hirosassa)) pointed out that git-secrets has had no release in
seven years, while [gitleaks](https://github.com/gitleaks/gitleaks) is actively maintained, and
the item was reworked to gitleaks before merging. *Detailed design* below describes the
implemented gitleaks design directly; *Alternatives considered* records why git-secrets was
rejected on reconsideration, since that choice was made and then reversed within this same item.

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

**Choice of tool.** [gitleaks](https://github.com/gitleaks/gitleaks) is a single Go binary with a
curated, actively-maintained default ruleset (AWS credentials, GitHub tokens including
fine-grained PATs, PEM private keys, and hundreds more) plus a tracked TOML config for anything
project-specific. Two properties shape the design below:

- Its config file, `.gitleaks.toml`, is an ordinary tracked file gitleaks reads directly — unlike
  a tool that stores its configuration in local `git config` (a per-clone setting clone/pull never
  carries over), there is nothing to self-heal per clone. `make hooks` needs no gitleaks-specific
  step at all.
- gitleaks matches with Go's own regex engine (compiled into the binary), not the system `grep`,
  so a pattern behaves identically on every contributor's machine regardless of OS. This
  eliminates a whole class of bug by construction: an earlier revision of this item, still using
  git-secrets (which shells out to `grep -E`), shipped two GNU-only regex constructs — `\s`
  outside a bracket expression, and `\b` — that PR review caught because a POSIX-only `grep -E`
  (BSD grep, the macOS default) treats both as literal characters, silently weakening the local
  hook on every contributor's Mac. gitleaks cannot have that failure mode.

**A. Tracked hooks.** Two new files join [`.githooks/`](../../.githooks/):

- [`.githooks/pre-commit`](../../.githooks/pre-commit) scans every staged file for a prohibited
  pattern and refuses the commit on a match, via `gitleaks git --pre-commit --staged --redact` —
  gitleaks' own documented pre-commit invocation.
- [`.githooks/prepare-commit-msg`](../../.githooks/prepare-commit-msg) runs the same scan for a
  merge commit, whose staged index already holds the incoming tree by the time this hook fires, so
  merging in a branch that already carries a secret does not silently reintroduce it.

The existing [`.githooks/commit-msg`](../../.githooks/commit-msg) hook gains a
`gitleaks stdin --redact` call (piping in the commit-message file) ahead of its scoped-subject
check, so a secret pasted into the message body itself is caught too. Git executes only one script
per hook name, so the two checks share that one file rather than each getting its own. Every hook
degrades gracefully when `gitleaks` is not yet on `PATH` — it skips with no effect on the commit,
the same way the existing commit-msg hook already no-ops when `uv` is absent — so a contributor
who has not installed it yet is never blocked from committing, only left unprotected until they do.
`--redact` keeps a real match from ever printing in cleartext to a terminal or CI log.

**B. `.gitleaks.toml` extends, rather than replaces, the default ruleset.**
[`.gitleaks.toml`](../../.gitleaks.toml) sets `[extend] useDefault = true` and adds exactly two
things the defaults don't cover: an Anthropic API key or OAuth token (`.env.example`'s
`ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`), and `BAJUTSU_SERVE_TOKEN` /
`GRAFANA_ADMIN_PASSWORD` hardcoded by key name rather than value shape (the actual deploy-time
secrets [`deploy/self-host/README.md`](../../deploy/self-host/README.md) has an operator set,
which have no distinctive value shape of their own to match on). The Anthropic rule needed one
more step than a plain addition: the default ruleset already ships `anthropic-api-key`
(`sk-ant-api03-…`) and `anthropic-admin-api-key` (`sk-ant-admin01-…`), neither of which covers
`CLAUDE_CODE_OAUTH_TOKEN`'s `sk-ant-oat01-` prefix. Registering a *third*, separate rule alongside
those two — reproduced empirically against gitleaks v8.30.1, not assumed from the docs — made
gitleaks silently stop matching any of the three; the fix was overriding the `anthropic-api-key`
rule **by id** with one broadened regex covering all three prefixes, and disabling
`anthropic-admin-api-key` so only one `sk-ant-`-matching rule is ever registered at once. Every
regex in the config was verified against real, correctly-shaped secrets in a scratch scan, not
just read for plausibility.

**C. A CI re-scan closes the local-hook gap.** A local hook only protects a clone that has it
wired and a commit that was not made with `--no-verify`; neither holds unconditionally — the same
reasoning already justifies why `make check` runs identically in the pre-push hook and in CI. A new
`make lint-secrets` target re-scans every tracked file (`gitleaks dir . --redact`, which respects
`.gitignore`) and joins `make check`, following the exact skip-with-a-notice pattern `lint-actions`
already uses for `actionlint`: it skips locally with a message when `gitleaks` is not on `PATH`,
and CI always installs it first, pinned to the `v8.30.1` release tarball with its published
checksum verified — the same pattern `actionlint`'s own install step already uses, and a closer
fit for gitleaks than it was for git-secrets, which had cut no release to pin a checksum against.

**D. `[[allowlists]]` in `.gitleaks.toml` is the escape valve for a legitimate false match.**
Running `gitleaks dir .` against this repository's existing tree surfaced real cases, each now a
narrow, `targetRules`-scoped allowlist entry rather than a loosened pattern:
[`tests/test_github_app.py`](../../tests/test_github_app.py)'s deliberately malformed PEM fixture
(the literal string `nope` as the key body) matches gitleaks' default `private-key` rule without
being a private key; `deploy/self-host/.env.example`'s self-documenting `change-me` placeholder and
`deploy/self-host/docker-compose.yml`'s `${VAR:?…}` / `${VAR:-…}` shell interpolation reference
`BAJUTSU_SERVE_TOKEN` / `GRAFANA_ADMIN_PASSWORD` without a literal value;
[`bajutsu/serve/launchagent.py`](../../bajutsu/serve/launchagent.py) assigns from an
already-validated `token` variable, not a literal; and gitleaks' own entropy-heuristic
`generic-api-key` default rule flags an Android AVD device name
(`.github/workflows/android-e2e.yml`), a JS metrics key literal
(`bajutsu/templates/serve.metrics.mjs`), and a TOTP test fixture secret (`tests/test_totp.py`,
`tests/test_totp_step.py`) — none of which are API keys.

**E. Documentation.** [`docs/ai-development.md`](../../docs/ai-development.md) (and its
`docs/ja/` mirror) gains a section describing the two-layer guard, and
[`SECURITY.md`](../../SECURITY.md) (and its Japanese mirror) notes it alongside the existing
API-key guidance it backs up.

## Alternatives considered

- **git-secrets, this item's original choice.** Rejected on reconsideration during review: it has
  had no release in seven years (the pinned commit this item briefly used was from 2019), while
  gitleaks is actively maintained; git-secrets also shells out to the system `grep -E`, which
  reintroduced a GNU-vs-POSIX regex portability bug twice during review (see *Detailed design*) —
  a class of bug gitleaks cannot have, since it matches with its own bundled regex engine on every
  platform. git-secrets' local-`git-config` pattern storage also needed a self-healing `make hooks`
  step this item no longer needs at all, since gitleaks' config is a plain tracked file.
- **Scan the entire commit history in CI (`gitleaks git`, unrestricted).** Rejected for this item's
  scope: the goal here is to block a secret going forward, matching the request that motivated it;
  scanning all of history is slower, and a historical false positive found that way would need its
  own triage pass across every past commit before the check could go green. Left as a candidate for
  a future, separate item, not folded into this one.
- **Rely on the local pre-commit hook alone, with no CI step.** Rejected: a hook can be bypassed
  with `--no-verify`, or simply never wired by a clone that skipped `make setup`, so only an
  independent CI re-scan actually gates a PR — the same reason `make check` itself runs in both the
  pre-push hook and CI rather than trusting the hook alone.
- **Register the Anthropic OAuth-token prefix as a separate rule alongside the defaults.** Rejected
  after it was shown, empirically, to make gitleaks silently stop matching any `sk-ant-`-prefixed
  rule at all (see *Detailed design*, *B*). Overriding the existing `anthropic-api-key` rule by id
  with one combined regex, and disabling the now-redundant `anthropic-admin-api-key`, is the
  version that was actually verified to work.

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
