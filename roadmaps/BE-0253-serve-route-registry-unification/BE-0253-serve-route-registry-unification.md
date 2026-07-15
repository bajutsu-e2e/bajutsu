**English** · [日本語](BE-0253-serve-route-registry-unification-ja.md)

# BE-0253 — Unify the serve dual-backend route tables behind a declarative registry

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0253](BE-0253-serve-route-registry-unification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0253") |
| Implementing PR | [#1098](https://github.com/bajutsu-e2e/bajutsu/pull/1098), [#1108](https://github.com/bajutsu-e2e/bajutsu/pull/1108) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`serve` runs two HTTP backends behind one API surface: a stdlib `http.server` handler
(`bajutsu/serve/handler.py`) and, alongside it, a FastAPI app (`bajutsu/serve/server/app.py`). Every
endpoint is declared twice — once as a `match path` case in the stdlib handler's `do_GET`/`do_POST`
(`handler.py:186-444`) and once as an `@app.get`/`@app.post`/`@app.delete` route in `app.py:122-490`
— with both branches dispatching to the same `bajutsu.serve.operations` (`ops`) function. The
request-level auth gate, CSRF `Origin` check, `Host` allowlist, and hardening response headers are
*also* reimplemented independently in both backends (`handler.py:61-69,121-170,273-290` vs.
`app.py:69-118`). This item replaces the two hand-maintained route tables — and the duplicated
security enforcement around them — with one declarative registry that both backends consume.

## Motivation

The two route tables have already drifted, silently. The following endpoint groups exist in the
stdlib handler but have no counterpart in the FastAPI app, so a deployment that serves the FastAPI
backend 404s on them with no code anywhere declaring that this is intentional:

- `GET /flakiness` (`handler.py:237`)
- `GET /api/ant/login` and `POST /api/ant/login` (`handler.py:214,352`)
- `POST /api/enrich` (`handler.py:382`)
- `POST /api/codegen` (`handler.py:376`)
- `POST /api/capture/start`, `POST /api/capture/mark`, `POST /api/capture/finish`, and
  `GET /api/capture/screenshot` (`handler.py:388-393,266`)
- `POST /api/jobs/{id}/respond-human` (`handler.py:414`)
- `GET /runs/{id}/archive.zip` (`handler.py:264`) — the FastAPI app's `GET /runs/{rel:path}`
  (`app.py:128`) serves a run's static artifacts by path, but does not special-case `archive.zip`
  the way the stdlib handler's `_serve_run_archive` does, so a request for it falls through to
  `ops.run_file` looking for a literal file named `archive.zip` instead of generating the zip.

Some of these are plausibly local-only by design — `/api/capture/*` holds an in-process `Driver`
instance across the start/mark/finish sequence, which the stdlib handler's single-process model
supports but a horizontally-scaled FastAPI deployment may not; `/api/ant/login` shells out to a
local CLI. But nothing in the code *says* which endpoints are intentionally local-only versus
accidentally missing — the gap was found by manually diffing the two `match`/route lists against
each other, not by reading a declaration anywhere. A future endpoint added to one backend and
forgotten in the other fails the same way: silently, discoverable only by manually diffing two
~300-line files. The duplicated auth/CSRF/Host/header enforcement carries a sharper risk: the two
copies must be kept byte-compatible by hand for the security posture to actually match between
backends, and nothing checks that they do — a latent path to a security-relevant divergence, not
just a missing feature. Both problems have the same root cause (one behavior, two independent
implementations to keep in sync) and the same fix (make the one source of truth executable instead
of duplicated).

## Detailed design

The work is behavior-preserving for every route both backends already serve today; it only changes
where each route table's data lives, and makes today's silent drift an explicit, greppable
declaration. Five MECE parts:

1. **A single declarative route registry.** Introduce one table — a list of entries, each carrying
   the HTTP method, the path pattern (including the two backends' differing conventions for
   variable path segments, e.g. `/api/jobs/{job_id}` vs. `handler.py`'s manual
   `path.startswith(...)/endswith(...)` matching), the `ops.*` callable to dispatch to, and a small
   set of per-route flags: `needs_actor` (whether the handler must resolve and pass the requesting
   identity), `off_loop` (routes like SSE streaming or file serving that bypass the uniform
   JSON-response helper), and `local_only` (see point 4). This registry is the single source of
   truth both backends iterate; it does not itself execute a request.
2. **The stdlib handler dispatches from the registry.** `do_GET`/`do_POST`/`do_DELETE`
   (`handler.py:181-429,431-`) iterate the registry instead of a hand-written `match path`,
   resolving path parameters and calling the paired `ops` function the same way the current cases
   do. The per-route flags (`needs_actor`, `off_loop`) drive the same conditional logic
   (`self._actor()`, streaming vs. `self._json(...)`) that today is written out per case.
3. **`make_app` generates FastAPI routes from the registry.** `bajutsu/serve/server/app.py`'s route
   declarations (`app.py:122-490`) are replaced with a loop over the same registry that registers
   each entry as an `app.get`/`app.post`/`app.delete` (skipping `local_only` entries, see point 4),
   removing the hand-written `@app.<method>` decorator per endpoint.
4. **`local_only` turns silent drift into a declaration.** Every endpoint identified in *Motivation*
   as missing from the FastAPI app is triaged: an endpoint that is genuinely local-only by design
   (e.g. `/api/capture/*` and its in-process `Driver` lifetime, `/api/ant/login`'s local-CLI shell-out)
   is marked `local_only=True` in the registry, so the FastAPI generator in point 3 skips it
   deliberately and a reader can grep the registry to see the full list and why. An endpoint that
   turns out to have no such reason (this item does not presume in advance which of the list falls
   into which bucket — that triage is part of the work) is instead backfilled as a normal FastAPI
   route, closing the gap outright.
5. **Collapse the duplicated auth/CSRF/Host/header enforcement into one shared gate helper.** The
   logic in `handler.py:61-69` (hardening headers), `handler.py:121-170` (`_gate`, the auth check),
   and `handler.py:273-290` (`_csrf_ok`/`_host_ok`) is functionally identical to `app.py:69-118`'s
   `gate` middleware and `_hardened` helper. Extract one implementation of this policy — parameterized
   over how each backend reads a request's headers/cookies/method/path and writes a response — that
   both `handler.py` and `app.py` call, so the security posture is defined once instead of twice.

## Alternatives considered

- **Keep the two route tables and two security-enforcement copies as hand-maintained lists, and add
  a test that fails when they drift from each other.** Rejected as the primary fix: a test catches
  drift after the fact (and only if someone remembers to run it before the drift ships — this item's
  own motivating gap already shipped undetected), but it polices the duplication rather than removing
  it. A single registry makes the two backends structurally unable to drift, because there is only
  one list left to drift from.
- **Fix only the currently-missing endpoints (backfill them into `app.py` by hand) without building a
  registry.** Rejected: this closes today's known gap but leaves the same hand-duplication in place
  for the next endpoint anyone adds, so the same class of drift recurs. It also leaves the duplicated
  auth/CSRF/Host/header logic untouched, which is the sharper risk described in *Motivation*.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Define the declarative route registry (method, path pattern, `ops.*` callable,
      `off_loop`/`local_only`/`content_type` flags)
- [x] Rewrite the stdlib handler's `do_GET`/`do_POST`/`do_DELETE` to dispatch from the registry
- [ ] Generate `app.py`'s FastAPI routes from the registry in `make_app`
- [ ] Triage every endpoint missing from `app.py` today (`/flakiness`, `/api/ant/login`,
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

## References

- `bajutsu/serve/handler.py:61-69` (hardening headers), `:121-170` (`_gate`), `:181-291` (`do_GET`
  and the `_csrf_ok`/`_host_ok` helpers), `:292-429` (`do_POST`)
- `bajutsu/serve/server/app.py:69-118` (the `gate` middleware and `_hardened` helper), `:122-490`
  (the FastAPI route declarations)
- `bajutsu/serve/operations/` — the `ops.*` functions both backends dispatch to
- Related: BE-0134 (eliminated the serve-to-CLI flag-mirror drift the same way — deriving a
  hand-duplicated surface from a single source of truth instead of policing it with a test)
- Originates from a codebase-analysis pass over `bajutsu/serve` (technical debt).
