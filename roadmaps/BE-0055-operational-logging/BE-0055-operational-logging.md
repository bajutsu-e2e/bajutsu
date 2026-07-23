**English** · [日本語](BE-0055-operational-logging-ja.md)

# BE-0055 — Operational logging for the hosted serve

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0055](BE-0055-operational-logging.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0055") |
| Implementing PR | [#334](https://github.com/bajutsu-e2e/bajutsu/pull/334) |
| Topic | Hosting the web UI |
<!-- /BE-METADATA -->

## Introduction

The hosted `bajutsu serve` is now a **multi-process, multi-tenant** service — a control plane plus
remote macOS workers, scoped by org — after the persistence/identity/multi-tenancy work landed
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)). Yet the
tool has **almost no operational logging of its own**: a single `getLogger` call exists in the whole
codebase, and the stdlib request handler deliberately silences per-request logging. This proposal
designs an **operational logging contract** for the hosted serve: **structured** (JSON), **correlated**
by ids, and **redacted**, emitted to **stdout**.

This is the tool's *own* diagnostic trace, kept deliberately distinct from the three log surfaces that
already exist:

- **Evidence** — the *test subject's* trace (`deviceLog`, `appTrace`, network, actionLog), captured and
  scrubbed by the evidence subsystem ([evidence.md](../../docs/evidence.md)). Not this.
- **Run output** — the `--progress` scenario/step stream, delivered live and stored via the serve
  LogBus (BE-0015). Not this.
- **Audit log** — who did what, in the `audit_log` table (BE-0015). Adjacent but separate (a viewer
  for it is a distinct concern).

It is shaped by the prime directives ([CLAUDE.md](../../CLAUDE.md), [DESIGN.md](../../DESIGN.md)):
the deterministic `run` / CI gate must stay lean (stdlib-only, quiet, human-readable), and **secrets
must never reach a log line**.

## Motivation

- **No cross-process traceability.** With the control plane and workers in separate processes, there
  is today no way to follow one user action — "org X's run failed to dispatch" — from the request,
  through the enqueued job, to the worker that ran it. The run *output* travels via the LogBus, but the
  tool's *operational* events are uncorrelated.
- **No redaction guarantee on operational output.** Ad-hoc `print`/log calls risk leaking a resolved
  `${secrets.X}` value, the operator token, an OAuth session id, or `ANTHROPIC_API_KEY`. Evidence has a
  redaction subsystem; the operational channel has no such guarantee.
