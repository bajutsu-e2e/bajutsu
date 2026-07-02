**English** · [日本語](BE-XXXX-apikey-reveal-rbac-gate-ja.md)

# BE-XXXX — RBAC-gate the API-key reveal endpoint

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-apikey-reveal-rbac-gate.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

On a hosted `serve` deployment, `GET /api/apikey?reveal=1` returns the shared AI provider key
in full to any authenticated caller, regardless of role. This proposal RBAC-gates the reveal path
so only an admin or owner can read the raw key.

## Motivation

`bajutsu/serve/authz.py`'s `required_role` (line 147) computes the minimum role a request needs,
but it only inspects state-changing calls: `if method != "POST": return None`. `/api/apikey` is
listed in `_ADMIN_PATHS`, so a `POST` to it (setting the key) correctly requires the admin role —
but the reveal path is a `GET` with a query parameter (`bajutsu/serve/handler.py:162-163`,
`ops.api_key_info(state, bool(self._qs("reveal")))`), which `required_role` never gates because
it isn't a `POST`. `api_key_info` (`bajutsu/serve/operations.py:237-246`) then includes the full
key value in the JSON payload whenever `reveal` is truthy, with no further role check.

Severity is Low: the endpoint still requires a valid session or token (the general `_gate()`
auth check), and on the default local/single-user `serve` this only affects the operator. But on
a hosted, multi-role deployment (BE-0051, BE-0047), a `viewer` role — meant to be read-only over
run results — can read the shared AI provider key by hitting the reveal query parameter directly,
which is the exact key the `admin`-only `POST /api/apikey` is supposed to protect from
non-admins.

## Detailed design

1. **Extend `required_role` to cover `GET /api/apikey?reveal=1`.** Add a check alongside the
   existing `POST` branch: when `method == "GET"` and `path == "/api/apikey"` and the `reveal`
   query parameter is truthy, require the admin role. The unrevealed `GET` (masked preview only)
   keeps its current open-read behavior — it exposes no secret, only whether a key is set and a
   masked preview.
2. **Thread the query value into the RBAC check.** `required_role` currently takes only
   `(method, path)`; the reveal decision needs the query string too, so extend the transport-side
   call (`forbidden_for_role` / `_gate()` in `bajutsu/serve/handler.py`) to pass the parsed query
   for this one path, without changing the signature for every other caller.
3. **Verify no other read path leaks the same value.** `provider_info` and `config_info` are
   checked to confirm they never echo the raw key; only `api_key_info`'s `reveal` branch does.

## Alternatives considered

- **Move the reveal value out of `GET` entirely (require a `POST` to fetch it).** Rejected as a
  larger API shape change for marginal benefit — the fix is the same RBAC gate either way, and
  keeping it a `GET` (idempotent, no state change) matches how the rest of `serve`'s read
  endpoints work.
- **Redact the key everywhere and drop `reveal` altogether.** Rejected: an admin legitimately
  needs to copy the full key out of `serve` occasionally (e.g. to reuse it in another tool); the
  RBAC gate is a smaller, targeted fix that keeps that capability for the right role.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Extend `required_role` (or the transport gate) to require the admin role for
      `GET /api/apikey?reveal=1`.
- [ ] Confirm `provider_info` / `config_info` never echo the raw key.
- [ ] Add a regression test asserting a non-admin role gets 403 on the reveal path.

No PR has landed yet.

## References

`bajutsu/serve/authz.py:147` (`required_role`), `bajutsu/serve/handler.py:162-163`,
`bajutsu/serve/operations.py:237-246` (`api_key_info`). Related: BE-0051 (serve hardening for
hosting), BE-0047 (AI data sovereignty). Originates from the 2026-07-02 codebase-analysis report
(security).
