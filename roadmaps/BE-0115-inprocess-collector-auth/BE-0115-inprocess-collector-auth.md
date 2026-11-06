**English** · [日本語](BE-0115-inprocess-collector-auth-ja.md)

# BE-0115 — Authenticate the in-process iOS network collector

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0115](BE-0115-inprocess-collector-auth.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0115") |
| Implementing PR | [#690](https://github.com/bajutsu-e2e/bajutsu/pull/690) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

The in-process network collector receives an iOS app's traffic over HTTP. It accepts any POST that
reaches its loopback port, with no authentication. This proposal adds a shared token, so that
the collector accepts exchanges from the app instance a run launched and from no other process.

## Motivation

`NetworkCollector` (`bajutsu/network.py:63-138`) starts a `ThreadingHTTPServer` bound to
`127.0.0.1` on an ephemeral port (`start()`, line 108). Its handler's `do_POST` (`_make_handler`,
`bajutsu/network.py:145-160`) parses and stores any JSON body it receives, with no check of who sent
the body. Any process on the same machine that can reach the bound port can POST fabricated
exchanges into a running scenario. The pipeline treats those exchanges as observed network traffic
for `request`/`response` assertions and for the `network.json` evidence artifact
(`bajutsu/scenario/models/assertions.py:76`, `bajutsu/runner/pipeline.py:189`).

Severity is Low. The collector is loopback-only (`127.0.0.1`, never `0.0.0.0`), so the exposure
reaches merely another process already running on the same machine as the Simulator. That attack
surface is meaningfully smaller than a network-reachable endpoint. It is also consistent with the
Simulator's own trust model: by design, the app and the collector already share the same host
loopback, per the module docstring of `bajutsu/network.py`.

Still, any other local process can inject false exchanges: a second test run, an unrelated tool, or
malware. The false exchanges can make a `request` assertion pass or fail incorrectly. That
undermines the determinism the runner otherwise guarantees.

## Detailed design

1. **Generate a per-run shared token** when the collector starts, alongside the ephemeral port
   already allocated in `start()`.
2. **Inject the token into the app** the same way the port is injected today (the
   `BAJUTSU_COLLECTOR` launch env). An example is a sibling `BAJUTSU_COLLECTOR_TOKEN` env var. The
   Swift-side sender (`BajutsuKit`) can then attach the token to each POST as a header, matching how
   `serve`'s `Authorization: Bearer` token check works.
3. **Check the token in `do_POST`** before parsing or storing the body. Use a constant-time
   comparison (`secrets.compare_digest`), matching the pattern `serve`'s own token check already
   uses. Reject a missing or mismatched token with a 401/403 rather than silently dropping it. A
   misconfigured client then fails loudly, instead of a run silently missing exchanges.
4. **Scope the `BajutsuKit` change to the collector's HTTP sender.** The token travels the same
   path the port already does, so the scenario and CLI sides need no new configuration surface.

## Alternatives considered

- **Leave the collector loopback-only with no token, since the blast radius is already
  local-only.** Rejected. "Another local process" is not hypothetical on a shared CI runner or on a
  developer machine running multiple tools. The fix is a shared token that mirrors `serve`'s own
  auth pattern, and it is inexpensive relative to the determinism risk of an unauthenticated write
  into a run's evidence stream.
- **Bind the collector to a Unix domain socket instead of a TCP port.** Rejected as a larger
  platform-level change. The app-side POST path is HTTP over TCP by design (`BajutsuKit` runs inside
  the Simulator's app process). Switching transports would touch both the Swift SDK and the Python
  receiver. The benefit, process-level isolation, is one the token already achieves at the
  application layer.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Generate and inject a per-run shared token alongside `BAJUTSU_COLLECTOR`.
- [x] Check the token in `NetworkCollector`'s `do_POST` with a constant-time comparison.
- [x] Update `BajutsuKit`'s HTTP sender to attach the token to each POST.
- [x] Add a regression test asserting an unauthenticated/mismatched POST is rejected.

- [#690](https://github.com/bajutsu-e2e/bajutsu/pull/690) — implement the shared-token auth end to end (collector mints the token in
  `start()`, `pool` injects `BAJUTSU_COLLECTOR_TOKEN`, `do_POST` verifies it with
  `secrets.compare_digest`, `BajutsuKit` sends it as `Authorization: Bearer`), with
  regression tests for the accept/reject paths.

## References

`bajutsu/network.py:63-160` (`NetworkCollector`, `_make_handler`),
`bajutsu/scenario/models/assertions.py:76`, `bajutsu/runner/pipeline.py:189`. Related: BE-0020
(multi-backend evidence fallback). Originates from the 2026-07-02 codebase-analysis report
(security).
