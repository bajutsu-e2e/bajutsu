**English** · [日本語](BE-0115-inprocess-collector-auth-ja.md)

# BE-0115 — Authenticate the in-process iOS network collector

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0115](BE-0115-inprocess-collector-auth.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0115") |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

The in-process network collector that receives an iOS app's traffic over HTTP accepts any POST
that reaches its loopback port, with no authentication. This proposal adds a shared token so only
the app instance a run launched can report exchanges into it.

## Motivation

`NetworkCollector` (`bajutsu/network.py:63-138`) starts a `ThreadingHTTPServer` bound to
`127.0.0.1` on an ephemeral port (`start()`, line 108), and its handler's `do_POST`
(`_make_handler`, `bajutsu/network.py:145-160`) parses and stores any JSON body it receives with
no check of who sent it — any process on the same machine that can reach the bound port can POST
fabricated exchanges into a running scenario, which the pipeline treats as observed network
traffic for `request`/`response` assertions and the `network.json` evidence artifact
(`bajutsu/scenario/models/assertions.py:76`, `bajutsu/runner/pipeline.py:189`).

Severity is Low: the collector is loopback-only (`127.0.0.1`, never `0.0.0.0`), so the exposure
is limited to another process already running on the same machine as the Simulator — a
meaningfully smaller attack surface than a network-reachable endpoint, and consistent with the
Simulator's own trust model (the app and the collector already share the same host loopback by
design, per `bajutsu/network.py`'s module docstring). Still, any other local process — a second
test run, an unrelated tool, malware — can inject false exchanges to make a `request` assertion
pass or fail incorrectly, undermining the determinism the runner otherwise guarantees.

## Detailed design

1. **Generate a per-run shared token** when the collector starts, alongside the ephemeral port
   already allocated in `start()`.
2. **Inject the token into the app** the same way the port is injected today (`BAJUTSU_COLLECTOR`
   launch env) — e.g. a sibling `BAJUTSU_COLLECTOR_TOKEN` env var — so the Swift-side sender
   (`BajutsuKit`) can attach it to each POST (a header, matching how `serve`'s `Authorization:
   Bearer` token check works).
3. **Check the token in `do_POST`** before parsing/storing the body, using a constant-time
   comparison (`secrets.compare_digest`, matching the pattern `serve`'s own token check already
   uses); reject a missing or mismatched token with a 401/403 rather than silently dropping it,
   so a misconfigured client fails loudly instead of a run silently missing exchanges.
4. **Scope the `BajutsuKit` change to the collector's HTTP sender** — the token travels the same
   path the port already does, so no new configuration surface is needed on the scenario/CLI
   side.

## Alternatives considered

- **Leave it loopback-only with no token, since the blast radius is already local-only.**
  Rejected: "another local process" is not a hypothetical on a shared CI runner or a developer
  machine running multiple tools, and the fix (a shared token, mirroring `serve`'s own auth
  pattern) is inexpensive relative to the determinism risk of an unauthenticated write into a
  run's evidence stream.
- **Bind the collector to a Unix domain socket instead of a TCP port.** Rejected as a larger
  platform-level change: the app-side POST path is HTTP over TCP by design (`BajutsuKit` runs
  inside the Simulator's app process), and switching transports would touch both the Swift SDK
  and the Python receiver for a benefit (process-level isolation) the token already achieves at
  the application layer.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Generate and inject a per-run shared token alongside `BAJUTSU_COLLECTOR`.
- [ ] Check the token in `NetworkCollector`'s `do_POST` with a constant-time comparison.
- [ ] Update `BajutsuKit`'s HTTP sender to attach the token to each POST.
- [ ] Add a regression test asserting an unauthenticated/mismatched POST is rejected.

No PR has landed yet.

## References

`bajutsu/network.py:63-160` (`NetworkCollector`, `_make_handler`),
`bajutsu/scenario/models/assertions.py:76`, `bajutsu/runner/pipeline.py:189`. Related: BE-0020
(multi-backend evidence fallback). Originates from the 2026-07-02 codebase-analysis report
(security).
