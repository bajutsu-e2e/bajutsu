**English** · [日本語](BE-0090-uploaded-config-command-execution-ja.md)

# BE-0090 — Govern and sandbox command execution from uploaded bundle configs

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
ships a prebuilt binary; [DESIGN §1](../../../DESIGN.md)). But a config carries other fields that run
a shell command on the `serve` host — chiefly `launchServer.cmd` — and that command is **not**
governed: when the bound bundle runs, the `bajutsu run` subprocess spawns it as written, on the bare
host, with the operator's environment. This item gives that surface an explicit, tier-aware policy
**and** a safe way to run it: a `sandbox` mode that executes the uploaded command inside a throwaway
**Docker** container so it cannot touch the `serve` host. The policy decides *whether* a command runs;
the sandbox decides *how safely* — together they make "yes, run the uploaded server" something a
multi-tenant host can actually allow.

## Motivation

BE-0073 binds an uploaded bundle as the active config and runs the deterministic `run` against it.
Three config fields name a shell command, but they are not all live today:

- `build` — the on-demand build of `appPath` (`bajutsu/serve/jobs.py` `_build_app`). Executed for a
  local config; **already denied** for an uploaded one.
- `launchServer.cmd` ([BE-0059](../../implemented/BE-0059-launch-target-server/BE-0059-launch-target-server.md)) —
  brings up the host behind `baseUrl` for the run. **Executed today** on the `serve` host
  (`bajutsu/runner/launch_server.py`: `subprocess.Popen(shlex.split(ls.cmd), env={**os.environ, …},
  start_new_session=True)`), in its own process group.
- `mockServer.cmd` — declared in the schema (`bajutsu/config.py` `MockServer.cmd`) to start a mock of
  the app's dependencies, but **not yet wired to an executor**: mocks are fulfilled in-process by the
  Playwright network collector, so `cmd` is currently a dormant surface that would execute the same
  way as `launchServer` once a launcher exists.

So the live exposure today is `launchServer.cmd`; `mockServer.cmd` is a latent one the same policy
must cover before it is wired. BE-0073 closes the `build` door for uploads (`start_run` forces
`build=None` when the active config is an uploaded bundle) but leaves `launchServer` **open** — and it
had to: a web bundle has **no** `appPath` binary, so `launchServer` is the only way it can be
self-contained (it serves the bundled static app at run time), which is exactly why a blanket block
isn't an option.

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

A policy that can only say *deny* or *allow* leaves the host case stuck: `deny` breaks the legitimate
self-contained-web-bundle use the upload path exists for, and `allow` is bare-host RCE. The missing
option is a **safe yes** — run the uploaded command, but confine it so a hostile `cmd` reaches nothing
but a disposable container. That is what virtualizing the server command with Docker buys, and it is
already in the project's grain: Tier-B self-hosting ships a `docker compose` control plane
([`deploy/self-host/`](../../../deploy/self-host/), [docs/self-hosting.md](../../../docs/self-hosting.md)),
and BE-0015 / BE-0016 already name per-job container isolation as their domain. This item pulls that
isolation forward as a concrete execution mode and gives it the deterministic policy gate to sit
behind.

## Detailed design

Treat "an uploaded config's command-execution fields" (`build`, `launchServer.cmd`, `mockServer.cmd`)
as one governed surface, gate it with a single serve-level policy, and add a sandboxed-execution mode
that runs the command inside a container. Both halves are fully deterministic (no LLM in the run or
gate path), host-level (not per-app), and configured through `targets.<name>`, so they respect the
prime directives.

