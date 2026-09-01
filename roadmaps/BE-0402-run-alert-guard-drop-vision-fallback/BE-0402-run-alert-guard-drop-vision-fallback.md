**English** · [日本語](BE-0402-run-alert-guard-drop-vision-fallback-ja.md)

# BE-0402 — Drop the AI-vision fallback from run's reactive system-alert guard

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0402](BE-0402-run-alert-guard-drop-vision-fallback.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0402") |
| Implementing PR | [#PENDING](https://github.com/bajutsu-e2e/bajutsu/pull/PENDING) |
| Topic | AI provider configuration |
| Related | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md), [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md), [BE-0394](../BE-0394-ai-provider-none-kill-switch/BE-0394-ai-provider-none-kill-switch.md) |
<!-- /BE-METADATA -->

## Introduction

`run`'s reactive system-alert guard (`systemAlertHandling`) clears an operating-system prompt that
the application's own accessibility tree cannot see: a notification-permission request, App Tracking
Transparency (ATT), or the cross-process paste consent. Since
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md),
the guard prefers a deterministic native path on the iOS XCUITest backend. It queries SpringBoard for
the alert's buttons and taps a policy-named one, with no model call. Where that native path cannot
act — an incapable backend, a surface the query cannot enumerate, or a button no policy names — it
falls back today to `bajutsu/agents/alerts.py`'s `ClaudeAlertLocator`. A screenshot goes to Claude
vision, and the guard taps the coordinate the model returns.

This proposal removes that vision fallback from `run` entirely. The guard's deterministic half stays
unchanged: the native SpringBoard probe, the `rules`/`instruction` label policy, and the in-tree
dismiss path. The half that resolves a blocking prompt by asking a model to read a screenshot is
deleted from `run`'s path. When the native path cannot act, the guard now does nothing. The blocked
step or `wait` continues toward its own timeout exactly as it would with no guard configured, and the
eventual timeout message names the alert it saw, when it saw one, instead of reading as an
unexplained missing element. `record` and `crawl` are unaffected: both are already Tier-1,
AI-driven authoring paths outside prime directive 1's deterministic-gate concern, and both keep
dismissing an unexpected prompt through the same vision locator while a human or an agent composes a
scenario.

## Motivation

`run` is Bajutsu's deterministic gate. Prime directive 2 puts determinism first, and prime
directive 1 keeps a large language model (LLM) off the pass/fail path entirely. The vision fallback
does not decide pass/fail: `AlertGuardConfig.__call__` and `_AlertGuardGate` in
`bajutsu/orchestrator/waits.py` treat it strictly as an accelerant, never the wait's own condition
check. It still acts on the device mid-run, though — it takes a screenshot, sends it to a hosted
model, and taps wherever the model's answer lands. Two runs of the same scenario against the same
build can therefore diverge in when, or whether, a screen recovers from an unnamed prompt, even
though no single run's outcome is judged by the model. `run --system-alert-handling` is also, today,
the one path inside the deterministic gate that reaches an LLM at all. `bajutsu/capabilities.py`
already records `--system-alert-handling` as the flag that flips `run` off the Claude-free
classification (BE-0101), and `bajutsu/ai/__init__.py`'s module docstring names the same guard as the
seam's one broadly reachable entry point from inside `run`. Removing the fallback closes that one
remaining path, so `run`'s "Claude-free by construction" claim holds with no flag-dependent exception.

[BE-0394](../BE-0394-ai-provider-none-kill-switch/BE-0394-ai-provider-none-kill-switch.md) already
lets a project opt out of the fallback with `ai: { provider: none }`. Its own Motivation section
names the same three properties as reasons a project might need the fallback gone: an unmasked
screenshot leaving the machine, a coordinate tap where a native query would instead resolve to a
named button, and a model round trip on the run's critical path. That proposal's contribution was
turning an environmental absence — no credential configured — into a committed, reviewable statement
a project opts into. It left the code path itself in place and on by default, so a project that never
sets `ai.provider` still ships a scenario suite whose alert recovery is, for the cases native cannot
name, an AI-vision call. This proposal is the complement. Instead of one more way to switch the
fallback off, it removes the fallback from `run`, so no configuration is needed to get the
deterministic-only behavior and no configuration can bring the model call back.

