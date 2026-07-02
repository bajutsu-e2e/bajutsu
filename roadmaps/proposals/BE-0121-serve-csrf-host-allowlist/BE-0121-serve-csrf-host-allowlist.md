**English** · [日本語](BE-0121-serve-csrf-host-allowlist-ja.md)

# BE-0121 — Unconditional CSRF and Host-allowlist defenses for serve

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0121](BE-0121-serve-csrf-host-allowlist.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

`make serve`'s default posture — loopback, no token — leaves state-changing `POST` requests with
no Origin/CSRF check and no `Host` allowlist. This item makes both defenses unconditional, and
closes the gap where a config bound at runtime via the API is treated as operator-trusted.

## Motivation

`serve`'s CSRF defense only runs when a token is configured: `_csrf_ok` (`bajutsu/serve/handler.py:198`)
compares `Origin` against `Host`, but `do_POST` only calls it when `state.token is not None`
(`bajutsu/serve/handler.py:219`). In the default local run — the common case, since `make serve`
starts with no token — every state-changing `POST` is unguarded.

A `text/plain` `fetch` from a malicious page qualifies as a CORS "simple request", so the browser
sends it cross-origin without a preflight. A user running `make serve` who opens such a page in
another tab can, with no interaction beyond that page load, have it:

1. `POST /api/config` with a `git` spec, binding an attacker-controlled Git repository as the
   active config (`bajutsu/serve/operations.py:551`, `bind_git_config`).
2. `POST /api/run`, which builds the (missing) app binary by running that config's `build:` command
   through `shlex.split` (`bajutsu/serve/jobs.py:451`) — arbitrary code execution on the host, under
   the identity of whoever is running `serve`.

The uploaded-bundle path already treats `build` as untrusted: `start_run` sets `build = None` for an
uploaded config so its command is never executed (`bajutsu/serve/operations.py:761`, landed under
BE-0090). The Git-config path has no equivalent guard — the comment at
`bajutsu/serve/operations.py:753` states plainly that "a local/Git config is operator-trusted and
ungoverned, so it gets no flag." That assumption holds when the operator typed the Git spec
themselves; it does not hold when a cross-origin request bound the spec on their behalf.

Separately, there is no `Host` header allowlist. DNS rebinding — pointing a hostname the victim's
browser resolves to `127.0.0.1` after an initial same-origin-looking load — lets a page's
same-origin requests reach the loopback server anyway, so `GET /api/apikey?reveal=1` can exfiltrate
`ANTHROPIC_API_KEY` without ever needing the CSRF bypass above.

Both gaps sit in the same place BE-0051 (serve hardening for hosting) already hardens headers and
sessions, and both compound the config-source risk BE-0090 and BE-0108 (hosted config source
restriction) address from the "which config sources are safe" angle — this item closes the
transport-level hole that lets an untrusted source get bound in the first place.

## Detailed design

1. **Run CSRF/Origin checks unconditionally.** Move the `_csrf_ok()` check in `do_POST` out from
   under `if state.token is not None`, so every state-changing request is checked regardless of
   whether a token is configured. `_csrf_ok` already allows non-browser clients (no `Origin`
   header) through, so CLI/script access without a browser is unaffected; only cross-origin
   browser requests are blocked.
2. **Add a `Host` allowlist.** Reject requests whose `Host` header does not match the interface(s)
   `serve` was actually bound to (loopback names/addresses by default; the configured bind host
   otherwise) with a `4xx`, checked in the same request-gating path as the CSRF check. This closes
   the DNS-rebinding path to `/api/apikey?reveal=1` and every other endpoint, independent of the
   token/CSRF posture.
3. **Treat a Git config bound at runtime as untrusted by default.** When `bind_git_config`
   (`bajutsu/serve/operations.py:551`) binds a spec supplied through the API (as opposed to one
   pre-configured by the operator before `serve` started), mark the resulting config's `build`
   ungoverned-by-default: require an explicit opt-in (a `--allow-remote-build`-style flag, or a
   per-request confirmation surfaced in the UI) before `start_run` executes it, mirroring the
   `upload_exec` gating BE-0090 added for uploaded bundles. Absent the opt-in, `start_run` nulls
   `build` for a runtime-bound Git config exactly as it already does for uploads.
4. **Tests.** Extend the serve HTTP harness: a cross-origin `POST` is rejected with no token
   configured; a request with a mismatched `Host` header is rejected; a runtime-bound Git config's
   `build` is not executed without the opt-in.
5. **Docs.** Note the unconditional CSRF/Host defenses and the Git-config trust boundary in the
   serve hardening docs (`docs/` and `docs/ja/`).

No change touches the deterministic `run`/CI gate's pass/fail logic, the drivers, or the scenario
schema — this is entirely inside the serve transport and config-binding layer, and no LLM is
introduced anywhere.

## Alternatives considered

- **Keep the check token-gated and rely on `Host` allowlisting alone.** Rejected: the two defenses
  cover different attack shapes (cross-origin `fetch` vs. DNS rebinding), and the finding shows
  both are exploitable independently in the common no-token default; removing either leaves a real
  hole.
- **Trust the Git-config path because BE-0063 already confines it to a content-addressed cache.**
  Rejected: confinement stops path traversal on extraction, not arbitrary `build:` command
  execution — the cache safely materializes whatever repository the request pointed at, attacker
  content included.
- **Only warn in the UI when a Git config is bound via the API, without a server-side guard.**
  Rejected as cosmetic, for the same reason BE-0108 rejected a UI-only fix for the file browser: a
  hand-crafted request bypasses anything enforced only in the frontend.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] CSRF/Origin check (`_csrf_ok`) runs unconditionally in `do_POST`, regardless of token
- [ ] `Host` header allowlist added and enforced on every request
- [ ] Runtime-bound Git config's `build` ungoverned-by-default, gated behind explicit opt-in
- [ ] Tests: cross-origin POST rejected with no token, mismatched Host rejected, ungoverned Git
      `build` not executed without opt-in
- [ ] Docs updated (both languages)

No PR has landed yet.

## References

- `bajutsu/serve/handler.py:198` — `_csrf_ok`, the Origin/Host check.
- `bajutsu/serve/handler.py:219` — the token-gated call site that skips the check by default.
- `bajutsu/serve/operations.py:551` — `bind_git_config`.
- `bajutsu/serve/operations.py:753` — the comment stating Git/local configs are "operator-trusted
  and ungoverned."
- `bajutsu/serve/operations.py:761` — the existing `build = None` guard for uploaded bundles.
- `bajutsu/serve/jobs.py:451` — `shlex.split(job.build)`, the command execution this item gates.
- [BE-0051 — Serve hardening for hosting](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
- [BE-0090 — Uploaded-config command execution](../../implemented/BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md)
- [BE-0063 — Git config source](../../implemented/BE-0063-git-config-source/BE-0063-git-config-source.md)
- [BE-0108 — Hosted config source restriction](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)
- Originates from the 2026-07-02 codebase-analysis report (security).
