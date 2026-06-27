**English** · [日本語](BE-0090-uploaded-config-command-execution-ja.md)

# BE-0090 — Govern command execution from uploaded bundle configs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0090](BE-0090-uploaded-config-command-execution.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Hosting the web UI (cloud / self-hosted) |
<!-- /BE-METADATA -->

## Introduction

[BE-0073](../../proposals/BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) lets a
browser user upload a `.zip` bundle that `serve` extracts and binds as the **active config** the
Replay / Record / Crawl tabs run from. An uploaded config is untrusted input, and BE-0073 already
refuses one part of it: a target's `build` command is **never** executed on the host (the bundle
ships a prebuilt binary; [DESIGN §1](../../../DESIGN.md)). But a config carries other fields that
also run a shell command on the `serve` host — `launchServer.cmd` and `mockServer.cmd` — and those
are **not** governed: when the bound bundle runs, the `bajutsu run` subprocess executes them as
written. This item defines an explicit, tier-aware policy for **every** command-execution field in an
uploaded config, so the upload path's security model is consistent and ready for multi-tenant hosting.

## Motivation

BE-0073 binds an uploaded bundle as the active config and runs the deterministic `run` against it.
Three target fields in a config spawn a shell command on the host:

- `build` — the on-demand build of `appPath` (`bajutsu/serve/jobs.py` `_build_app`).
- `launchServer.cmd` ([BE-0059](../../implemented/BE-0059-launch-target-server/BE-0059-launch-target-server.md)) —
  brings up the host behind `baseUrl` for the run, in its own process group.
- `mockServer.cmd` — starts the mock that stubs the app's dependencies.

BE-0073 closes the first door for uploads: `start_run` forces `build=None` when the active config is
an uploaded bundle (`state.upload` set), so an uploaded config's `build` never runs. It leaves the
other two **open** — `launchServer.cmd` and `mockServer.cmd` execute unchanged. The asymmetry is
real and was, in fact, load-bearing for the web case: a web bundle has **no** `appPath` binary, so
`launchServer` is the only way it can be self-contained (it serves the bundled static app at run
time), which is exactly why a blanket block isn't an option.

The gap matters at the deployment boundary, not on a laptop:

- **Tier-A (authenticated single-Mac `serve`).** Running an uploaded config's `launchServer.cmd` is
  no worse than running the uploaded `.app` binary the same bundle ships — the authenticated operator
  is already trusted to bring and run their own suite. Today's behavior is the *intended* model here.
