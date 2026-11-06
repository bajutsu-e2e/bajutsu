**English** · [日本語](BE-0302-config-source-real-repo-fetch-ja.md)

# BE-0302 — Real-repository fetch verification for the config source

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0302](BE-0302-config-source-real-repo-fetch.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0302") |
| Topic | Configuration sourcing |
| Related | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md) |
<!-- /BE-METADATA -->

## Introduction

`materialize()` in `config_source.py` fetches config from a Git host. A team can then point `bajutsu`
at a remote repository instead of a local checkout.

The test module says outright that it "injects a fake transport... no network, no `git` binary". Every
`materialize()` test passes a fake `Transport`. The real `urllib.request`-based implementation of
`_GitHubTransport` is exercised only for its HTTP-error mapping, through a monkeypatched `urlopen` that
raises a canned error. No test fetches a real tarball from a real repository. This item adds one.

## Motivation

The fake transport proves that `materialize()` composes whatever bytes a transport hands it into a
working config tree. That is real and useful coverage of the composition logic.

The fake transport proves nothing about `_GitHubTransport` itself:

- Whether the redirect chain of a real GitHub tarball URL is followed correctly.
- Whether the real response's content-type or compression is handled as assumed.
- Whether a real rate-limit or auth-failure response maps to the right error, rather than to an
  unhandled exception.

The GitHub App token flow has the same gap. A mock returns only what its author believes the real host
returns.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A real disposable test repository.** Use a small, stable, low-privilege public repository as the
  fetch target. A private repository with a dedicated throwaway GitHub App installed is the alternative.
  - `_GitHubTransport` already works unauthenticated when no token is configured. GitHub's
    commits/tarball endpoints also need no auth for a public repository. The public-repo option
    therefore needs no credential to fetch at all.
  - A personal access token (PAT) there buys one thing: the higher authenticated rate limit. GitHub's
    unauthenticated cap is 60 requests/hour, which is tight on a shared Actions-runner IP. The PAT
    stays optional.
  - Store whichever credential is actually used as a dedicated repository secret. For the public-repo
    option it is a fine-grained, read-only PAT scoped to the one repository. For the private-repo
    option it is the throwaway App's private key.
  - Whoever owns the test repository (or the App) rotates the secret.
- **A live fetch test.** Drive `materialize()` through the real `_GitHubTransport` against that
  repository. Skip the test when network access or the relevant credential is unavailable. Assert that
  the fetched config tree matches what is actually in the repository.
- **Cover the error path for real too.** Where feasible, provoke a real 404 or 403: a deliberately
  wrong ref, or an unauthorized private repo. Do not rely on a monkeypatched `HTTPError` alone. The
  test confirms that the real error surface maps the way the `_GitHubTransport` tests assume.
- **Non-gating, permanently.** Land the new job as CI signal, following BE-0282's precedent of landing
  new backend coverage as signal first.
  - BE-0282's own `network (playwright)` job needs no secret. It therefore triggers safely on every
    `pull_request`, including a fork's.
  - This job needs a real credential. It triggers only on `push` to `main` (or `schedule`), never on
    `pull_request`. `roadmap-id.yml` relies on the same trigger restriction to keep its
    `AUTOMATION_BOT_PRIVATE_KEY` / `AUTOMATION_BOT_APP_ID` secrets away from a fork-triggered run.
  - A check that runs only after merge reports against `main`'s commit, not a PR's head SHA. It
    structurally cannot become a required status check the way BE-0282's job could.
  - That is the design's permanent shape, not a step toward requiring the job later. The only way to
    change it would be to expose the credential to a PR-triggered run.

## Alternatives considered

- **Trust the fake-transport tests, since the composition logic is thoroughly covered.** Composition
  correctness assumes that the transport handed it correct bytes in the first place. It says nothing
  about whether the real transport implementation produces those bytes from a real host.
- **Cover this indirectly through a GitHub App token-flow integration test alone.** That test verifies
  the token flow. This item verifies the fetch-and-materialize path built on top of the token flow.
  Both are needed, because a working token with a broken fetch, and a working fetch with a broken
  token, are each independently observable failure modes.

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