The fallback's practical coverage has also shrunk since it was written, which is what makes deleting
it, rather than continuing to gate it, the right call now. The two backends that never advertised the
native `HANDLE_SYSTEM_ALERT` capability do not lean on vision for their common prompts either. The web
(Playwright) backend auto-dismisses a JavaScript (JS) dialog through a fixed, non-destructive policy
before the guard ever runs (`bajutsu/drivers/playwright.py:381`). The Android (adb) backend surfaces
a system permission dialog inside the same window dump an ordinary `tap` already reaches
(`bajutsu/drivers/adb.py:1589`). On the iOS XCUITest backend,
[BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md) let a
scenario name each covered prompt by its own rule, so the three prompts
`bajutsu/scenario/system_alerts.py` covers today resolve natively even when they share a button
label. What is left for the fallback to answer is a genuinely unanticipated prompt: one no rule,
candidate label, or in-tree dismiss names. That is precisely the case where a human debugging a red
run needs a legible message, not a best-effort guess.

Free-text `instruction` values are the one piece of today's configuration surface that stops working
once the fallback is gone. A loud rejection serves an author better than a silent no-op here.
`SystemAlertHandling.instruction` (`bajutsu/scenario/models/scenario.py:81`) already documents two
forms. One is a list of candidate labels the native path resolves deterministically. The other is
"a free-text string … the legacy form" that only the vision locator ever interpreted; the native
path, per that same docstring, "ignores the string and taps a default dismissive label instead."
Written in the free-text form, a scenario's setting therefore already has no effect on the
native-capable iOS backend today — and neither does a target config's or a `--alert-instruction`
flag's. On a backend without the native capability, that free-text form is, before this change, the
only thing steering the fallback at all. Removing the
fallback would make it inert everywhere rather than only on iOS — the same class of silent
wrong-answer outcome BE-0382's own Motivation section spent its length ruling out for `rules`.
Rejecting a free-text `instruction` at `run` time, before any scenario's device work begins, keeps
the rejection legible and points an author at the list or `rules` form that still works.

## Detailed design

The work divides into the guard's own two call sites, the CLI (command-line interface) wiring that
constructs it, the free-text validation, and the classification and documentation that follow from
`run` no longer reaching Claude. Each unit below is scoped to `run`. `bajutsu/agents/alerts.py`
(`ClaudeAlertLocator`, `SystemAlertGuard`) and its own test suite stay unchanged, since `record` and
`crawl` still construct and use them.

### 1. `AlertGuardConfig` drops its `vision` field

`AlertGuardConfig` (`bajutsu/orchestrator/types.py:315`) is a `BlockedHandler`: calling it clears a
blocking alert or returns `None`. It is built from a required `vision: BlockedHandler` field plus the
native policy (`labels`, `rules`, `poll_interval`). `__call__` calls `self.probe_native` first and,
on anything other than `"dismissed"`, falls through to `self.vision(driver)`. This unit removes the
`vision` field entirely. `__call__` becomes `probe_native`'s own dispatch: return the `AlertEvent` on
`"dismissed"`, `None` on `"incapable"` / `"absent"` / `"unhandled"`, with no second call.
`NativeAlertState`'s four values, `pick_alert_label`, and `match_alert_rule` stay unchanged; only the
state a caller can no longer trade for a model call changes.

The same unit widens `probe_native`'s return so the buttons it already read survive the call. The
method returns `tuple[NativeAlertState, AlertEvent | None]` today and fills the event only for
`"dismissed"`; on `"unhandled"` it returns `("unhandled", None)`, discarding the `buttons` list it
just read from `driver.system_alert_labels()` (`bajutsu/orchestrator/types.py:348`). Units 2 and 3
need exactly that list to name the alert, so the return grows a third member carrying the observed
labels — `tuple[NativeAlertState, AlertEvent | None, list[str]]`, empty except where a query actually
saw an alert. Widening the existing return is preferred over re-querying `system_alert_labels()` at
the `"unhandled"` moment: a second cross-process query costs another round trip on the runner's
single main thread and reopens the time-of-check/time-of-use window that `probe_native`'s own
dismiss-race branch exists to close.

### 2. `_AlertGuardGate` reports an unhandled alert instead of calling vision

