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

The hosted `bajutsu serve` is now a **multi-process, multi-tenant** service. It has a control plane and remote macOS workers, scoped by org. The persistence, identity, and multi-tenancy work of [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) made it so.

Yet the tool has **almost no operational logging of its own**. The whole codebase has a single `getLogger` call, and the stdlib request handler deliberately silences per-request logging. This proposal designs an **operational logging contract** for the hosted serve. The logs are **structured** (JSON), **correlated** by ids, and **redacted**, and they go to **stdout**.

The contract covers the tool's *own* diagnostic trace. Three log surfaces already exist, and this proposal keeps them distinct:

- **Evidence.** The *test subject's* trace (`deviceLog`, `appTrace`, network, actionLog). The evidence subsystem captures and scrubs it ([evidence.md](../../docs/evidence.md)). It is not in scope here.
- **Run output.** The `--progress` scenario and step stream. The serve LogBus delivers it live and stores it (BE-0015). It is not in scope here.
- **Audit log.** Who did what, in the `audit_log` table (BE-0015). It is adjacent but separate, and a viewer for it is a distinct concern.

The prime directives ([CLAUDE.md](../../CLAUDE.md), [DESIGN.md](../../DESIGN.md)) shape the design. The deterministic `run` / CI gate must stay lean: stdlib-only, quiet, and human-readable. **Secrets must never reach a log line.**

## Motivation

- **No cross-process traceability.** The control plane and the workers run in separate processes. Today nothing follows one user action, such as "org X's run failed to dispatch", from the request through the enqueued job to the worker that ran it. The run *output* travels through the LogBus, but the tool's *operational* events are uncorrelated.
- **No redaction guarantee on operational output.** Ad-hoc `print` and log calls risk leaking a resolved `${secrets.X}` value, the operator token, an OAuth session id, or `ANTHROPIC_API_KEY`. Evidence has a redaction subsystem. The operational channel has no such guarantee.
- **The design is unowned.** [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) names "structured JSON logs" in its observability row but defers the design. No item owns the contract.
- **The audience is a future site reliability engineer (SRE).** Someone who operates the hosted service needs greppable, alertable, correlatable logs. They should not have to page through the test subject's evidence to find them.

## Detailed design

### Two-tier split (do not bloat the gate)

The deterministic `run` (Tier 2 / CI gate) keeps its current behavior: human-readable, quiet, **stdlib logging only**, and no new dependency. Structured operational logging is a **serve-mode** concern. Environment variables select it, so the difference lives in config, not in per-app code:

- `BAJUTSU_LOG_FORMAT=json|text`. Serve defaults to `json`, and the CLI defaults to `text`.
- `BAJUTSU_LOG_LEVEL`. It takes standard level names and has a sensible default per mode.
- **Sink: stdout only** (12-factor). Aggregation (Sentry, Prometheus, OpenTelemetry) is the *deployment's* job and stays in BE-0015's observability scope. This item adds no such dependency, so the Linux gate can still test it.

### Where it plugs in (the seams)

The codebase today has no operational logging to speak of. A lone `logging.getLogger(__name__)` lives in [`serve/jobs.py`](../../bajutsu/serve/jobs.py). Both HTTP request handlers deliberately silence per-request logging: `serve/handler.py`'s `Handler.log_message` and the stub in `network.py`. There is no root configuration and no `contextvars` anywhere. The wiring is therefore greenfield, and one place installs it:

