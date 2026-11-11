**English** · [日本語](BE-0121-serve-csrf-host-allowlist-ja.md)

# BE-0121 — Unconditional CSRF and Host-allowlist defenses for serve

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0121](BE-0121-serve-csrf-host-allowlist.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0121") |
| Implementing PR | [#641](https://github.com/bajutsu-e2e/bajutsu/pull/641) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

The default posture of `make serve` binds loopback and sets no token. In that posture, state-changing `POST` requests face no Origin/CSRF check and no `Host` allowlist. This item makes both defenses unconditional. It also closes a gap: a config bound at runtime through the API counts as operator-trusted, although the operator never vouched for it.

## Motivation

`serve`'s CSRF defense runs only when a token is configured. `_csrf_ok` (`bajutsu/serve/handler.py:198`) compares `Origin` against `Host`. But `do_POST` calls it only when `state.token is not None` (`bajutsu/serve/handler.py:219`). `make serve` starts with no token, so the common default run leaves every state-changing `POST` unguarded.

A `text/plain` `fetch` from a malicious page qualifies as a CORS "simple request", so the browser sends it cross-origin without a preflight. Suppose a user runs `make serve` and opens such a page in another tab. The page load alone lets the page do two things.

1. `POST /api/config` with a `git` spec, which binds an attacker-controlled Git repository as the active config (`bind_git_config`, `bajutsu/serve/operations.py:551`).
2. `POST /api/run`, which builds the missing app binary by running that config's `build:` command through `shlex.split` (`bajutsu/serve/jobs.py:451`). The result is arbitrary code execution on the host, under the identity of whoever runs `serve`.

The uploaded-bundle path already treats `build` as untrusted. `start_run` sets `build = None` for an uploaded config, so its command never runs (`bajutsu/serve/operations.py:761`, landed under BE-0090). The Git-config path has no equivalent guard. The comment at `bajutsu/serve/operations.py:753` states plainly that "a local/Git config is operator-trusted and ungoverned, so it gets no flag."

That assumption holds when the operator typed the Git spec. It fails when a cross-origin request bound the spec on the operator's behalf.

Separately, no `Host` header allowlist exists. DNS rebinding works like this: after an initial load that looks same-origin, a hostname resolves in the victim's browser to `127.0.0.1`. The page's same-origin requests then reach the loopback server anyway. `GET /api/apikey?reveal=1` can thus exfiltrate `ANTHROPIC_API_KEY` without the CSRF bypass above.

Both gaps sit where BE-0051 (serve hardening for hosting) already hardens headers and sessions. Both also compound the config-source risk that BE-0090 and BE-0108 (hosted config source restriction) address from the "which config sources are safe" angle. This item closes the transport-level hole that lets an untrusted source get bound in the first place.

## Detailed design

1. **Run CSRF/Origin checks unconditionally.** Move the `_csrf_ok()` check in `do_POST` out from under `if state.token is not None`. Every state-changing request then gets checked, whether or not a token is configured. `_csrf_ok` already lets non-browser clients (no `Origin` header) through. CLI and script access without a browser is unaffected, and cross-origin browser requests alone are blocked.
2. **Add a `Host` allowlist.** Reject a request with a `4xx` when its `Host` header does not match the interface(s) that `serve` bound to. By default these are the loopback names and addresses. Otherwise they are the configured bind host. Run this check in the same request-gating path as the CSRF check. It closes the DNS-rebinding path to `/api/apikey?reveal=1` and to every other endpoint, independent of the token/CSRF posture.
3. **Treat a Git config bound at runtime as untrusted by default.** `bind_git_config` (`bajutsu/serve/operations.py:551`) may bind a spec that arrived through the API. The alternative is a spec the operator pre-configured before `serve` started. For the API case, mark the resulting config's `build` ungoverned-by-default.
   `start_run` then needs an explicit opt-in before it executes that `build`. The opt-in is a `--allow-remote-build`-style flag, or a per-request confirmation in the UI. This mirrors the `upload_exec` gating that BE-0090 added for uploaded bundles. Without the opt-in, `start_run` nulls `build` for a runtime-bound Git config, as it already does for uploads.
4. **Tests.** Extend the serve HTTP harness with three cases. A cross-origin `POST` is rejected with no token configured. A request with a mismatched `Host` header is rejected. A runtime-bound Git config's `build` does not run without the opt-in.
5. **Docs.** Note the unconditional CSRF/Host defenses and the Git-config trust boundary in the serve hardening docs (`docs/` and `docs/ja/`).

No change touches the pass/fail logic of the deterministic `run`/CI gate, the drivers, or the scenario schema. The work stays inside the serve transport and config-binding layer. No LLM enters anywhere.

## Alternatives considered

- **Keep the check token-gated and rely on `Host` allowlisting alone.** Rejected. The two defenses cover different attack shapes: cross-origin `fetch` and DNS rebinding. The finding shows that each is exploitable independently in the common no-token default. Dropping either leaves a real hole.
- **Trust the Git-config path because BE-0063 already confines it to a content-addressed cache.** Rejected. The confinement stops path traversal on extraction. It does not stop arbitrary `build:` command execution. The cache safely materializes whatever repository the request pointed at, attacker content included.
- **Warn in the UI when a Git config is bound via the API, without a server-side guard.** Rejected as cosmetic, for the reason BE-0108 gave when it rejected a UI-only fix for the file browser. A hand-crafted request bypasses anything that the frontend alone enforces.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] CSRF/Origin check (`_csrf_ok`) runs unconditionally in `do_POST`, regardless of token
- [x] `Host` header allowlist added and enforced on every request
- [x] Runtime-bound Git config's `build` ungoverned-by-default, gated behind explicit opt-in
- [x] Tests: cross-origin POST rejected with no token, mismatched Host rejected, ungoverned Git
      `build` not executed without opt-in
- [x] Docs updated (both languages)

Log:

- **Unconditional CSRF/Host defenses + Git-config build trust boundary**: moved the `_csrf_ok`
  check out from under the token gate in `do_POST` and `_handle_upload`, so a cross-origin `POST` is
  blocked on the no-token loopback default too. Added a `Host`-header allowlist derived from the
  bound interface (`make_server` → `state.allowed_hosts`), enforced at the top of `_gate` on every
  request, closing the DNS-rebinding path to `/api/apikey`. Marked a Git config bound at runtime via
  the API untrusted (`state.git_config_from_api`) so the run/record/crawl dispatch nulls its
  `build:` command unless `--allow-remote-build` / `BAJUTSU_ALLOW_REMOTE_BUILD` opts in — a single
  `_governed_build` helper now gates all three dispatch paths, mirroring the uploaded-bundle guard.
  Applied the same unconditional CSRF check and Host allowlist to the FastAPI/`--asgi` gate
  (`server/app.py`, `make_asgi_server`), keeping the two transports' policy in lockstep.

## References

- `bajutsu/serve/handler.py:198` — `_csrf_ok`, the Origin/Host check.
- `bajutsu/serve/handler.py:219` — the token-gated call site that skips the check by default.
- `bajutsu/serve/operations.py:551` — `bind_git_config`.
- `bajutsu/serve/operations.py:753` — the comment stating Git/local configs are "operator-trusted
  and ungoverned."
- `bajutsu/serve/operations.py:761` — the existing `build = None` guard for uploaded bundles.
- `bajutsu/serve/jobs.py:451` — `shlex.split(job.build)`, the command execution this item gates.
- [BE-0051 — Serve hardening for hosting](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
- [BE-0090 — Uploaded-config command execution](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md)
- [BE-0063 — Git config source](../BE-0063-git-config-source/BE-0063-git-config-source.md)
- [BE-0108 — Hosted config source restriction](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)
- Originates from the 2026-07-02 codebase-analysis report (security).
