**English** · [日本語](BE-XXXX-config-source-real-repo-fetch-ja.md)

# BE-XXXX — Real-repository fetch verification for the config source

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-config-source-real-repo-fetch.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Configuration sourcing |
| Related | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md) |
<!-- /BE-METADATA -->

## Introduction

`config_source.py`'s `materialize()` fetches config from a Git host so a team can point `bajutsu`
at a remote repository instead of a local checkout. Its own test module says outright that it
"injects a fake transport... no network, no `git` binary" — every `materialize()` test passes a
fake `Transport`, and `_GitHubTransport`'s real `urllib.request`-based implementation is exercised
only for its HTTP-error mapping, via a monkeypatched `urlopen` raising a canned error. No test ever
fetches a real tarball from a real repository. This item adds one.

## Motivation

The fake transport proves `materialize()` correctly composes whatever bytes a transport hands it
into a working config tree — real and useful coverage of the composition logic. It proves nothing
about `_GitHubTransport` itself: whether a real GitHub tarball URL's redirect chain is followed
correctly, whether the real response's content-type or compression is handled as assumed, or whether
a real rate-limit or auth-failure response is mapped to the right error rather than an
unhandled exception. The same gap shows up in the GitHub App token flow: a mock only ever returns
what its author believes the real host returns.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A real disposable test repository.** Use a small, stable, low-privilege public repository (or a
  private repository with a dedicated throwaway GitHub App installed) as the fetch target.
  `_GitHubTransport` already works unauthenticated when no token is configured, and GitHub's
  commits/tarball endpoints don't require auth for a public repository, so the public-repo option
  needs no credential to fetch at all — a personal access token (PAT) there is purely to buy the higher authenticated rate
  limit (GitHub's unauthenticated cap is 60 requests/hour, tight on a shared Actions-runner IP) and
  stays optional. Store whichever credential is actually used — a fine-grained,
  read-only PAT scoped to the one repository, or the throwaway App's private key for the
  private-repo option — as a dedicated repository secret; whoever owns the test repository (or the
  App) rotates it.
- **A live fetch test.** Drive `materialize()` through the real `_GitHubTransport` against that
  repository, skipped when network access or the relevant credential is unavailable, asserting the
  fetched config tree matches what's actually in the repository.
- **Cover the error path for real too.** Where feasible, provoke a real 404/403 (a deliberately wrong
  ref or an unauthorized private repo) rather than only a monkeypatched `HTTPError`, confirming the
  real error surface maps the way `_GitHubTransport`'s tests assume.
- **Non-gating, permanently.** Land the new job as CI signal, following BE-0282's precedent of
  landing new backend coverage as signal first. Unlike BE-0282's own `network (playwright)` job,
  though — which needs no secret and so safely triggers on every `pull_request`, including a fork's
  — this job needs a real credential and triggers only on `push` to `main` (or `schedule`), never
  `pull_request`, mirroring the trigger restriction `roadmap-id.yml` already relies on to keep its
  `AUTOMATION_BOT_PRIVATE_KEY`/`AUTOMATION_BOT_APP_ID` secrets away from a fork-triggered run. A check
  that only runs after merge reports against `main`'s commit, not a PR's head SHA, so it structurally
  cannot become a required status check the way BE-0282's job could: this is the design's permanent
  shape, not a step toward requiring it later, since the only way to change that would be exposing
  the credential to a PR-triggered run.

## Alternatives considered

- **Trust the fake-transport tests, since the composition logic is thoroughly covered.** Composition
  correctness assumes the transport handed it correct bytes in the first place; it says nothing
  about whether the real transport implementation actually produces those bytes from a real host.
- **Cover this indirectly through a GitHub App token-flow integration test alone.** That verifies the
  token flow; this item verifies the fetch-and-materialize path built on top of it. Both are needed —
  a working token with a broken fetch, or a working fetch with a broken token, are each independently
  observable failure modes.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Designate a real disposable test repository as the fetch target.
- [ ] Add a live fetch test driving `materialize()` through the real `_GitHubTransport`.
- [ ] Cover a real error response (404/403), not only a monkeypatched one.
- [ ] Wire it into CI as a non-gating signal.

## References

- [BE-0063 — Load config (and its scenario tree) from a Git repository + ref](../BE-0063-git-config-source/BE-0063-git-config-source.md)
- [BE-0224 — Granting private-repository access for the GitHub config source](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/config_source.py`, `tests/test_config_source.py`, `.github/workflows/roadmap-id.yml`
