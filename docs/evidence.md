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

Implementation: `bajutsu/intervals.py`. These are **subprocess child processes** — `simctl` on iOS,
`adb` on Android — started before the action and stopped after the step settles. Process spawning is
injectable (`Spawn`) and testable. Web has no subprocess: its intervals are Playwright-native and
supplied by the driver (see below). (`appTrace` is an iOS interval too — a `log stream` over the
app's os_log subsystem, paired into timed intervals by `parse_app_trace`.)

> **Interval kinds are opt-in (BE-0028).** `video` / `deviceLog` / `appTrace` are heavy, so a
> scenario records an interval **only when it asks for that kind** — through an inline `capture:`
> or a `capturePolicy` rule (e.g. a `result: error` rule that captures `video`). A scenario that
> requests none records none, keeping the common case cheap; the lightweight instant baseline
> (`screenshot` + `elements`) is always captured, so a failure still leaves evidence (DESIGN §10).
> Preview what a scenario would record with `bajutsu trace --explain` (see [cli](cli.md#trace)).

| Kind | Start command (iOS / Android) | Stop signal | Filename |
|---|---|---|---|
| `video` | `simctl io <udid> recordVideo --codec h264` / `adb shell screenrecord` | **SIGINT** (a hard kill would corrupt the mp4) | `scenario.mp4` |
| `deviceLog` | `simctl spawn <udid> log stream --level debug --style compact [--predicate ...]` / `adb logcat -T 1` | SIGTERM | `device.log` |

- `start_video` / `start_device_log` (iOS) and `start_screenrecord` / `start_logcat` (Android)
  return an `Interval`, and `Interval.stop()` sends the signal and finalizes the file. Stop waits up
  to 10s, then kills. `screenrecord` records device-side, so its `Interval` also pulls the finalized
  mp4 off the device on stop and removes the device copy. If the pull fails (the device vanished),
  the sink drops that one artifact with a warning rather than emit a path with no file behind it —
  it never fails the run over evidence I/O. Note `adb screenrecord` caps a single recording at ~180s
  (the platform default/maximum), so an Android video of a longer scenario ends at that mark; the
  on-device tuning of this cap and of SIGINT finalization is part of the deferred BE-0007 e2e.
- deviceLog can be narrowed by `--predicate` (NSPredicate) to a subsystem, etc. (the CLI's
  `--log-predicate`) on iOS; `adb logcat` is unfiltered (a logcat filterspec is a different syntax, a
  later knob) and starts the follow from the tail so it reflects the scenario window, not the whole
  ring buffer.
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

Interval captures come from the driver's `driver_interval` provider when it supplies one (web's
Playwright-native console / video, Android's `adb` screenrecord / logcat); otherwise `FileSink`
takes the simctl path, which it skips when `udid` is absent. The CLI's `run` uses
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
| `"adb"` | Interval evidence from `adb` (screenrecord video, logcat device log). |
| `"collector"` | The idb app-side network collector (`BAJUTSU_COLLECTOR`). |
| `"playwright"` | Native Playwright network observation (web backend). |
| `"<backend> (fallback)"` | A read-only evidence fallback supplied the artifact ([BE-0020](../roadmaps/BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback.md)). |

When an evidence kind cannot be supplied by any backend in the list, a `SkippedCapture(kind,
reason)` is recorded per scenario and disclosed in the manifest — the gap is never silently empty.

## Visual evidence

A `visual` assertion produces a `VisualEvidence` record carried into the manifest and the
report. It contains the run-dir-relative paths to the baseline copy, the actual screenshot,
and the diff visualization (when the comparison found differences), plus `diff_pct` (the
percentage of pixels that differed) and `engine` — the comparison engine that produced the
verdict (`"exact"` or `"pixelmatch"`; [BE-0165](../roadmaps/BE-0165-visual-compare-engines/BE-0165-visual-compare-engines.md)).

The engine is selected per assertion (`compare:`) with a target-level config fallback
(`visualCompare`), and is recorded in the manifest so the algorithm that produced each
verdict is traceable. Implementation: `bajutsu/assertions.py` `VisualEvidence`.

## Masking (redact)

Screenshots, logs, and network data can capture PII (personally identifiable information) and tokens. Declare what to mask before writing. Implementation: `scenario/models/evidence.py` `Redact`. Config's `redact` and the scenario's `redact` are merged (union) ([configuration](configuration.md#merging-redact)).

```yaml
redact:
  labels: ["Card Number"]               # accessibility labels
  headers: ["X-Session"]                # extra HTTP header names (on top of the defaults)
  fields: ["token", "password"]         # JSON/body field names
  unmaskHeaders: ["authorization"]      # opt out of a default (visible, deliberate)
```

> **Sensitive headers are masked by default** (a scenario needs no `redact:` for this): the
> built-in set is `authorization`, `proxy-authorization`, `cookie`, `set-cookie`, `x-api-key`,
> and `x-auth-token`, matched case-insensitively. `cookie` and `set-cookie` are treated as one
> concern — naming (or unmasking) either covers both. Header names in `redact.headers` add to
> this set; they never replace it. If you genuinely need a default header's raw value (e.g.
> debugging an auth failure), name it under `unmaskHeaders` — turning off protection is an
> explicit, visible choice, never the mere absence of `redact:`.

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
>
> Value matching is **encoding-aware**: the same secret reaches evidence verbatim but often
> encoded, so its literal bytes never appear. Alongside the raw value, redaction masks its
> common encodings — percent-encoded (a URL query or form field, e.g. `p@ss` as `p%40ss`),
> HTML-escaped and JSON-escaped forms, and an `Authorization: Basic <base64(user:pass)>` token
> whose decoded credential carries the value. This is a fixed set of transforms applied to
> *known* values (the value is encoded, then searched for), not a decode-everything scan, so
> the cost and false-positive surface stay bounded. One limitation remains: where evidence is
> genuinely fragmented before redaction runs (a value split across streamed chunks that
> redaction never sees as one contiguous string), matching is best-effort — assembled full-text
> evidence, the common case, is unaffected.
>
> The executed scenario is also snapshotted into the run directory (`scenario.yaml`, and the raw
> YAML view in the report). A `totp` step's `secret` is a durable base32 seed, not a one-time code,
> so a **literal** seed written straight into the scenario is masked to `<redacted>` in that
> snapshot — a `${secrets.X}` reference is kept as-is (it is not the seed, and its resolved value
> is masked by the secret-value rule above). Prefer `${secrets.X}` for a `totp` seed so it never
> sits in the scenario file to begin with.

## File permissions

Redaction reduces what a leaked artifact reveals, but it is a best-effort denylist, so who can read the artifact matters too. The runner creates each run directory owner-only (`0700`) and writes the sensitive files it may hold — `network.json`, the copied `scenario.yaml`, the element dump (`elements.json`), and screenshots — owner-only (`0600`), independent of the host's `umask` ([BE-0131](../roadmaps/BE-0131-run-artifact-permissions/BE-0131-run-artifact-permissions.md)). Everything else lands under the `0700` run directory, so a run's evidence is not readable by another local account on a shared host (a CI runner, say) by default. Implementation: `artifact_perms.py`.