- **The design is unowned.** [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
  names "structured JSON logs" in its observability row but defers the design; no item owns the
  contract.
- **Audience: a future SRE.** Someone operating the hosted service needs greppable, alertable,
  correlatable logs — without paging through the test subject's evidence to find them.

## Detailed design

### Two-tier split (do not bloat the gate)

The deterministic `run` (Tier 2 / CI gate) keeps its current behavior: human-readable, quiet, **stdlib
logging only**, no new dependency. Structured operational logging is a **serve-mode** concern. Selected
by environment so the difference lives in config, not per-app code:

- `BAJUTSU_LOG_FORMAT=json|text` — serve defaults to `json`, the CLI to `text`.
- `BAJUTSU_LOG_LEVEL` — standard level names; defaults sensible per mode.
- **Sink: stdout only** (12-factor). Aggregation (Sentry / Prometheus / OpenTelemetry) is the
  *deployment's* job and stays in BE-0015's observability scope — this item adds no such dependency, so
  it remains testable on the Linux gate.

### Where it plugs in (the seams)

The codebase today has no operational logging to speak of: a lone `logging.getLogger(__name__)` in
[`serve/jobs.py`](../../bajutsu/serve/jobs.py), and both HTTP request handlers deliberately silence
per-request logging (`serve/handler.py`'s `Handler.log_message`, `network.py`'s stub). There is no root
configuration and no `contextvars` anywhere — so this is greenfield wiring, installed in one place:

- **A new `bajutsu/serve/oplog.py`** (named to avoid shadowing the stdlib `logging`) owns the whole
  contract: `configure(format, level)` installs the root formatter + the redacting/correlation filters;
  it also holds the `contextvars`, the event-name registry, and the JSON formatter. `serve` calls it at
  startup; the CLI/`run` path never imports it (keeping the gate stdlib-only and quiet).
- **Two HTTP surfaces, one filter.** Because the filter sits at the **root logger**, it covers both the
  hosted FastAPI app ([`serve/server/app.py`](../../bajutsu/serve/server/app.py), which already has an
  `@app.middleware("http")` at the request boundary — the natural place to mint `request_id` and bind the
  contextvar) and the local stdlib server ([`serve/handler.py`](../../bajutsu/serve/handler.py)'s
  `do_GET` / `do_POST`). JSON is enabled in serve mode; the local CLI stays text.
- **Worker boundary.** [`serve/server/worker_job.py`](../../bajutsu/serve/server/worker_job.py)'s
  `execute_job_spec(spec, …)` is where the job's ids bind. The job spec carries **`job_id` / `org` /
  `actor`** (built in `worker_job.py`); **`run_id` is minted when the run starts on the worker**, not in
  the spec, so it is bound there (correcting the invariant below). Cross-process correlation is therefore
  by *shared id values* that already exist on both sides, never by propagating a context object.
- **Redaction reuse.** The filter wraps the existing `Redactor` ([`redaction.py`](../../bajutsu/redaction.py),
  `redact_text`) for value-masking, plus the new key-based masker.

### The contract — machine-checkable invariants

Operational logs are **non-deterministic** (timestamps, ordering), so — unlike evidence
([DESIGN §2](../../DESIGN.md)) — the design does **not** assert byte-equality. It asserts a **schema
and a set of invariants**, each verified by a gate test:

1. **Secret-free (redaction).** A single redacting filter/formatter sits at the **root logger**, so
   *every* record — including third-party libraries' — is scrubbed; correctness does not depend on each
   call site behaving. Two masking modes:
   - **value-based** — known secret values (resolved `${secrets.X}`, `BAJUTSU_SERVE_TOKEN`, OAuth
     session id / client secret, `ANTHROPIC_API_KEY`), reusing the existing redaction machinery
     (`redaction.py`);
   - **key-based** — sensitive structured-field keys (`authorization`, `token`, `secret`, `password`,
     `cookie`, `api_key`) masked by name regardless of value.

   *Tests:* a raw `logging.getLogger("anything").info(<secret>)` emits no raw secret; a record carrying
   `{"authorization": "Bearer …"}` is masked.

2. **Correlation-id propagation.** A `request_id` is minted at request entry (the FastAPI middleware,
   and the stdlib handler's `do_*`). On the worker, `execute_job_spec` binds `job_id` / `org` / `actor`
   from the spec, and `run_id` once the run mints it. Ids are held in `contextvars` and a logging
   `Filter` injects them into every record — framework-agnostic, so it works for both the stdlib handler
   and the FastAPI app. Cross-process correlation is by **shared id**, not propagated context:
   `job_id` / `org` / `actor` travel in the job spec and `run_id` is the run's own id, so both sides log
   the same values without threading a context object across the process boundary.

   *Tests:* one request through the app yields records that all share its `request_id`; the worker's
   `execute_job_spec` records carry `job_id` + `run_id` + `org`.

3. **Structured schema (serve).** Each serve log line is single-line JSON with a fixed shape:
   `ts, level, logger, event?, msg, request_id?, org?, actor?, job_id?, run_id?`.

   *Test:* every emitted line parses as JSON with the required keys and types.

4. **Gate-clean (two-tier).** Operational logging is a side channel that never affects a run's pass/fail,
   and its setup pulls no heavy dependency on the default path.

   *Test:* the existing import guard stays green; logging configuration is stdlib-only.

5. **Event taxonomy.** A small registry of **stable event names** so an SRE can grep/alert on `event=`:
   e.g. `run.dispatched`, `run.recorded`, `oauth.login`, `quota.rejected`, `worker.job.started`,
   `worker.job.finished`, `artifact.upload.failed`.

   *Test:* the key flows emit their expected `event`.

### Redaction reuse

The operational channel runs through the existing `redaction.py` value-masking, extended with the
key-based masking above. This keeps a single source of truth for "what counts as a secret", shared with
secret variables ([BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md)) and
the redacted-AI-path direction
([BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)).

The value-mask set has **two sources**, because secrets live at two scopes:

- **Static (process-lifetime), on the control plane** — `BAJUTSU_SERVE_TOKEN`, the OAuth client secret /
  session ids, `ANTHROPIC_API_KEY`. Read once at `oplog.configure`, masked for the whole process.
- **Per-run (scoped), on the worker** — the values a run resolves from `${secrets.X}`. These are not
  process-global, so the worker binds a **run-scoped `Redactor`** (seeded with the run's resolved secret
  values) into a `contextvar` when the run starts; the root filter consults it for records emitted during
  that run, and the scope falls away when the run ends. This closes the gap that env-only masking would
  leave for run-resolved secrets, without making them process-global.

Key-based masking (sensitive field *names*) backstops both, catching a structured value whose literal
isn't in either set.

### Out of scope

- **Metrics / error tracking / distributed tracing** (Prometheus, Sentry, OpenTelemetry) — deployment /
  observability, owned by [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md).
- **An audit-log viewer** — the `audit_log` table exists (BE-0015); surfacing it is a separate concern.
- **Evidence and run output** — already owned (evidence subsystem; the LogBus).
- **A file sink / in-process rotation** — stdout only; see Alternatives.

## Alternatives considered

- **Per-call-site redaction** (each log call masks its own values) — rejected: it relies on discipline
  and leaks through third-party libraries. A root-level filter is the structural guarantee.
- **A file sink with in-process rotation** (`BAJUTSU_LOG_FILE`) — deferred: stdout + the deployment
  shipping it (12-factor) keeps the tool simple and the gate dependency-free. Can be added later if a
  self-hoster needs it.
- **`threading.local` or explicit id threading** for correlation — rejected in favor of `contextvars`,
  which works across the stdlib handler's threads and FastAPI's async/threadpool without threading ids
  through every signature.
- **A structured-logging library** (e.g. `structlog`) — rejected for now to stay stdlib-only and keep
  the gate lean; can be revisited if the hand-rolled JSON formatter proves limiting.
- **A dedicated "Observability & operations" roadmap topic** — deferred: this item sits under *Hosting
  the web UI* for now. If an audit-log viewer and metrics items follow, a dedicated topic can be split
  out then.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [DESIGN.md](../../DESIGN.md) §2 — determinism-first; secret masking.
- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — the hosted topology and the deferred "structured JSON logs" observability row this item realizes.
- [BE-0032 — Secret variables](../BE-0032-secret-variables/BE-0032-secret-variables.md) — the secret-masking machinery shared with this item.
- [BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) — the redacted-path philosophy this item extends to operational logs.
- [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) and [BE-0051 — Serve hardening](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) — the serve this logging instruments.
- [evidence.md](../../docs/evidence.md) — the evidence subsystem this is deliberately distinct from.