- **Hosted / multi-tenant `serve`** ([BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
  [BE-0016](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)). An
  authenticated-but-untrusted tenant uploading a bundle whose config sets `launchServer.cmd: rm -rf …`
  (or anything else) is **arbitrary command execution on the host** — a remote-code-execution vector.
  BE-0073 is explicitly scoped to single-Mac Tier-A and defers multi-tenant isolation to BE-0015 /
  BE-0016, so this isn't a BE-0073 bug; it's the seam those items must close before hosting is safe.

So the policy today is **implicit and inconsistent**: `build` is denied for uploads, `launchServer` /
`mockServer` are silently allowed, and nothing names the rule or lets an operator change it. This item
makes the rule explicit and tier-appropriate, which both removes the inconsistency and unblocks a safe
multi-tenant path.

## Detailed design

Treat "an uploaded config's command-execution fields" (`build`, `launchServer.cmd`, `mockServer.cmd`)
as one governed surface, and gate it with a single serve-level policy — fully deterministic, no LLM,
host-level (not per-app), so it respects the prime directives.

- **Policy knob.** A `serve` option (`--upload-exec=<deny|reuse|allow>`, mirrored by an env var for
  the hosted backend) decides what happens when the *active config is an uploaded bundle* and a run
  would spawn one of these commands. It applies only to upload-sourced configs; a local or
  Git-sourced config (already operator-trusted) is unaffected.
- **Tier-appropriate default.** Default to `allow` for a local single-Mac `serve` (the BYO-suite
  Tier-A model, consistent with running the uploaded binary), and to `deny` when `serve` runs in a
  hosted/multi-tenant configuration (a non-loopback bind, or the server backend of BE-0015/BE-0016).
  The default tracks the deployment, so the safe choice is automatic and an operator opts *into* the
  looser one, never out of the stricter one.
- **`reuse` — the useful middle ground for `launchServer`.** `launchServer` already probes `readyUrl`
  first and reuses an already-running server instead of spawning `cmd` ([BE-0059](../../implemented/BE-0059-launch-target-server/BE-0059-launch-target-server.md)).
  Under `reuse`, an uploaded config may *use* a `baseUrl` whose host the operator started out-of-band,
  but its `cmd` is not run — letting a hosted operator pre-provision the target server and still accept
  uploaded suites against it.
- **Fail loud, never silently skip.** Under `deny`/`reuse`, if a run needs a blocked command (no
  server answers `readyUrl`), the run fails with a clear error naming the offending field
  ([DESIGN §2](../../../DESIGN.md): fail loud, no silent fallback) — a denied `launchServer` must not
  read as a flaky run. `build` keeps its existing always-denied treatment for uploads (the bundle
  ships the binary), folded into this policy as the precedent rather than a separate special case.
- **Provenance.** Record the policy decision (allowed / denied / reused, and which field) into the
  run's `manifest.json` alongside BE-0073's upload provenance, so "what did this run execute, and
  what was suppressed?" stays answerable.

The scope is the **policy seam** — deciding *whether* an uploaded config's commands run — not process
sandboxing. Deeper per-job execution isolation (containers, seatbelt, egress controls) is BE-0015 /
BE-0016 territory; this item gives those the deterministic gate they switch on.

## Alternatives considered

- **Block `launchServer` / `mockServer` outright for uploads, like `build`.** Rejected: `launchServer`
  is required at run time to bring up the app under test, and a web bundle has no other way to be
  self-contained, so a blanket block breaks the legitimate Tier-A use case the upload path exists for.
- **Do nothing; rely on BE-0073's Tier-A scoping.** Rejected as the durable answer: the rule is
  correct for Tier-A but implicit and inconsistent (`build` denied, the rest allowed), which is a
  latent foot-gun and a hard blocker for BE-0015 / BE-0016. Defining the seam now, while the upload
  path is fresh, is cheaper than retrofitting it under a hosting deadline.
- **Allowlist specific command strings.** Rejected as brittle: a string allowlist is easy to bypass
  and hard to get right; a tier-based allow/deny with an explicit operator opt-in is simpler and has a
  clearer security story.
- **Sandbox the run (containers / seatbelt / process isolation) instead of gating the command.**
  Deferred, not opposed: per-job isolation is the BE-0015 / BE-0016 domain and is heavier; this item
  defines the policy gate those will sit behind, so the two compose rather than compete.

## References

- [BE-0073 — Upload a config + scenarios + app-binary bundle and bind it as the active config](../../proposals/BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)
  — the upload path; where `build` is denied for uploads and `launchServer` / `mockServer` are not.
- [BE-0059 — Bring up the target server for a run (`launchServer`)](../../implemented/BE-0059-launch-target-server/BE-0059-launch-target-server.md)
  — the run-time server bring-up that carries `cmd` and the `readyUrl` reuse this builds on.
- [BE-0051 — Serve hardening for hosting](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
  — token auth + path confinement this extends with a command-execution policy.
- [BE-0015 — Public hosting of the web UI](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
  [BE-0016 — Self-hosting of the web UI](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
  — the multi-tenant/hosted targets where `deny`-by-default matters and per-job sandboxing lives.
- [DESIGN §1](../../../DESIGN.md) (Bajutsu receives a prebuilt app, does not build it),
  [DESIGN §2](../../../DESIGN.md) (determinism; fail loud, never a silent fallback).
- `bajutsu/config.py` (`LaunchServer.cmd`, `MockServer.cmd`, `AppConfig.build`),
  `bajutsu/serve/operations.py` (`start_run` forcing `build=None` for an uploaded bundle; `state.upload`),
  `bajutsu/serve/jobs.py` (`_build_app`; the run job machinery) — the surfaces a fix touches.
