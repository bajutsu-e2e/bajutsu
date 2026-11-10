**English** · [日本語](BE-0173-slim-web-worker-image-ja.md)

# BE-0173 — Slim Linux web-worker container image

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0173](BE-0173-slim-web-worker-image.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0173") |
| Implementing PR | [#718](https://github.com/bajutsu-e2e/bajutsu/pull/718) |
| Topic | Hosting the web UI |
| Related | [BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads.md), [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md), [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu will ship a **slim, containerized Linux worker for the web (Playwright) backend**. The
hosted topology can then run `bajutsu worker` as a small OCI image instead of a hand-provisioned
machine.

The image carries the worker's true runtime closure: the base package, the `web` backend, and the
assertion extras a run reaches (`visual`, `schema`). It deliberately omits the control plane's
dependencies (`server`, `db`, `oauth`, `cloud`) and the AI SDK (`ai`).

[BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads.md) made
this possible. It made the worker **credential- and cloud-SDK-free**: the worker talks to the
control plane and object storage over plain HTTP. A worker image therefore needs no `boto3`/GCS SDK
and bakes in no cloud secrets.

## Motivation

The self-hosted deploy bundle
([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), `deploy/self-host/`) runs
the Linux control plane in a container and the **Mac workers on bare metal**. The Mac worker is not
containerized because the iOS Simulator (idb backend) needs the Aqua GUI session.

The web (Playwright) backend runs **headless on Linux**, so a web worker has no such constraint and
can be a container. A Linux web worker in the fleet is a target topology for hosting on the web. It
wants a first-class, reproducible image, not an ad-hoc `uv sync` on a VM.

Two things make a *slim* image worth designing deliberately, instead of installing the whole
package.

- **Deploy weight.** A naive image that installs the full dependency set pulls the control plane's
  stack (`fastapi`/`uvicorn`, `sqlalchemy`/`alembic`/`psycopg`), the cloud SDKs (`boto3`, GCS), and
  the AI SDK (`anthropic`). That adds a few hundred MB of wheels the worker never imports. BE-0160
  lets us drop them, because the worker's network dependency is an HTTP client, not a cloud SDK.

  The Chromium binary that Playwright needs dominates the remaining weight, and even that is not
  fixed. A headless Linux worker never renders a window, so it installs Chromium's **headless
  shell** (`playwright install --only-shell`) instead of the full headed build. Playwright already
  auto-selects the shell for headless launches. The shell trims tens of MB of browser and needs a
  lighter system-library set. A smaller image also pulls and cold-starts faster across a scaling
  fleet.
- **A named runtime closure.** Today the docs install the worker as `bajutsu[idb]`, but a run also
  reaches `pillow` (visual assertions) and `jsonschema` (`responseSchema` assertions).
  `bajutsu[idb]` alone *under-installs*, and such a run fails lazily at assertion time. Neither
  backend has a single name for "what a worker must install". Defining that closure fixes the gap
  for the Mac worker too, and it is the exact input the container build consumes.

The web worker is also a **new capability axis** for the pool.
[BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md) routes jobs to
workers by capability. It frames the heterogeneous pool as Mac workers that differ in iOS runtime or
device class, and it does not yet treat **backend (idb vs web)** as a routing dimension.

A Linux web worker introduces `backend=web` as a capability. The slim image delivers that
capability, and BE-0166 supplies the routing that sends web jobs to it. This item adds the image and
the runtime closure. Extending BE-0166 with the backend dimension is tracked there (see *Detailed
design* §5).

## Detailed design

The work is the deploy artifact, plus the packaging and the guardrail that keep it correct and slim.
Nothing here touches the deterministic `run`/CI gate or puts an LLM on the verdict path. It is
packaging, a Dockerfile, and docs.

### 1. Define the worker runtime-closure extras

Add composite extras in `pyproject.toml` that name exactly what a worker must install per backend.
Compose them from the existing single-purpose extras, so no version is duplicated:

- `worker-web = ["bajutsu[web,visual,schema]"]`
- `worker-idb = ["bajutsu[idb,visual,schema]"]`

The `ai` extra stays **opt-in** and outside the closure. A worker needs it only when scenarios use
the AI authoring/investigation paths (`record`, `run --dismiss-alerts`), consistent with the
base-AI-free guarantee. The container build (§2) and the Mac worker install docs both consume
`worker-web` / `worker-idb`, so "what a worker installs" lives in one place.

### 2. Multi-stage Dockerfile for the web worker

Add `deploy/self-host/worker-web.Dockerfile` as a multi-stage build:

- **Build stage**: install `bajutsu[worker-web]` into a virtual environment. The install is
  non-editable, not an editable checkout.
- **Final stage**: a slim Python base, the venv, and the Chromium browser Playwright needs. Nothing
  comes from `server`/`db`/`oauth`/`cloud`/`ai`.

  The browser is installed with `playwright install --with-deps --only-shell chromium`. That installs
  the **headless shell**, not the full headed Chromium. A Linux worker is always headless, and
  Playwright auto-selects the shell for headless launches, so the driver is unchanged. The shell is
  tens of MB lighter and needs a smaller `--with-deps` system-library set.

  The container refuses to run Chromium as root without weakening the sandbox. The final stage
  therefore runs the worker as an unprivileged user. The driver keeps its default sandboxed launch
  flags, which stays app-agnostic. Alternative B weighs this self-installed slim base against the
  upstream Playwright image.

- The entrypoint runs `bajutsu worker`. Environment variables (`BAJUTSU_SERVER_URL`, `BAJUTSU_TOKEN`)
  configure it exactly as they configure the bare-metal worker. Per BE-0160, the worker holds **no**
  object-store credentials.

### 3. Compose service + self-hosting docs

Add an **optional** `worker-web` service to `deploy/self-host/docker-compose.yml`. It is off by
default, so an idb-only single-tenant deploy is unchanged.

Document the **heterogeneous fleet** in `docs/self-hosting.md` and its Japanese mirror. Mac idb
workers run on bare metal (Aqua GUI), Linux web workers run as containers, and both lease from the
same control plane over HTTP. State plainly that the web worker image needs no cloud SDK or secrets
(BE-0160), and name the extras that back it (§1).

### 4. Cold-start / closure guard test

Add a test that asserts the worker's import closure stays lean. Importing the `worker` command path
must **not** import `fastapi`/`uvicorn`, `sqlalchemy`, `boto3`/GCS, or `anthropic`. A stray
top-level import can then never silently re-inflate the image or slow worker cold-start.

The test extends the existing import-guard approach (`tests/serve/test_import_guard.py`) to the
worker entry. It runs in the standard gate, with no Simulator, and works on Linux.

### 5. BE-0166: backend as a capability axis (cross-item)

Record `backend` (idb vs web) as a routing dimension. A `web` job is then leased only by a web
worker, and an `idb` job only by a Mac worker. This is a small addition to
[BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md)'s design, not new
code here. This item and BE-0166 cross-reference each other.

Until BE-0166 lands, a homogeneous web-only or idb-only pool is unaffected. The addition matters
once the pool mixes backends.

## Alternatives considered

### A. Install the full package in the worker image

Build the image from `bajutsu[web]` plus whatever else, or from the whole dependency set. This is
simpler to write. It pulls the control plane, cloud, and AI stacks that the worker never imports,
which is the avoidable weight this item removes. BE-0160 makes dropping them safe, so keeping them
wastes that win.

### B. Build on the upstream Playwright Python image vs. `playwright install` ourselves

Basing the final stage on `mcr.microsoft.com/playwright/python` ships browsers and OS libraries
preinstalled and version-matched. The cost is a larger, less-controlled base and an external base
dependency.

Installing Chromium ourselves onto a slim Python base keeps the base minimal and fully ours. The
cost is maintaining the `--with-deps` system-library step.

**Adopted: the self-installed slim base.** Control wins here because the whole point is a minimal
image. The upstream image also bundles the full headed browser that we deliberately skip.

### E. Full headed Chromium vs. the headless shell (`--only-shell`)

Since Playwright 1.49, `playwright install chromium` fetches both the full headed Chrome-for-Testing
build and a separate `chromium-headless-shell`. Playwright auto-selects the shell for headless
launches.

A Linux worker is always headless, so the image installs the shell alone (`--only-shell`) and skips
the full build. That saves tens of MB of browser and lightens the `--with-deps` set. It needs **no
code change**, because the driver's `launch(headless=True)` already resolves to the shell.

The shell drops headed mode, browser extensions, and in-page PDF rendering. Its screenshots are not
pixel-identical to full Chrome. None of these affect a headless E2E worker's click, navigation,
network-interception, screenshot, or video paths.

A visual baseline is already backend- and platform-specific, captured and compared on the same
worker. The shell therefore does not introduce a new parity axis.

Keeping the full build would cost image size for capabilities the worker never uses, so it is not
adopted.

### C. Containerize the Mac (idb) worker too

This is out of scope and not feasible. The idb backend needs the Aqua GUI session and the iOS
Simulator, which a Linux container cannot provide. The Mac worker stays bare metal, and §1's
`worker-idb` extra still improves its bare-metal install. The container story is web-only by nature.

### D. Split the worker into a separate distribution (`bajutsu-worker` wheel)

Publish a second package holding the worker, run, and backend code alone. This would shave the
*source* of the `serve`/`mcp`/`ai` modules, but that is pure Python of negligible size. The real
weight is in dependencies, which the extras (§1) already gate.

Splitting the distribution cuts against the "one package = one deterministic core" shape. It adds
release-process overhead for a marginal size win, so it is not adopted.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Define the `worker-web` / `worker-idb` runtime-closure extras in `pyproject.toml`
- [x] Multi-stage web-worker Dockerfile in `deploy/self-host/`
- [x] Optional `worker-web` compose service + `docs/self-hosting.md` (+ Japanese mirror)
      heterogeneous-fleet docs
- [x] Cold-start / import-closure guard test for the worker entry
- [x] Extend BE-0166 with `backend` as a capability axis (reciprocal cross-reference)

[#718](https://github.com/bajutsu-e2e/bajutsu/pull/718) — Slim web-worker image landed: `worker-web`
/ `worker-idb` closure extras; multi-stage
`deploy/self-host/worker-web.Dockerfile` installing only `bajutsu[worker-web]` + Chromium's headless
shell (`--only-shell`) and running as an unprivileged user; optional `worker-web` compose service
(off by default, behind the `web-worker` profile) + heterogeneous-fleet docs in both languages; a
worker import-closure guard (`tests/serve/test_import_guard.py`); and the reciprocal `backend`
capability-axis note added to BE-0166.

## References

- [BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads.md) —
  Credential- and cloud-SDK-free worker (the enabler: a worker image needs no cloud SDK or secrets)
- [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md) —
  Capability-routed queues (the routing that sends web jobs to web workers; gains `backend` as a
  capability axis)
- [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) — Web UI self-hosting
  (the deploy bundle this image joins)
- [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) —
  Post-completion worker model (the worker↔control-plane HTTP loop the image runs)
- [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) — Web (Playwright)
  backend (the headless-on-Linux backend that makes a containerized worker possible)
- `pyproject.toml` — the `worker` (empty since BE-0160), `web`, `visual`, `schema` extras this
  composes
- `deploy/self-host/` — the compose + Dockerfile bundle the web-worker image and service extend
- `docs/self-hosting.md` — the topology docs to extend with the heterogeneous (Mac idb + Linux web)
  fleet