- **Policy knob — now four modes.** A `serve` option `--upload-exec=<deny|reuse|sandbox|allow>`
  (mirrored by an env var for the hosted backend) decides what happens when the *active config is an
  uploaded bundle* and a run would spawn one of these commands. It applies only to upload-sourced
  configs; a local or Git-sourced config (already operator-trusted) is unaffected.
  - **`deny`** — the command never runs; a run that needs it fails loud (see below).
  - **`reuse`** — don't run the uploaded `cmd`; only `launchServer`'s existing `readyUrl` probe may
    *use* an operator-provisioned server already answering at `baseUrl`.
  - **`sandbox`** — run the uploaded `cmd`, but **inside a throwaway Docker container**, never on the
    `serve` host. This is the new safe-yes (detailed below).
  - **`allow`** — run the uploaded `cmd` directly on the `serve` host (today's behavior).
- **Sandbox by default.** `serve` treats Docker as a required dependency of the upload path, so
  `sandbox` is the default for an uploaded config's commands on *every* deployment — there is no
  Docker-absent case to fall back from. `allow` is an explicit operator opt-out for a trusted local
  single-Mac `serve` that wants bare-host parity with running the uploaded binary (the BYO-suite
  Tier-A model); a hosted/multi-tenant `serve` keeps the `sandbox` default. The operator opts *into*
  bare-host `allow`, never out of containment.
- **The `sandbox` mode — virtualizing the server command.** When `sandbox` is in effect and a run
  needs `launchServer.cmd` (or, once wired, `mockServer.cmd`), bajutsu runs the command in a fresh
  container rather than via `subprocess.Popen` on the host:
  - **Image + port come from config, not code.** `launchServer` gains an optional `image` (the
    container base, e.g. `node:20-slim`, carrying the runtime the `cmd` needs) and a `port` (the
    in-container port the server listens on, which bajutsu publishes to a loopback host port). The
    bundle's static files are bind-mounted **read-only** at `cwd`; `cmd`, `env`, `readyTimeout` carry
    over unchanged. With no `image`, `sandbox` fails loud (the bundle must declare what it runs in) —
    it never falls back to bare-host execution.
  - **Hardened, disposable container.** `--rm`, no host bind mounts except the read-only bundle, a
    dropped capability set (`--cap-drop=ALL`), `--security-opt=no-new-privileges`, a read-only root
    filesystem with a tmpfs scratch, non-root user, CPU/memory/pids limits, and only the one published
    port. The container is named per-run and torn down on teardown, replacing the host process-group
    kill of `start_launch_server`.
  - **Readiness is unchanged.** The run still probes `readyUrl` (now the published loopback port) as a
    **condition wait**, never a fixed sleep ([DESIGN §2](../../../DESIGN.md)); `reuse` semantics still
    apply — an externally answered `readyUrl` short-circuits both spawn and container.
  - **Containment, not a VM.** Docker confines the blast radius to a disposable container; it is not a
    VM-grade boundary. Egress restriction, per-tenant network namespaces, and VM-level isolation stay
    BE-0015 / BE-0016 territory — `sandbox` is the deterministic, here-and-now hardening those build on,
    and the published port stays loopback-confined so the sandbox never widens the host's exposure.
- **Fail loud, never silently skip.** Under any mode, if a run needs a command the mode forbids or
  cannot satisfy — `deny`/`reuse` with no server answering `readyUrl`, or `sandbox` with no `image` —
  the run fails with a clear error naming the offending field and the reason
  ([DESIGN §2](../../../DESIGN.md): fail loud, no silent fallback). A blocked or sandbox-misconfigured
  `launchServer` must not read as a flaky run, and `sandbox` must never quietly degrade to bare-host
  `allow`. `build` keeps its existing always-denied treatment for uploads, folded into this policy as
  the precedent rather than a separate special case.
- **Provenance.** Record the policy decision (allowed / denied / reused / sandboxed, which field, and
  the image when sandboxed) into the run's `manifest.json` alongside BE-0073's upload provenance, so
  "what did this run execute, where, and what was suppressed?" stays answerable.

The scope is the **policy seam plus a containerized execution mode** for the upload path — deciding
whether an uploaded config's commands run, and running them in a disposable container when they do.
Deeper per-job isolation (egress controls, network namespaces, VM-grade sandboxing) remains BE-0015 /
BE-0016 territory; this item gives those a deterministic gate and a working container baseline to
extend.

## Alternatives considered

- **Policy gate only (`deny|reuse|allow`), no sandbox.** The original shape of this item. Rejected as
  insufficient for hosting: it can decide *whether* a command runs but cannot make "yes" safe, so a
  multi-tenant host is left choosing between a broken `deny` and bare-host RCE. The `sandbox` mode is
  the missing safe-yes, which is the whole point of the multi-tenant seam.
- **Make `sandbox` the only mode (drop the other three).** Rejected even though Docker is always
  available: `reuse` against an operator-provisioned server is a legitimate hosted pattern (the run
  needs no container at all), `allow` lets a trusted Tier-A operator skip the container for bare-host
  parity with running the uploaded binary, and `deny` is the right answer when an operator wants to
  refuse uploaded commands outright. `sandbox` is the safe *default*, not the only path — keeping the
  other three modes costs little and each earns its place.
- **Block `launchServer` / `mockServer` outright for uploads, like `build`.** Rejected: `launchServer`
  is required at run time to bring up the app under test, and a web bundle has no other way to be
  self-contained, so a blanket block breaks the legitimate Tier-A use case the upload path exists for.
- **Do nothing; rely on BE-0073's Tier-A scoping.** Rejected as the durable answer: the rule is
  correct for Tier-A but implicit and inconsistent (`build` denied, the rest allowed), which is a
  latent foot-gun and a hard blocker for BE-0015 / BE-0016. Defining the seam now, while the upload
  path is fresh, is cheaper than retrofitting it under a hosting deadline.
- **Allowlist specific command strings.** Rejected as brittle: a string allowlist is easy to bypass
  and hard to get right; a tier-based mode with an explicit operator opt-in and a container boundary
  is simpler and has a clearer security story.
- **A non-Docker sandbox (macOS `sandbox-exec`/seatbelt, bubblewrap, a VM).** Deferred, not opposed:
  seatbelt is macOS-only (no Linux control plane), bubblewrap is Linux-only, and a VM is far heavier.
  Docker is the one mechanism already shipped in the project's hosting stack and available on both the
  Tier-A Mac (Docker Desktop) and the Tier-B Linux node, so it is the pragmatic first sandbox.
  Stronger, VM-grade isolation stays a BE-0015 / BE-0016 option layered on the same policy gate.

## References

- [BE-0073 — Upload a config + scenarios + app-binary bundle and bind it as the active config](../../proposals/BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)
  — the upload path; where `build` is denied for uploads and `launchServer` is not.
- [BE-0059 — Bring up the target server for a run (`launchServer`)](../../implemented/BE-0059-launch-target-server/BE-0059-launch-target-server.md)
  — the run-time server bring-up that carries `cmd` and the `readyUrl` reuse this builds on; `sandbox`
  replaces its host `Popen` with a container while keeping the probe.
- [BE-0051 — Serve hardening for hosting](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
  — token auth + path confinement this extends with a command-execution policy and sandbox.
- [BE-0015 — Public hosting of the web UI](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
  [BE-0016 — Self-hosting of the web UI](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
  — the multi-tenant/hosted targets where `sandbox`-by-default matters and deeper (egress / VM-grade)
  per-job isolation lives.
- [docs/self-hosting.md](../../../docs/self-hosting.md), [`deploy/self-host/`](../../../deploy/self-host/)
  — the existing `docker compose` hosting stack `sandbox` is consistent with.
- [DESIGN §1](../../../DESIGN.md) (Bajutsu receives a prebuilt app, does not build it),
  [DESIGN §2](../../../DESIGN.md) (determinism; fail loud, never a silent fallback).
- `bajutsu/config.py` (`LaunchServer.cmd`, `MockServer.cmd`, `AppConfig.build`; the new
  `LaunchServer.image` / `port`), `bajutsu/runner/launch_server.py` (`start_launch_server`'s host
  `Popen` the `sandbox` mode replaces with a container), `bajutsu/serve/operations.py` (`start_run`
  forcing `build=None` for an uploaded bundle; `state.upload`), `bajutsu/serve/jobs.py` (`_build_app`;
  the run job machinery) — the surfaces a fix touches.