`_AlertGuardGate` (`bajutsu/orchestrator/waits.py:189`) is the mid-wait trigger BE-0269 added. Fed
each poll's tree via `observe`, it prefers the native probe on a native-capable backend
(`_observe_native`) and otherwise watches the collapsed-tree proxy (`_observe_vision`,
`shows_app_ui`) for a screen that looks blocked. Both paths currently end in `_fire_vision_bounded`,
which spends the model call under a cooldown and a hard per-wait attempt ceiling before giving up and
logging a warning. This unit removes the model call and the machinery that bounded it
(`_fire_vision_bounded`, the cooldown, the attempt ceiling) and replaces it with a note the gate
records instead of acting on:

- On a native-capable backend, `probe_native`'s `"unhandled"` state — an alert is up, but no rule or
  candidate label names its button — records the button labels that probe now returns (unit 1).
- On a backend without the native capability, or on a native backend's `"absent"` poll — a
  non-SpringBoard surface the query cannot enumerate, such as an action sheet or a WKWebView
  dialog — the same debounced collapsed-tree signal that used to trigger vision instead records that
  the screen looks blocked, naming no button labels.

The gate exposes this note through a method the wait loop consults only when it is about to report a
timeout, so a transient or self-resolved block never reaches the timeout message. The existing
debounce (`_GUARD_DEBOUNCE_POLLS`) already filters a transient collapsed frame, and the note reflects
the gate's most recent observation, not a sticky flag left over from earlier in the wait.

### 3. Both guard call sites name an unhandled alert in their failure reason

The guard has two call sites, and the Introduction's promise — that a blocked step *or* wait says
what blocked it — holds only if both carry the note.

The **mid-wait gate** is the first. `_wait`'s `for` branch and its `screenChanged` branch
(`bajutsu/orchestrator/waits.py:608` and `:663`) are the two guarded branches that can report a
timeout; `gone` and `request` are not guarded, and `settled` never fails. Both already build a plain
string, for example `f"wait timeout: for {target} ({timeout}s)"`. This unit appends the gate's note
(unit 2) to that string when one is present at the moment of timeout. A run failing behind an
unrecognized system prompt then reads, for example, as `wait timeout: for #submit (10.0s) — an
unhandled system alert is blocking the screen (buttons: Allow, Don't Allow)`, instead of naming only
the element that never appeared. The hedged, label-less form covers the non-native-capable case:
`… the screen appears blocked, possibly by a system alert or another overlay outside the app's view`.

The **end-of-step and `expect` retry** is the second, and it calls `AlertGuardConfig` directly rather
than through the gate: `alert_guard(driver)` after a failed `expect`
(`bajutsu/orchestrator/loop.py:700`) and `self.cfg.alert_guard(active_driver)` on the step retry
(`:1442`). After unit 1 that call returns `None` for every state other than `"dismissed"`, and the
step keeps its own `reason` untouched. A `tap` blocked by an unanticipated alert outside a wait would
therefore still fail as a bare `element not found` — the exact reading this proposal exists to
remove. The gate's note cannot serve here, because the end-of-step retry holds no
`_AlertGuardGate`. Unit 1's widened `probe_native` return can: `AlertGuardConfig` keeps the labels
from its most recent probe, and both call sites read the note from there, so the two sites share one
source instead of growing two notions of "the alert we saw". That state is safe to hold on the config
because `_guard_for` builds one `AlertGuardConfig` per scenario and a scenario's steps run in
sequence, so no note crosses a scenario or a worker boundary. The step's failure reason gains the same
suffix the wait's does.

### 4. `run`'s CLI wiring stops constructing the vision locator

`_alert_guard_factory` and `_vision_instruction` (`bajutsu/cli/commands/run.py:420`–`525`) build the
shared `ClaudeAlertLocator`, bind it into a `SystemAlertGuard`, and wrap it as the `vision` callback
each scenario's `AlertGuardConfig` carries. This unit deletes `_vision_instruction`, the
locator/guard construction, and the per-scenario `vision` closure. `_alert_guard_factory` is left to
build each scenario's `AlertGuardConfig` from `labels`, `rules`, and `poll_interval` alone, since unit
1 already dropped the field they were feeding. `_build_alert_locator` and `_build_alert_guard`
(`bajutsu/cli/_shared.py:460`–`519`) stay unchanged: `record` and `crawl` still call them directly.

