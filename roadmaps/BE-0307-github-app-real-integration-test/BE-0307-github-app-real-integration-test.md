**English** · [日本語](BE-0307-github-app-real-integration-test-ja.md)

# BE-0307 — Real integration test for the GitHub App config-source token flow

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0307](BE-0307-github-app-real-integration-test.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0307") |
| Topic | Configuration sourcing |
| Related | [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md), [BE-0302](../BE-0302-config-source-real-repo-fetch/BE-0302-config-source-real-repo-fetch.md) |
<!-- /BE-METADATA -->

## Introduction

`github/app.py` backs the private-repo config source with a GitHub App installation-token flow: sign
a JSON Web Token (JWT), exchange it for an installation token, use the token to fetch config. The JWT signing itself
is genuinely tested against real `cryptography` (a real RSA key, a real RS256 signature
verification). The network leg is not: `installation_token`'s exchange is driven entirely by a
hand-written `fake_fetch`, and `_fetch`'s HTTP-error mapping is tested by monkeypatching
`urllib.request.urlopen` to raise a canned error. No test, and no CI job, ever completes this flow
against a real GitHub App installation. This item adds one.

## Motivation

The signing math is solid and this item does not touch it. What it cannot prove is that GitHub's
real API accepts what Bajutsu sends and returns what the code assumes. A JWT whose claim shape or
algorithm GitHub rejects in practice, a clock-skew edge case in the `iat`/`exp` claims that only
shows up against a real clock, or an installation-token response whose real shape has drifted from
the hand-typed `{"id": 999}` / `{"token": "..."}` fixtures — none of these would be caught by the
current suite, because none of the exchange ever leaves the process. The private-repo config source is a
secondary feature (`config_source.py`), but the token flow underneath it is exactly the kind of
external-integration surface a mock cannot validate by construction: the mock only ever returns
what the person who wrote it believes GitHub returns.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A throwaway GitHub App for CI.** Register a minimal test-only GitHub App — scoped to nothing
  beyond what `installation_token` needs — installed on a disposable or low-privilege test
  repository, with its private key stored as a repository secret.
  [BE-0302](../BE-0302-config-source-real-repo-fetch/BE-0302-config-source-real-repo-fetch.md)'s
  private-repo-fetch option needs the same kind of throwaway App on the same kind of repository;
  reuse one App/repository pair across both items rather than standing up two.
- **A key-gated live integration test.** Add a test that runs the real `_app_jwt` →
  `installation_token` → `_fetch` chain against that real App and repository, skipped when the
  secret is absent so `make check` stays green and credential-free for every contributor.
- **Assert the real response shapes.** Confirm the real installation-lookup response still carries
  the `id` field and the real access-tokens response the `token` field — the two fields the flow
  reads through `_json_field` from two distinct endpoints — catching a schema drift the hand-typed
  fixtures would not.
- **Non-gating first.** Land the new job as CI signal, following the precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md),
  before considering it required.

## Alternatives considered

- **Trust the mocked HTTP tests, since the error-mapping logic is unit-tested.** Error mapping being
  correct for a canned `HTTPError` says nothing about whether GitHub's real API ever returns that
  error in that shape, or whether the JWT that triggers it is itself accepted in the first place.
- **Skip a throwaway App and test only against GitHub's public documentation of the expected
  shapes.** Documentation drifts from the implemented API in practice; a real call is the only check
  that actually observes GitHub's current behavior rather than its stated one.

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
