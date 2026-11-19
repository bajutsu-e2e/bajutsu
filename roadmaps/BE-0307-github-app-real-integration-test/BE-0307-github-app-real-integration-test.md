**English** · [日本語](BE-0307-github-app-real-integration-test-ja.md)

# BE-0307 — Real integration test for the GitHub App config-source token flow

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0307](BE-0307-github-app-real-integration-test.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0307") |
| Topic | Configuration sourcing |
| Related | [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md), [BE-0302](../BE-0302-config-source-real-repo-fetch/BE-0302-config-source-real-repo-fetch.md) |
<!-- /BE-METADATA -->

## Introduction

`github/app.py` backs the private-repo config source with a GitHub App installation-token flow. The
flow has three steps: sign a JSON Web Token (JWT), exchange it for an installation token, and use that
token to fetch config.

The JWT signing is genuinely tested against real `cryptography`, with a real RSA key and a real RS256
signature verification. The network leg is not. A hand-written `fake_fetch` drives the exchange in
`installation_token` entirely. The HTTP-error mapping in `_fetch` is tested by monkeypatching
`urllib.request.urlopen` to raise a canned error.

No test and no CI job completes this flow against a real GitHub App installation. This item adds one.

## Motivation

The signing math is solid, and this item does not touch it. What the current tests cannot prove is
that GitHub's real API accepts what Bajutsu sends and returns what the code assumes.

Three defects would escape the current suite:

- A JWT whose claim shape or algorithm GitHub rejects in practice.
- A clock-skew edge case in the `iat` / `exp` claims that shows up only against a real clock.
- An installation-token response whose real shape has drifted from the hand-typed `{"id": 999}` /
  `{"token": "..."}` fixtures.

None of the exchange ever leaves the process, so the suite cannot catch any of them.

The private-repo config source (`config_source.py`) is a secondary feature. The token flow underneath
it is exactly the kind of external-integration surface that a mock cannot validate by construction. A
mock returns only what its author believes GitHub returns.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A throwaway GitHub App for CI.** Register a minimal test-only GitHub App, scoped to nothing beyond
  what `installation_token` needs. Install it on a disposable or low-privilege test repository, and
  store its private key as a repository secret.
  [BE-0302](../BE-0302-config-source-real-repo-fetch/BE-0302-config-source-real-repo-fetch.md)'s
  private-repo-fetch option needs the same kind of throwaway App on the same kind of repository. Reuse
  one App and repository pair across both items rather than standing up two.
- **A key-gated live integration test.** Add a test that runs the real `_app_jwt` →
  `installation_token` → `_fetch` chain against that real App and repository. The test is skipped when
  the secret is absent, so `make check` stays green and credential-free for every contributor.
- **Assert the real response shapes.** Confirm that the real installation-lookup response still
  carries the `id` field, and that the real access-tokens response still carries the `token` field.
  The flow reads these two fields through `_json_field` from two distinct endpoints. The assertion
  catches a schema drift that the hand-typed fixtures would not.
- **Non-gating first.** Land the new job as CI signal, following the precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md),
  before considering it required.

## Alternatives considered

- **Trust the mocked HTTP tests, since the error-mapping logic is unit-tested.** The error mapping can
  be correct for a canned `HTTPError`. That says nothing about whether GitHub's real API ever returns
  that error in that shape, or whether GitHub accepts the JWT that triggers the error in the first
  place.
- **Skip a throwaway App and test only against GitHub's public documentation of the expected
  shapes.** In practice, documentation drifts from the implemented API. Only a real call observes
  GitHub's current behavior rather than its stated behavior.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Register a throwaway GitHub App for CI, scoped to a disposable test repository.
- [ ] Add a key-gated live integration test for the JWT → installation-token → fetch chain.
- [ ] Confirm the real installation-lookup and access-tokens responses still carry the `id` and
  `token` fields the flow reads.
- [ ] Wire it into CI as a non-gating signal.

## References

- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- [BE-0302 — Real-repository fetch verification for the config source](../BE-0302-config-source-real-repo-fetch/BE-0302-config-source-real-repo-fetch.md)
- `bajutsu/github/app.py`, `tests/test_github_app.py`