The same unit retires `run --alert-instruction` (`bajutsu/cli/commands/run.py:999`). The option takes
a bare string, so every value a user can pass is the free-text form; unit 5 turns a non-empty one into
a `run`-time error, and an empty one already normalizes to `None` (`run.py:454`). Its only two
remaining outcomes would be "no effect" and "abort the run" — trap surface on `run --help` with no
legal value. It cannot express the list form that replaces it either, since that form is per scenario
(`instruction: [...]`), so the flag is removed rather than reinterpreted. `record` and `crawl` keep
their own `--alert-instruction` (`crawl.py:470`, `record.py:145`), which still steers the vision guard
those two commands retain.

### 5. A free-text `instruction` is a `run`-time validation error

`SystemAlertHandling.instruction` (`bajutsu/scenario/models/scenario.py:125`) stays a
`str | list[str] | None` field; the type does not change, since `record` and `crawl` still accept the
free-text form for their own, unchanged vision guard. What changes is how `run` resolves it. A
Three sources carry the setting: a scenario's own `instruction`, a target config's
`run_defaults.system_alert_handling.instruction`, and the CLI's `--alert-instruction`. The flag is
always a bare string (`bajutsu/cli/commands/run.py:999`), so any non-empty value it carries is
inherently the free-text form. All three are checked by an **eager pass over `scenarios` in
`_alert_guard_factory`'s own body**, before it returns its per-scenario closure. A
resolved value that is a `str` — not a `list[str]`, and not empty — stops the whole `run` invocation
with a message naming the offending scenario or flag and pointing at the list (`instruction: [...]`)
or `rules` form.

The eager pass is the substance of this unit, not an implementation detail. `_alert_guard_factory`
returns a lazy `AlertGuardFor` callable (`bajutsu/runner/types.py:25`) that the pipeline invokes per
scenario inside the run loop, in a worker, after the run has already started
(`bajutsu/runner/pipeline.py:358`). A check placed inside `_guard_for` would therefore fire on
scenario N with scenarios 1…N-1 already executed — exactly partway through a run. Running the pass
over every scenario up front is what makes a suite rejected or accepted as a whole.

That deliberately departs from `SystemAlertHandling.resolved_locale`'s shape. The locale check raises
from inside `_guard_for`, and the runner catches it as one scenario's failure (`run.py:496-500`), so a
suite carrying one uncovered locale still runs its other scenarios. An unusable `instruction` is an
authoring mistake in the file rather than a condition of the run, and it is detectable without a
device, so it is worth catching before any scenario starts. The behavioral difference between the two
is real on a mixed suite, which is why this unit names it rather than leaving an implementer to reach
for the nearer precedent.

### 6. The Claude-boundary classification and documentation catch up

- `bajutsu/capabilities.py`'s `Capability("run", uses_claude=False, claude_flag="--system-alert-handling")`
  loses its `claude_flag`. `run` no longer reaches Claude under any flag, so the entry becomes
  `Capability("run", uses_claude=False)`, matching every other Claude-free command with no flag that
  changes its classification.
- `bajutsu/ai/__init__.py`'s module docstring names `run --system-alert-handling`'s alert guard as
  the AI seam's one broadly reachable entry point from inside `run` — the `credential_gap` lookup
  that decides whether to construct the vision locator. That sentence, and
  `--system-alert-handling`'s place in the docstring's list of Tier-1 paths, are removed along with
  the code path they describe.
- `docs/architecture.md`'s "DSL (domain-specific language) system-alert and tip handling" section,
  and its `docs/ja/architecture.md` mirror, describe `systemAlertHandling` as dismissing "with the
  AI-vision guard demoted to a fallback for what the native path can't name." That clause is
  rewritten to describe the new behavior: an unhandled alert surfaces in the wait's own timeout
  message, and a free-text `instruction` is rejected at `run` time.
- `CLAUDE.md`, `CONTRIBUTING.md` / `CONTRIBUTING.ja.md`, and `SECURITY.md` / `SECURITY.ja.md` each
  list the paths that need `ANTHROPIC_API_KEY` as "`record`, `run --system-alert-handling`" (or the
  Japanese equivalent). All five files drop `run --system-alert-handling` from that list, since `run`
  needs no AI credential under any flag once this lands.

### Out of scope

- **`record` and `crawl` keep the vision guard.** Both are already Tier-1, AI-driven authoring
  paths — `record` talks to Claude for the whole authoring loop, `crawl` for site exploration — so
  dismissing an unanticipated prompt by the same means costs nothing against prime directive 1 and
  keeps a real authoring convenience.
