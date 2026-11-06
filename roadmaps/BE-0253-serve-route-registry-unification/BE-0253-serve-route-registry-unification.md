**English** · [日本語](BE-0253-serve-route-registry-unification-ja.md)

# BE-0253 — Unify the serve dual-backend route tables behind a declarative registry

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0253](BE-0253-serve-route-registry-unification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0253") |
| Implementing PR | [#1098](https://github.com/bajutsu-e2e/bajutsu/pull/1098), [#1108](https://github.com/bajutsu-e2e/bajutsu/pull/1108), [#1115](https://github.com/bajutsu-e2e/bajutsu/pull/1115) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`serve` runs two HTTP backends behind one API surface. One is a stdlib `http.server` handler
(`bajutsu/serve/handler.py`). The other is a FastAPI app (`bajutsu/serve/server/app.py`). Each
endpoint has two declarations:

- as a `match path` case in the stdlib handler's `do_GET` / `do_POST` (`handler.py:186-444`)
- as an `@app.get` / `@app.post` / `@app.delete` route in `app.py:122-490`

Both declarations dispatch to the same `bajutsu.serve.operations` (`ops`) function.

Both backends also reimplement the following independently (`handler.py:61-69,121-170,273-290`
versus `app.py:69-118`):

- the request-level auth gate
- the cross-site request forgery (CSRF) `Origin` check
- the `Host` allowlist
- the hardening response headers

This item replaces the two hand-maintained route tables, and the duplicated security enforcement
around them, with one declarative registry that both backends consume.

## Motivation

The two route tables have already drifted without anyone noticing. The stdlib handler serves the endpoints below,
and the FastAPI app has no counterpart for them. A deployment that serves the FastAPI backend
answers 404 on each of them. No code anywhere declares that this is intentional.

- `GET /flakiness` (`handler.py:237`)
- `GET /api/ant/login` and `POST /api/ant/login` (`handler.py:214,352`)
- `POST /api/enrich` (`handler.py:382`)
- `POST /api/codegen` (`handler.py:376`)
- `POST /api/capture/start`, `POST /api/capture/mark`, `POST /api/capture/finish`, and
  `GET /api/capture/screenshot` (`handler.py:388-393,266`)
- `POST /api/jobs/{id}/respond-human` (`handler.py:414`)
- `GET /runs/{id}/archive.zip` (`handler.py:264`)

The last endpoint needs a closer look. The FastAPI app's `GET /runs/{rel:path}` (`app.py:128`)
serves a run's static artifacts by path. It does not special-case `archive.zip` the way the stdlib
handler's `_serve_run_archive` does. A request for the archive falls through to
`ops.run_file`, which looks for a literal file named `archive.zip` instead of generating the zip.

Some of these endpoints are plausibly local-only by design. `/api/capture/*` holds an in-process
`Driver` instance across the start, mark, and finish sequence. The stdlib handler's single-process
model supports that, but a horizontally scaled FastAPI deployment may not. `/api/ant/login` shells
out to a local CLI.

Nothing in the code says which endpoints are intentionally local-only and which are missing by
oversight. A manual diff of the two `match` and route lists found the gap. No declaration exposed it.

A future endpoint added to one backend and forgotten in the other fails the same way. The failure
is silent. A reader can find it solely by diffing two files of about 300 lines each by hand.

The duplicated auth, CSRF, Host, and header enforcement carries a sharper risk. The two copies must
stay byte-compatible by hand for the security posture to match between the backends. Nothing
checks that they do. That is a latent path to a security-relevant divergence, beyond a missing
feature.

Both problems have the same root cause: one behavior with two independent implementations to keep
in sync. They also have the same fix, which makes the one source of truth executable instead of
duplicated.

## Detailed design

The work preserves behavior for every route that both backends already serve today. It changes only
where each route table's data lives. It also turns today's unnoticed drift into an explicit, greppable
declaration. The work has five MECE parts.

1. **A single declarative route registry.** Introduce one table, a list of entries. Each entry
   carries the HTTP method, the path pattern, the `ops.*` callable to dispatch to, and a small set of
   per-route flags.
   - The path pattern covers the two backends' differing conventions for variable path segments.
     For example, FastAPI writes `/api/jobs/{job_id}`, whereas `handler.py` matches by hand with
     `path.startswith(...)` and `endswith(...)`.
   - `needs_actor` says whether the handler must resolve and pass the requesting identity.
   - `off_loop` marks routes, such as SSE streaming or file serving, that bypass the uniform
     JSON-response helper.
   - `local_only` is described in part 4.

   The registry is the single source of truth that both backends iterate. It does not execute a
   request itself.
2. **The stdlib handler dispatches from the registry.** `do_GET`, `do_POST`, and `do_DELETE`
   (`handler.py:181-429,431-`) iterate the registry instead of a hand-written `match path`. They
   resolve path parameters and call the paired `ops` function the same way the current cases do. The
   per-route flags (`needs_actor`, `off_loop`) drive the same conditional logic that is written out
   per case today: `self._actor()`, and streaming versus `self._json(...)`.
3. **`make_app` generates FastAPI routes from the registry.** The route declarations in
   `bajutsu/serve/server/app.py` (`app.py:122-490`) are replaced with a loop over the same registry.
   The loop registers each entry as an `app.get`, `app.post`, or `app.delete`, and skips `local_only`
   entries (see part 4). The hand-written `@app.<method>` decorator per endpoint disappears.
4. **`local_only` turns silent drift into a declaration.** Every endpoint that *Motivation* lists as
   missing from the FastAPI app is triaged. This item does not presume in advance which endpoint
   falls into which bucket, so the triage is part of the work.
   - An endpoint that is genuinely local-only by design is marked `local_only=True` in the registry.
     Examples include `/api/capture/*`, with its in-process `Driver` lifetime, and `/api/ant/login`,
     with its local-CLI shell-out. The FastAPI generator in part 3 then skips the endpoint
     deliberately, and a reader can grep the registry for the full list and the reasons.
   - An endpoint with no such reason is backfilled as a normal FastAPI route, which closes the gap
     outright.
5. **Collapse the duplicated auth, CSRF, Host, and header enforcement into one shared gate
   helper.** Three pieces of `handler.py` are functionally identical to the `gate` middleware and
   `_hardened` helper in `app.py:69-118`:
   - the hardening headers (`handler.py:61-69`)
   - `_gate`, the auth check (`handler.py:121-170`)
   - `_csrf_ok` and `_host_ok` (`handler.py:273-290`)

   Extract one implementation of this policy. It is parameterized over how each backend reads a
   request's headers, cookies, method, and path, and how it writes a response. Both `handler.py` and
   `app.py` call the shared implementation, so the security posture is defined once instead of
   twice.

## Alternatives considered

- **Keep the two route tables and two security-enforcement copies as hand-maintained lists, and add
  a test that fails when they drift from each other.** Rejected as the primary fix. A test catches
  drift after the fact, and only if someone remembers to run it before the drift ships. The gap that
  motivates this item already shipped undetected. A test also polices the duplication instead of
  removing it. A single registry makes the two backends structurally unable to drift, because only
  one list is left to drift from.
- **Fix only the currently missing endpoints, backfilling them into `app.py` by hand, without
  building a registry.** Rejected. This closes today's known gap. It leaves the same hand
  duplication in place for the next endpoint anyone adds, so the same class of drift recurs. It also
  leaves the duplicated auth, CSRF, Host, and header logic untouched, which is the sharper risk
  described in *Motivation*.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Define the declarative route registry (method, path pattern, `ops.*` callable,
      `off_loop`/`local_only`/`content_type` flags)
- [x] Rewrite the stdlib handler's `do_GET`/`do_POST`/`do_DELETE` to dispatch from the registry
- [x] Generate `app.py`'s FastAPI routes from the registry in `make_app`
- [x] Triage every endpoint missing from `app.py` today (`/flakiness`, `/api/ant/login`,
      `/api/enrich`, `/api/codegen`, `/api/capture/*`, `/api/jobs/{id}/respond-human`,
      `/runs/{id}/archive.zip`): mark genuinely local-only ones `local_only=True`, backfill the rest
- [x] Collapse the duplicated auth/CSRF/Host/header enforcement into one shared gate helper used by
      both backends

### Log

- 2026-07-15 — Part 5 (shared gate helper) landed in [#1098](https://github.com/bajutsu-e2e/bajutsu/pull/1098):
  new framework-agnostic `bajutsu/serve/gate.py` (`HARDENING_HEADERS`, `allowed_hosts`/`host_allowed`,
  `csrf_ok`, `is_open`, `is_authorized`, `actor_for`) that both backends call; transport mechanics stay
  per-backend. Behavior-preserving. Parts 1–4 (the declarative route registry) follow in later slices.
- 2026-07-15 — Parts 1–2 (define the registry + stdlib dispatch) landed in [#1108](https://github.com/bajutsu-e2e/bajutsu/pull/1108):
  new framework-agnostic `bajutsu/serve/routes.py` — one `ROUTES` table (`Route` frozen dataclass, a
  backend-neutral `RequestCtx` Protocol, a `{name}`/`{name:path}` template matcher) that captures each
  route's `ops.*` call in a per-route `handle(state, ctx)` adapter. `handler.py`'s `do_GET`/`do_POST`/
  `do_DELETE` now dispatch from it. Behavior-preserving. Deviation from Part 1's sketch: the `needs_actor`
  flag is *not* materialized — whether a route resolves the actor is already expressed inside its adapter
  closure, so a separate boolean would be a second source of the same truth (the drift class this item
  removes); `off_loop`/`local_only` stay (the closure can't express them) and `content_type` selects a
  text response. `off_loop` routes (SSE, file serve, raw uploads, OAuth, login, index) are *declared* here
  but kept bespoke per backend. Part 3 (generate `app.py` from the registry) and Part 4 (triage the
  FastAPI-missing endpoints via `local_only`) follow in the next slice.
- 2026-07-15 — Parts 3–4 (generate `app.py` from the registry + triage) landed in
  [#1115](https://github.com/bajutsu-e2e/bajutsu/pull/1115), completing the item. `make_app`
  now iterates `ROUTES` and registers every entry that carries a `handle` and is not `local_only`,
  replacing ~40 hand-written `@app.<method>` decorators; the off_loop routes (index, ES-module frontend,
  run file/range/archive, SSE, OAuth, login, raw-body uploads) stay bespoke. A backend-neutral
  `_FastapiCtx` mirrors the stdlib `_StdlibCtx`. Part 4 triage: `/api/ant/login` (machine-global
  credential, already 403s when hosted) and `/api/capture/*` (an in-process `Driver` across
  start/mark/finish) are marked `local_only=True` and skipped; every other endpoint previously missing
  from `app.py` — `/flakiness`, `/api/enrich`, `/api/codegen`, `/api/jobs/{id}/respond-human`, the
  never-mirrored `/api/scenario/apply-selector` and `/api/scenario/enrich-apply`, and `/runs/{id}/archive.zip`
  (backfilled bespoke) — is now served, closing the silent drift. The path-param `unquote` moved from the
  registry closures into each ctx (`path_param` now returns the decoded segment on both backends), so the
  two can't drift on how a percent-encoded segment reaches its `ops` call. Behavior-preserving, except the
  hosted backend now rejects a malformed request body with the stdlib's `400` rather than FastAPI's `422`
  (an untested error path, aligned deliberately) and `?purge` follows the stdlib's `== "true"` contract
  already shipped in Part 2 (the SPA never sends `?purge`).

## References

- `bajutsu/serve/handler.py:61-69` (hardening headers), `:121-170` (`_gate`), `:181-291` (`do_GET`
  and the `_csrf_ok`/`_host_ok` helpers), `:292-429` (`do_POST`)
- `bajutsu/serve/server/app.py:69-118` (the `gate` middleware and `_hardened` helper), `:122-490`
  (the FastAPI route declarations)
- `bajutsu/serve/operations/` — the `ops.*` functions both backends dispatch to
- Related: BE-0134 (eliminated the serve-to-CLI flag-mirror drift the same way — deriving a
  hand-duplicated surface from a single source of truth instead of policing it with a test)
- Originates from a codebase-analysis pass over `bajutsu/serve` (technical debt).
