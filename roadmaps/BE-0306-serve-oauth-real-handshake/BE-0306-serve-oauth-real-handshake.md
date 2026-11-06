**English** · [日本語](BE-0306-serve-oauth-real-handshake-ja.md)

# BE-0306 — Real OAuth handshake verification for serve's GitHub login

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0306](BE-0306-serve-oauth-real-handshake.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0306") |
| Topic | Verification & coverage |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md) |
<!-- /BE-METADATA -->

## Introduction

`GitHubOAuthClient` in `serve/server/oauth.py` wraps Authlib's `OAuth2Client` to drive `serve`'s GitHub
login. No test in `tests/serve/test_oauth.py` instantiates or drives the real class. Every test
substitutes `FakeOAuthClient`, `_RaisingOAuthClient`, or a hand-written `_PagingClient` that stands in
for `httpx`.

This item captures one real handshake against a throwaway GitHub OAuth App. It then replays the
captured handshake through the real client code.

## Motivation

The fakes prove that `serve`'s login flow correctly calls whatever `GitHubOAuthClient` returns. They
leave two questions open:

- Whether the real class, which wraps the real Authlib `OAuth2Client` over real `httpx`, completes a
  token exchange against GitHub.
- Whether `_fetch_orgs` parses GitHub's real org-list response shape at all, as opposed to the shape
  that the hand-built `_PagingClient` stands in for.

Two changes would be invisible to this suite: a version bump in Authlib that changes its
token-exchange call signature, and a change in GitHub's OAuth response shape. None of the suite ever
leaves the process. A mocked-client test suite cannot catch this failure mode by construction.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A throwaway GitHub OAuth App, captured once.** Register a minimal test-only OAuth App. Its client
  ID and secret serve only a one-time manual capture and are never stored in CI.
  - A maintainer manually completes one real authorization-code exchange against the App. The
    maintainer saves the raw HTTP responses: the token exchange, the user-identity lookup, and a
    representative org-list page.
  - Before the responses are committed, fixture values replace the real access token and every real
    identifying field. Those fields are the login, org identifiers, numeric user id, email, name,
    avatar URL, and any other PII GitHub returns.
- **Replay the captured responses through the real code, not a live CI login.** The initial
  authorization `code` requires a human to complete GitHub's hosted login-and-consent page.
  - Scripting that from a CI runner means logging into a live account programmatically. GitHub's 2FA,
    device-verification, and CAPTCHA challenges could then fire unpredictably on CI IPs.
  - That is exactly the kind of flakiness this item exists to avoid. It also risks a far more
    sensitive secret than the OAuth client secret.
  - Instead, intercept at the `httpx` transport boundary (`respx` or a custom `httpx.MockTransport`).
    Replay the captured real responses through the real `GitHubOAuthClient`, `OAuth2Client`, and
    `_fetch_orgs`.
  - An Authlib call-signature break against that known-real shape is then still caught, with no live
    network call or credential needed in CI. A *future* GitHub response-shape change stays invisible
    until the fixture is recaptured.
- **Keep the mocked-client tests as they are.** `FakeOAuthClient`, `_RaisingOAuthClient`, and
  `_PagingClient` already cover the login flow's own logic and its error paths. This item adds a
  captured-real-response fixture alongside them and does not replace them.

## Alternatives considered

- **Drive a real, scripted headless browser login in CI.** This was the first design considered here.
  The initial authorization `code` can only come from a human completing GitHub's hosted
  login-and-consent page. Scripting that from CI means holding and driving a live account's
  credentials, with GitHub's anti-automation defenses (2FA, device verification, CAPTCHA) able to fire
  unpredictably. That approach carries a worse flakiness and secret-handling problem than the one this
  item sets out to solve.
- **Trust the mocked-client tests, since the login flow's own logic is unit-tested.** The flow logic
  can be correct for a canned client response. That says nothing about whether the real Authlib and
  `httpx` stack parses what GitHub's real API returns, and this item verifies that property.
- **Rely on Authlib's own test suite for the OAuth2Client contract.** Authlib's tests cover Authlib.
  They say nothing about whether `serve`'s own `GitHubOAuthClient` wrapper and `_fetch_orgs`
  pagination logic handle a real GitHub response correctly.

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
