**English** · [日本語](BE-XXXX-serve-oauth-real-handshake-ja.md)

# BE-XXXX — Real OAuth handshake verification for serve's GitHub login

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-oauth-real-handshake.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Verification & coverage |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md) |
<!-- /BE-METADATA -->

## Introduction

`serve/server/oauth.py`'s `GitHubOAuthClient` wraps Authlib's `OAuth2Client` to drive `serve`'s
GitHub login. No test in `tests/serve/test_oauth.py` ever instantiates or drives the real class —
every test substitutes `FakeOAuthClient`, `_RaisingOAuthClient`, or a hand-written `_PagingClient`
standing in for `httpx`. This item captures one real handshake against a throwaway GitHub OAuth App
and replays it through the real client code.

## Motivation

The fakes prove `serve`'s login flow correctly calls whatever `GitHubOAuthClient` returns; they
prove nothing about whether the real class, wrapping the real Authlib `OAuth2Client` over real
`httpx`, actually completes a token exchange against GitHub, or whether `_fetch_orgs` parses GitHub's
real org-list response shape at all, as opposed to the hand-built `_PagingClient`'s stand-in for it.
A version bump in Authlib changing its token-exchange call signature or a change in GitHub's OAuth
response shape would both be invisible to this suite, because none of it ever leaves the process —
the exact failure mode a mocked-client test suite cannot catch by construction.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A throwaway GitHub OAuth App, captured once.** Register a minimal test-only OAuth App (its
  client ID/secret used only for a one-time manual capture, never stored in CI). A maintainer
  manually completes one real authorization-code exchange against it and saves the raw HTTP
  responses — the token exchange, the user-identity lookup, and a representative org-list page —
  with the real access token and every real identifying field in the responses (login, org
  identifiers, numeric user id, email, name, avatar URL, and any other PII GitHub returns)
  replaced by fixture values before the responses are committed.
- **Replay the captured responses through the real code, not a live CI login.** Getting the initial
  authorization `code` requires a human completing GitHub's hosted login-and-consent page —
  scripting that from a CI runner means logging into a live account programmatically, risking
  GitHub's 2FA/device-verification/CAPTCHA challenges firing unpredictably on CI IPs — exactly the
  kind of flakiness this item exists to avoid, and a far more sensitive secret to risk than the
  OAuth client secret. Instead, intercept at the `httpx` transport boundary (`respx` or a custom
  `httpx.MockTransport`) and replay the captured real responses back through the real
  `GitHubOAuthClient`/`OAuth2Client`/`_fetch_orgs`, so an Authlib call-signature break against that
  known-real shape is still caught with no live network call or credential needed in CI — though a
  *future* GitHub response-shape change stays invisible until the fixture is recaptured.
- **Keep the mocked-client tests as they are.** `FakeOAuthClient`, `_RaisingOAuthClient`, and
  `_PagingClient` already cover the login flow's own logic and its error paths; this item adds a
  captured-real-response fixture alongside them rather than replacing them.

## Alternatives considered

- **Drive a real, scripted headless browser login in CI.** This alternative was the first design
  considered here, but the initial authorization `code` can only come from a human completing GitHub's hosted
  login-and-consent page — scripting that from CI means holding and driving a live account's credentials, with
  GitHub's anti-automation defenses (2FA, device verification, CAPTCHA) able to fire unpredictably.
  That is a worse flakiness and secret-handling problem than the one this item sets out to solve.
- **Trust the mocked-client tests, since the login flow's own logic is unit-tested.** The flow logic
  being correct for a canned client response says nothing about whether the real Authlib/`httpx`
  stack actually parses what GitHub's real API returns, which is the property this item verifies.
- **Rely on Authlib's own test suite for the OAuth2Client contract.** Authlib's tests cover Authlib;
  they say nothing about whether `serve`'s own `GitHubOAuthClient` wrapper and `_fetch_orgs` pagination
  logic correctly handle a real GitHub response.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Register a throwaway GitHub OAuth App and manually capture one real token-exchange,
  user-identity, and org-list response.
- [ ] Replay the captured responses through the real `GitHubOAuthClient`/`_fetch_orgs` via an
  `httpx` transport intercept.
- [ ] Keep the mocked-client tests as they are.

## References

- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/serve/server/oauth.py`, `tests/serve/test_oauth.py`
  (`FakeOAuthClient`, `_RaisingOAuthClient`, `_PagingClient`)