- **A new `bajutsu/serve/oplog.py`**, named to avoid shadowing the stdlib `logging`, owns the whole contract. `configure(format, level)` installs the root formatter plus the redacting and correlation filters. The module also holds the `contextvars`, the event-name registry, and the JSON formatter. `serve` calls it at startup. The CLI and `run` path never import it, which keeps the gate stdlib-only and quiet.
- **Two HTTP surfaces, one filter.** The filter sits at the **root logger**, so it covers both servers. One is the hosted FastAPI app ([`serve/server/app.py`](../../bajutsu/serve/server/app.py)). It already has an `@app.middleware("http")` at the request boundary, the natural place to mint `request_id` and bind the contextvar. The other is the local stdlib server ([`serve/handler.py`](../../bajutsu/serve/handler.py)'s `do_GET` and `do_POST`). Serve mode enables JSON, and the local CLI stays text.
- **Worker boundary.** [`serve/server/worker_job.py`](../../bajutsu/serve/server/worker_job.py)'s `execute_job_spec(spec, …)` is where the job's ids bind. The job spec carries **`job_id`, `org`, and `actor`**, which `worker_job.py` builds. The worker mints **`run_id` when the run starts**, so the spec does not carry it, and the worker binds it there. This corrects the invariant below. Cross-process correlation therefore uses *shared id values* that already exist on both sides. It never propagates a context object.
- **Redaction reuse.** The filter wraps the existing `Redactor` ([`redaction.py`](../../bajutsu/redaction.py), `redact_text`) for value masking. It adds the new key-based masker.

### The contract — machine-checkable invariants

Operational logs are **non-deterministic**, because timestamps and ordering vary. Unlike evidence ([DESIGN §2](../../DESIGN.md)), the design therefore does **not** assert byte-equality. It asserts a **schema and a set of invariants**. A gate test verifies each one:

1. **Secret-free (redaction).** A single redacting filter or formatter sits at the **root logger**. *Every* record is scrubbed, including records from third-party libraries, so correctness does not depend on each call site behaving. Two masking modes apply:
   - **Value-based.** Known secret values are masked: resolved `${secrets.X}`, `BAJUTSU_SERVE_TOKEN`, the OAuth session id and client secret, and `ANTHROPIC_API_KEY`. The mode reuses the existing redaction machinery (`redaction.py`).
   - **Key-based.** Sensitive structured-field keys are masked by name, whatever the value: `authorization`, `token`, `secret`, `password`, `cookie`, and `api_key`.

   *Tests:* a raw `logging.getLogger("anything").info(<secret>)` emits no raw secret. A record that carries `{"authorization": "Bearer …"}` is masked.

2. **Correlation-id propagation.** The request entry mints a `request_id`, in the FastAPI middleware and in the stdlib handler's `do_*`. On the worker, `execute_job_spec` binds `job_id`, `org`, and `actor` from the spec. It binds `run_id` once the run mints it. The ids live in `contextvars`, and a logging `Filter` injects them into every record. The mechanism is framework-agnostic, so it works for both the stdlib handler and the FastAPI app. Cross-process correlation uses a **shared id**, not a propagated context. `job_id`, `org`, and `actor` travel in the job spec, and `run_id` is the run's own id. Both sides log the same values without threading a context object across the process boundary.

   *Tests:* one request through the app yields records that all share its `request_id`. The worker's `execute_job_spec` records carry `job_id`, `run_id`, and `org`.

3. **Structured schema (serve).** Each serve log line is single-line JSON with a fixed shape: `ts, level, logger, event?, msg, request_id?, org?, actor?, job_id?, run_id?`.

   *Test:* every emitted line parses as JSON with the required keys and types.

4. **Gate-clean (two-tier).** Operational logging is a side channel that never affects a run's pass/fail. Its setup pulls no heavy dependency on the default path.

   *Test:* the existing import guard stays green, and the logging configuration is stdlib-only.

5. **Event taxonomy.** A small registry of **stable event names** lets an SRE grep and alert on `event=`. Examples are `run.dispatched`, `run.recorded`, `oauth.login`, `quota.rejected`, `worker.job.started`, `worker.job.finished`, and `artifact.upload.failed`.

   *Test:* the key flows emit their expected `event`.

### Redaction reuse

The operational channel runs through the existing `redaction.py` value masking, extended with the key-based masking above. This keeps a single source of truth for "what counts as a secret". Secret variables ([BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md)) and the redacted-AI-path direction ([BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)) share it.

The value-mask set has **two sources**, because secrets live at two scopes:

- **Static (process-lifetime), on the control plane.** `BAJUTSU_SERVE_TOKEN`, the OAuth client secret and session ids, and `ANTHROPIC_API_KEY`. `oplog.configure` reads them once and masks them for the whole process.
- **Per-run (scoped), on the worker.** The values that a run resolves from `${secrets.X}`. They are not process-global. When the run starts, the worker binds a **run-scoped `Redactor`**, seeded with the run's resolved secret values, into a `contextvar`. The root filter consults it for records emitted during that run, and the scope falls away when the run ends. This closes the gap that env-only masking would leave for run-resolved secrets, without making them process-global.

Key-based masking (sensitive field *names*) backstops both sources. It catches a structured value whose literal is in neither set.

### Out of scope

- **Metrics, error tracking, and distributed tracing** (Prometheus, Sentry, OpenTelemetry). They belong to deployment and observability, which [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) owns.
- **An audit-log viewer.** The `audit_log` table exists (BE-0015). Surfacing it is a separate concern.
- **Evidence and run output.** The evidence subsystem and the LogBus already own them.
- **A file sink and in-process rotation.** Output goes to stdout only. See Alternatives.

## Alternatives considered

- **Per-call-site redaction** (each log call masks its own values). Rejected. It relies on discipline and leaks through third-party libraries. A root-level filter is the structural guarantee.
- **A file sink with in-process rotation** (`BAJUTSU_LOG_FILE`). Deferred. Writing to stdout and letting the deployment ship the output (12-factor) keeps the tool simple and the gate dependency-free. A self-hoster who needs a file sink can get it added later.
- **`threading.local` or explicit id threading** for correlation. Rejected in favor of `contextvars`. It works across the stdlib handler's threads and FastAPI's async and threadpool code, and it needs no ids threaded through every signature.
- **A structured-logging library** (for example `structlog`). Rejected for now, to stay stdlib-only and keep the gate lean. The decision can be revisited if the hand-rolled JSON formatter proves limiting.
- **A dedicated "Observability & operations" roadmap topic.** Deferred. This item sits under *Hosting the web UI* for now. If an audit-log viewer and metrics items follow, a dedicated topic can be split out then.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [DESIGN.md](../../DESIGN.md) §2: determinism-first and secret masking.
- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md): the hosted topology and the deferred "structured JSON logs" observability row that this item realizes.
- [BE-0032 — Secret variables](../BE-0032-secret-variables/BE-0032-secret-variables.md): the secret-masking machinery that this item shares.
- [BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md): the redacted-path philosophy that this item extends to operational logs.
- [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) and [BE-0051 — Serve hardening](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md): the serve that this logging instruments.
- [evidence.md](../../docs/evidence.md): the evidence subsystem that this item deliberately keeps distinct.
