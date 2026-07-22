**English** · [日本語](BE-XXXX-serve-oauth-real-handshake-ja.md)

# BE-XXXX — Real OAuth handshake verification for serve's GitHub login

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-oauth-real-handshake.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Hosting the web UI (cloud / self-hosted) |
| Related | [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md) |
<!-- /BE-METADATA -->

## Introduction

`serve/server/oauth.py`'s `GitHubOAuthClient` wraps Authlib's `OAuth2Client` to drive `serve`'s
GitHub login. No test in `tests/serve/test_oauth.py` ever instantiates or drives the real class —
every test substitutes `FakeOAuthClient`, `_RaisingOAuthClient`, or a hand-written `_PagingClient`
standing in for `httpx`. This item adds a real handshake test against a throwaway GitHub OAuth App.

## Motivation

The fakes prove `serve`'s login flow correctly calls whatever `GitHubOAuthClient` returns; they
prove nothing about whether the real class, wrapping the real Authlib `OAuth2Client` over real
`httpx`, actually completes a token exchange against GitHub, or whether `_fetch_orgs`'s pagination
logic holds up against GitHub's real paginated response headers rather than the hand-built
`_PagingClient`'s. A version bump in Authlib changing its token-exchange call signature, a change in
GitHub's OAuth response shape, or a redirect/cookie-domain misconfiguration would all be invisible to
this suite, because none of it ever leaves the process — the exact failure mode a mocked-client test
suite cannot catch by construction.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A throwaway GitHub OAuth App for CI.** Register a minimal test-only OAuth App with its client
  secret stored as a repository secret, scoped to a disposable test account or organization.
- **A key-gated live handshake test.** Drive `GitHubOAuthClient` through a real (scripted, headless)
  authorization-code exchange against that App, skipped when the secret is absent, asserting a real
  access token comes back and `_fetch_orgs` parses a real paginated response.
- **Non-gating first.** Land the new job as CI signal, following the precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md),
  before considering it required.

## Alternatives considered

- **Trust the mocked-client tests, since the login flow's own logic is unit-tested.** The flow logic
  being correct for a canned client response says nothing about whether the real Authlib/`httpx`
  stack actually completes a handshake against GitHub, which is the property this item verifies.
- **Rely on Authlib's own test suite for the OAuth2Client contract.** Authlib's tests cover Authlib;
  they say nothing about whether `serve`'s own `GitHubOAuthClient` wrapper and `_fetch_orgs` pagination
  logic correctly drive it against GitHub specifically.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Register a throwaway GitHub OAuth App for CI.
- [ ] Add a key-gated live handshake test for `GitHubOAuthClient` and `_fetch_orgs`.
- [ ] Wire it into CI as a non-gating signal.

## References

- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/serve/server/oauth.py`, `tests/serve/test_oauth.py`
  (`FakeOAuthClient`, `_RaisingOAuthClient`, `_PagingClient`)