- **No new native alert-clearing capability for web or Android.** Neither backend advertises
  `HANDLE_SYSTEM_ALERT` today, and this proposal adds none. The motivation above establishes that
  both already handle their common prompts without vision; an unanticipated one on either backend now
  surfaces as the hedged, label-less timeout note from unit 3, the same way an unenumerable surface
  does on iOS.
- **No new heuristic stand-in for vision** — a default coordinate, the top-most alert-shaped
  element, and so on.
  [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)'s
  own Alternatives considered section already rejected a fixed-offset tap, for the same reason this
  proposal does: a guessed answer is the class of nondeterminism prime directive 2 rules out for an
  ambiguous selector, not a property to reintroduce one layer down.
- **`iosTipKitHandling` (BE-0389) is untouched.** It shares the alert guard's end-of-step retry
  shape, but it never called a model to begin with, so it sits outside this proposal's concern.

## Alternatives considered

| Option | Summary | Why not adopted |
|---|---|---|
| Keep the status quo: `ai: { provider: none }` as the only opt-out (BE-0394) | Leave the fallback in place, on by default, switchable off per project | A project that never sets `ai.provider` still ships a suite whose alert recovery is, for the cases native cannot name, an AI-vision call; the reason to remove it — a model call inside the deterministic gate — does not depend on any one project's configuration |
| Add a heuristic, model-free stand-in for the unresolved case (a fixed offset, the top-most alert-shaped element) | Keep some automatic recovery for a prompt no rule or label names | Rejected for the same reason BE-0315 rejected a fixed-offset tap: a guessed answer is the same nondeterminism prime directive 2 rules out elsewhere for an ambiguous selector |
| Remove the vision guard everywhere, including `record` and `crawl` | One smaller module (`agents/alerts.py` deleted outright), one class of behavior to reason about | Rejected in this proposal's walkthrough: `record` and `crawl` are already Tier-1, AI-driven paths outside prime directive 1's deterministic-gate concern, and removing their guard would regress a real authoring convenience for no gain in determinism |
| Silently ignore a free-text `instruction` on `run` (treat it as the built-in dismissive default) | Backward-compatible: an existing scenario written in the legacy form keeps running, minus the answer it named | Rejected: a scenario that wrote `instruction: "tap Allow"` to grant a permission would silently deny it instead — the exact silent-wrong-answer outcome BE-0382's own Motivation section catalogued for the same field |

## Progress

- [x] Unit 1 — `AlertGuardConfig` drops its `vision` field; `probe_native` returns the buttons it read.
- [x] Unit 2 — `_AlertGuardGate` records what blocks the screen instead of calling vision; the
      cooldown and per-wait attempt ceiling that bounded the model call are gone with it.
- [x] Unit 3 — both guard call sites name an unhandled alert in their failure reason. The `gone`
      branch carries the note too: it became guarded after this proposal was written.
- [x] Unit 4 — `run`'s CLI wiring stops constructing the vision locator, and `--alert-vision-instruction`
      is retired (`serve`'s CLI mirror follows). `run` also drops the AI usage ledger it installed
      only to attribute the guard's tokens, since nothing in it can spend any.
- [x] Unit 5 — a `visionInstruction` from a scenario or a target config stops the whole `run`
      invocation before any scenario's device work begins. BE-0401 landed between this proposal and
      its implementation and split the old free-text `instruction` into `labels` (native) and
      `visionInstruction` (vision-only), so the check names the latter rather than a string form.
      `serve`'s HTTP body rejects `alertVisionInstruction` with a 400 on the same reasoning, so no
      entry point is left inverting a caller's intent silently.
- [x] Unit 6 — the Claude-boundary classification and the documentation catch up in both languages.

## References

- [`bajutsu/agents/alerts.py`](../../bajutsu/agents/alerts.py) — the vision locator, unchanged by
  this proposal.
- [`bajutsu/orchestrator/types.py`](../../bajutsu/orchestrator/types.py) — `AlertGuardConfig`,
  `NativeAlertState`, `pick_alert_label`, `match_alert_rule`.
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `_AlertGuardGate`, `_wait`.
- [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) — `_alert_guard_factory`,
  `_vision_instruction`.
- [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py) — `_build_alert_locator`,
  `_build_alert_guard` (unchanged; still serve `record` / `crawl`).
- [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py) —
  `SystemAlertHandling`.
- [`bajutsu/capabilities.py`](../../bajutsu/capabilities.py) — the Claude-boundary classification.
