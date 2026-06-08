**English** · [日本語](ja/evidence.md)

# The evidence (Evidence/Trace) subsystem

> Treat the request "capture evidence every time a particular action happens" as a
> **repeatedly-firing rule** rather than a one-shot instruction. This way the same evidence
> reproduces without AI on the second run onward.
>
> Implementation: `bajutsu/evidence.py` (instant + Sinks) · `bajutsu/intervals.py` (interval:
> video / deviceLog). Firing is decided on the orchestrator side
> ([run-loop](run-loop.md#evidence-rule-firing)).

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
| `elements` (a11y tree) | `driver.query()` as JSON | instant | ✅ captured |
| `actionLog` | orchestrator internals (action · duration) | — | ✅ inherent in the manifest |
| `video` | `simctl io recordVideo` | interval | ✅ captured (needs udid) |
| `deviceLog` | `simctl spawn log stream` | interval | ✅ captured (needs udid) |
| `network` | (the mock server) | interval | ⚠️ **not implemented** (no source) |
| `appTrace` | (os_signpost / OSLog) | interval | ⚠️ **not implemented** |

> `network` / `appTrace` **validate** as capture tokens (valid in the schema), but there is no
> acquisition implementation in `evidence.py` / `intervals.py`, so they are not recorded today.

**Default modifiers**: instant kinds (`screenshot`/`elements`) default to `after`; interval kinds
(`video`/`deviceLog`) default to `around` (start before the action, stop after the step). Stating
`screenshot.before` explicitly yields a filename like `before.png`.

## A. `capturePolicy` (rule-based)

Repeatedly-firing rules, written per scenario (implementation: `scenario.py` `CaptureRule` /
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

(real example in [`sample/scenarios/settings.yaml`](../sample/scenarios/settings.yaml))

The trigger `on` is **exactly one** of `action` / `event` / `result`:

- `action: <tap|longPress|type|swipe|...>` — optionally combined with `idMatches` (glob against the
  primary target's `id`). `idMatches` can only be used with `action`.
- `event: screenChanged` — fires if `query()` changed during that step.
- `result: error` — fires if the step failed (the safety net).

The detailed firing logic is in [run-loop](run-loop.md#evidence-rule-firing).

## B. Inline evidence

To capture just one step, attach `capture:` directly to the step.

```yaml
- tap: { id: settings.reindex }
- wait: { for: { id: settings.reindexComplete }, timeout: 5 }
  capture: [video, deviceLog]     # record the interval of this wait
```

(real example in [`sample/scenarios/evidence.yaml`](../sample/scenarios/evidence.yaml))

## Interval evidence (video / deviceLog)

Implementation: `bajutsu/intervals.py`. Both are **backend-independent `simctl` child processes**:
started before the action and stopped after the step settles. Process spawning is injectable
(`Spawn`) and testable.

| Kind | Start command | Stop signal | Filename |
|---|---|---|---|
| `video` | `simctl io <udid> recordVideo --codec h264` | **SIGINT** (a hard kill would corrupt the mp4) | `segment.mp4` |
| `deviceLog` | `simctl spawn <udid> log stream --level debug --style compact [--predicate ...]` | SIGTERM | `device.log` |

- `start_video` / `start_device_log` return an `Interval`, and `Interval.stop()` sends the signal
  and finalizes the file. Stop waits up to 10s, then kills.
- deviceLog can be narrowed by `--predicate` (NSPredicate) to a subsystem, etc. (the CLI's
  `--log-predicate`).
- `INTERVAL_KINDS = {"video", "deviceLog"}`. The orchestrator uses this set to split "interval /
  instant."

## Sinks (where evidence goes)

```python
class EvidenceSink(Protocol):
    def start_intervals(self, step_id, kinds) -> list[Interval]: ...   # start intervals before the action
    def capture(self, driver, step_id, kinds) -> list[Artifact]: ...   # acquire instant captures after the step
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
    kind: str       # "screenshot" / "elements" / "video" / "deviceLog"
    provider: str   # "driver" (instant) / "simctl" (interval)
```

## Masking (redact)

Because screenshots / logs / network can capture PII and tokens, declare what to mask before
writing. Implementation: `scenario.py` `Redact`. Config's `redact` and the scenario's `redact` are
merged (union) ([configuration](configuration.md#merging-redact)).

```yaml
redact:
  labels: ["Card Number"]               # accessibility labels
  headers: ["Authorization", "Cookie"]  # HTTP header names
  fields: ["token", "password"]         # JSON/body field names
```

> ⚠️ The **application** of redact (the actual masking) is future wiring, together with the
> evidence it targets (especially network). Today it goes only as far as the declaration (the schema
> and the merge).
