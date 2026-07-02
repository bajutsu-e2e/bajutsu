**English** · [日本語](ja/evidence.md)

# The evidence (Evidence/Trace) subsystem

Evidence capture for a recurring action is expressed as a **repeatedly-firing rule** rather than a one-shot instruction. This ensures the same evidence is collected without AI on every subsequent run.

Implementation: `bajutsu/evidence.py` (instant + Sinks) · `bajutsu/intervals.py` (interval: video / deviceLog / appTrace). Firing is decided on the orchestrator side ([run-loop](run-loop.md#evidence-rule-firing)).

Related: [the capture tokens in scenarios](scenarios.md#capture-token-grammar) · [reporting](reporting.md)

---

## Three ways to request evidence

| Way | Use | Example |
|---|---|---|
| **A. Rules (`capturePolicy`)** ★ central | automatic capture **every time** a particular action happens | screenshot + elements on every tap of `settings.*` |
| **B. Per-step (`capture:`)** | this one step only | video + deviceLog around a specific wait |
| **C. Default policy** | a baseline guarantee | config's `capture: [screenshot.after, elements, actionLog]` |

> C (config default) `capture` resolves to `Effective.capture` ([configuration](configuration.md)),
> but currently the run loop uses only the scenario's `capturePolicy` and the per-step `capture` as
> firing sources. The wiring to auto-apply the config default capture to every step is not in place.

## Evidence kinds and acquisition timing

A `capture:` token is `<kind>[.<modifier>]` ([scenarios](scenarios.md#capture-token-grammar)).

| Kind | Source | Interval / instant | Status |
|---|---|---|---|
| `screenshot` | the driver (idb uses `simctl io screenshot`) | instant | ✅ captured |
| `elements` (a11y / accessibility tree) | `driver.query()` as JSON | instant | ✅ captured |
| `actionLog` | orchestrator internals (action · duration) | — | ✅ inherent in the manifest |
| `video` | `simctl io recordVideo` | interval | ✅ captured (needs udid) |
| `deviceLog` | `simctl spawn log stream` | interval | ✅ captured (needs udid) |
| `network` | the in-app collector (BajutsuKit → `network.json`) | interval | ✅ captured (the `--network` run flag) |
| `appTrace` | `simctl spawn log stream` over the app's os_log subsystem | interval | ✅ captured (needs udid + subsystem) |

> `appTrace` pairs the app's `os_signpost` / `os_log` `<name> started` / `<name> finished` markers
> into timed intervals (`intervals.parse_app_trace`). `network` is produced by the request collector
> rather than the interval system — its exchanges are written to `<sid>/network.json`
> ([network observation](drivers.md), the `--network` flag).

**Default modifiers**: instant kinds (`screenshot`/`elements`) default to `after`; interval kinds
(`video`/`deviceLog`) default to `around` (start before the action, stop after the step). Stating
`screenshot.before` explicitly yields a filename like `before.png`.

## A. `capturePolicy` (rule-based)

Repeatedly-firing rules, written per scenario (implementation: `scenario/models/evidence.py` `CaptureRule` /
`Trigger`).

```yaml
capturePolicy:
  # On every tap of settings.*, capture the post-tap screenshot and elements
  - on: { action: tap, idMatches: "settings.*" }
    capture: [screenshot.after, elements]

  # On every screen transition
  - on: { event: screenChanged }
    capture: [screenshot.around, elements]

  # On error in any step, capture the maximum (the safety net)
  - on: { result: error }
    capture: [screenshot, video, deviceLog, elements, actionLog]
```

(real example in [`demos/showcase/scenarios/settings.yaml`](../demos/showcase/scenarios/settings.yaml))

The trigger `on` is **exactly one** of `action` / `event` / `result`:

- `action: <tap|longPress|type|swipe|...>` — optionally combined with `idMatches` (glob against the
  primary target's `id`). `idMatches` can only be used with `action`.
- `event: screenChanged` — fires if `query()` changed during that step.
- `result: error` — fires if the step failed (the safety net).

The detailed firing logic is in [run-loop](run-loop.md#evidence-rule-firing).

> **Preview firing before a run (BE-0028).** A loose glob or a `screenChanged` rule can fire on
> far more steps than intended, and attaching a heavy capture (`video` / `deviceLog` / `appTrace` /
> `network`) to it quietly produces gigabytes. `bajutsu trace --explain <scenario.yaml>` is a
> read-only dry run that counts
> how many times each rule would fire (and on which steps), and flags ⚠ a heavy capture on a
> broadly-matching rule — so you can tighten the match before paying for it. See [cli](cli.md#trace).

## B. Inline evidence

To capture just one step, attach `capture:` directly to the step.

```yaml
- tap: { id: settings.reindex }
- wait: { for: { id: settings.reindexComplete }, timeout: 5 }
  capture: [video, deviceLog]     # record the interval of this wait
```

(real example in [`demos/showcase/scenarios/evidence.yaml`](../demos/showcase/scenarios/evidence.yaml))

## Interval evidence (video / deviceLog / appTrace)

Implementation: `bajutsu/intervals.py`. These are **backend-independent `simctl` child processes**:
started before the action and stopped after the step settles. Process spawning is injectable
(`Spawn`) and testable. (`appTrace` is an interval too — a `log stream` over the app's os_log
subsystem, paired into timed intervals by `parse_app_trace`.)

> **Interval kinds are opt-in (BE-0028).** `video` / `deviceLog` / `appTrace` are heavy, so a
> scenario records an interval **only when it asks for that kind** — through an inline `capture:`
> or a `capturePolicy` rule (e.g. a `result: error` rule that captures `video`). A scenario that
> requests none records none, keeping the common case cheap; the lightweight instant baseline
> (`screenshot` + `elements`) is always captured, so a failure still leaves evidence (DESIGN §10).
> Preview what a scenario would record with `bajutsu trace --explain` (see [cli](cli.md#trace)).

| Kind | Start command | Stop signal | Filename |
|---|---|---|---|
| `video` | `simctl io <udid> recordVideo --codec h264` | **SIGINT** (a hard kill would corrupt the mp4) | `segment.mp4` |
| `deviceLog` | `simctl spawn <udid> log stream --level debug --style compact [--predicate ...]` | SIGTERM | `device.log` |

- `start_video` / `start_device_log` return an `Interval`, and `Interval.stop()` sends the signal
  and finalizes the file. Stop waits up to 10s, then kills.
- deviceLog can be narrowed by `--predicate` (NSPredicate) to a subsystem, etc. (the CLI's
  `--log-predicate`).
- `INTERVAL_KINDS = {"video", "deviceLog", "appTrace"}`. The orchestrator uses this set to split
  "interval / instant."

## Sinks (where evidence goes)

```python
class EvidenceSink(Protocol):
    def capture(self, driver, step_id, kinds, *, elements=None) -> list[Artifact]: ...   # instant captures after a step
    def start_scenario_intervals(self, scenario_id, kinds) -> list[Interval]: ...        # begin video / deviceLog / appTrace for the whole scenario
    def finish_scenario_intervals(self, scenario_id, started) -> list[Artifact]: ...     # stop them and collect the files
```

| Sink | Behavior |
|---|---|
| `NullSink` (default) | writes nothing (keeps a run side-effect-free) |
| `FileSink(run_dir, udid, log_predicate)` | writes under `run_dir/<step_id>/` |

`FileSink` skips interval captures if `udid` is absent (they need simctl). The CLI's `run` uses
`FileSink(runs/<runId>, udid=..., log_predicate=...)` ([cli](cli.md#run)).

## Artifact provenance (provider)

Every piece of evidence is recorded as an `Artifact(name, kind, provider)`, leaving in the manifest
**which provider it came from**.

```python
@dataclass
class Artifact:
    name: str       # filename (e.g. "after.png")
    kind: str       # "screenshot" / "elements" / "video" / "deviceLog" / "network"
    provider: str   # who supplied this artifact (see table below)
```

| `provider` value | Meaning |
|---|---|
| `"driver"` | The actuator captured it directly (screenshots, element trees). |
| `"simctl"` | Interval evidence from `simctl` (video, device log, app trace). |
| `"collector"` | The idb app-side network collector (`BAJUTSU_COLLECTOR`). |
| `"playwright"` | Native Playwright network observation (web backend). |
| `"<backend> (fallback)"` | A read-only evidence fallback supplied the artifact ([BE-0020](../roadmaps/implemented/BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback.md)). |

When an evidence kind cannot be supplied by any backend in the list, a `SkippedCapture(kind,
reason)` is recorded per scenario and disclosed in the manifest — the gap is never silently empty.

## Masking (redact)

Screenshots, logs, and network data can capture PII (personally identifiable information) and tokens. Declare what to mask before writing. Implementation: `scenario/models/evidence.py` `Redact`. Config's `redact` and the scenario's `redact` are merged (union) ([configuration](configuration.md#merging-redact)).

```yaml
redact:
  labels: ["Card Number"]               # accessibility labels
  headers: ["Authorization", "Cookie"]  # HTTP header names
  fields: ["token", "password"]         # JSON/body field names
```

> Redaction **is applied** before evidence is written (`redaction.py` `Redactor`): the device log /
> app trace are scrubbed by key→value patterns, the element tree masks a value when its label is
> configured (or scrubs an embedded secret), and each network exchange is masked structurally —
> header values by name, and the url / request / response bodies as free text (so query params and
> `token` / `password` body fields are caught). Images (screenshots / video) cannot be masked and
> are left as-is.
>
> Redaction also extends to **secret input values**: the literal values behind `${secrets.X}`
> (resolved from the environment, declared via config's `secrets:`
> [configuration](configuration.md#secrets-secrets)) are masked wherever they would appear in
> evidence — not just the configured `labels` / `headers` / `fields`. Longest values are masked
> first so a value that is a substring of another never leaves a partial leak.
