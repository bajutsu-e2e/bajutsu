**English** · [日本語](BE-0457-real-device-step-lint-ja.md)

# BE-0457 — Lint and skip steps a target's capabilities cannot run, with an explicit skipIfUnsupported opt-out

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0457](BE-0457-real-device-step-lint.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0457") |
| Implementing PR | [#2211](https://github.com/bajutsu-e2e/bajutsu/pull/2211) (units 1–3, 6, 7, 11, 12) |
| Topic | Platform support |
| Related | [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md), [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md) |
<!-- /BE-METADATA -->

## Introduction

A scenario written against the iOS Simulator often holds steps that cannot run on a real iPhone.
`setLocation`, `push`, and the clipboard steps all drive `simctl`, which reaches the Simulator alone.
Today `bajutsu lint` cannot see that gap, because it never reads the target config.

This item makes `bajutsu lint` report each [step](../../docs/glossary.md#scenario-authoring) and
precondition that the selected [target](../../docs/glossary.md#target-app-device) cannot run. The
target's own capability set decides it, the same set the run preflight already reads. A real device
is the motivating case, since it lacks every `simctl`-backed capability. The explicit opt-out is
`skipIfUnsupported`, always with a written reason:

- As a step modifier, `skipIfUnsupported: "<reason>"`, the runner skips the step when its routed
  target lacks a capability the step needs.
- As a scenario-level map, `skipIfUnsupported: { erase: "<reason>" }`, the runner skips the named
  [precondition](../../docs/glossary.md#scenario-authoring) when the target cannot apply it. The map
  takes `erase`, `seedPhotos`, and `permissions`.

The name states the condition and the action, not a device class. Whether a step can run depends on
the environment's capabilities. The opt-out names that dependency directly. A gap that carries no
opt-out fails the lint. The same gap fails the run before any device work. The target's
`appPath` install is the one exception: it lives in the target config, not the scenario, so Bajutsu
ignores it on a real device with a notice.

## Motivation

A Simulator-only step surfaces late on a real device. The run-time narrowing already exists.
`capabilities_for_run` (`bajutsu/common/backends.py:194`) drops the `simctl`-backed capabilities. It
does so for a target with `xcuitest.deviceType: device`. The dropped set is `DEVICE_CONTROL_ALL`, the
iOS permission grants, and `DEVICE_GROUP`. The capability preflight of BE-0082 then turns the whole
scenario away before any device work. BE-0238 relies on that preflight verdict.

That preflight runs on the `doctor` and `run` paths alone. `bajutsu lint`
(`bajutsu/cli/commands/lint.py:10`) takes a scenario file and nothing else. It checks the grammar, never
the target. Three costs follow from that gap:

- **A developer porting a scenario learns of the gap late.** The developer must invoke `run` or
  `doctor` against the real-device config to see which steps cannot run. Until then, a `setLocation`
  in the middle of the scenario reads as fine.
- **One scenario cannot serve both devices.** No construct marks a step as Simulator-only so that a
  real-device run skips it. The workaround is a copy of the scenario with the steps deleted. The two
  copies then drift.
- **A precondition failure gives no way forward.** On a real device,
  `xcuitest_environment.py:337-350` raises on `erase`, on an `appPath` install, and on `permissions`.
  Raising is right by default: a scenario that asked for `erase: true` depends on a clean state. Yet the
  author who accepts a dirty state on a real device has no way to say so.

Once this item ships, one command shows the change. Run `bajutsu lint` with `--config` and a
real-device `--target`. The command exits 1 and names each Simulator-only step and precondition by
path. No device is leased first. Next, add `skipIfUnsupported` where the author accepts the gap. The
scenario then lints clean and runs in full on the Simulator. On the real device it runs too, and the
report lists each skipped step and precondition with its reason.

## Detailed design

The design has five parts: the classification, the two opt-outs, the lint, the run-time skip, and the
install-and-launch preconditions. Nothing here adds a model call or a fixed pause. Per-app differences
stay in the target config.

### Which steps and preconditions a target cannot run

This item keeps no separate list of steps a real device cannot run. Each target's capability set
is the single source of truth, as `capabilities_for_run` returns it (`backends.py:270`). On a real
device that set already drops the `simctl`-backed tokens. The lint and the run both judge a step
against that set. A token added to any target's narrowing thus changes both verdicts in one commit.
`DEVICE_GROUP` is the one dropped token that `unsupported` does not act on. It has no
`_REQUIREMENTS` entry. `_preflight_targets` (`pipeline.py:339`) refuses a device group on its own,
ahead of `unsupported`. The lint mirrors that refusal. A `deviceGroups:` declaration whose
member lacks `DEVICE_GROUP` is a lint error. No opt-out covers it, because the run cannot proceed
without the device group either.

The table that maps a step to its capability already exists. `_REQUIREMENTS` in
`bajutsu/common/capability/capability_preflight.py` pairs each device-control step with its BE-0212
token. The same table already maps each `permissions` service to its token, at the location
`scenario.permissions`. The lint and the run call a structured variant of `unsupported` (see *The
lint*). The variant reports every finding for the target's own capability set.

`erase` and `seedPhotos` carry no capability token. The lint checks them directly, against the
route's ability to apply them: today the local real-device XCUITest route cannot. `erase` resolves
the same way `run` resolves it: the scenario's own value, else the target config's `erase`.

### The two `skipIfUnsupported` opt-outs

`Step` gains one modifier at the same level as `name` and `capture`:

```python
# bajutsu/common/scenario/models/steps/step.py
skip_if_unsupported: str | None = Field(default=None, alias="skipIfUnsupported")
```

| Rule | Behavior |
|---|---|
| Value | A non-blank reason string, validated like `Sleep.reason`; an empty or blank value fails at load |
| Exactly-one-action check | `_MODIFIERS` in `steps/_shared.py` gains the field, so the check does not count it as an action |
| On `if`, `forEach`, `web`, or `app` | The block is one skip unit: one unsupported step anywhere inside skips the whole block |
| On `use` or `group` | `expand.py` stamps the reason and one shared skip-unit id onto every expanded step, accepts an identical reason on a step, and refuses a different one — the rule `_merge_target` (`expand.py:77`) already applies to `target`; `_no_modifiers_on_use` gains it as a second exception beside `target` (BE-0446) |
| On a target group (`steps:` with `target`) | `_target_group` accepts it; `_expand_target_groups` (`models/scenario/_targets.py`) and the component path in `expand.py` stamp the same unit onto each nested step |
| On `manual` or `setPrimaryTarget` | Rejected at load |
| On an `if`, `forEach`, `web`, or `app` block holding a `manual` step | Rejected at load; on `use` or `group`, the copied modifier reaches the nested `manual` at expansion and is rejected there |

The two rejections have different reasons. A `manual` step exists to stop a run where a human took
over. Skipping it would hide that stop. A `setPrimaryTarget` step changes how later steps resolve their
target, so skipping it would change the meaning of the rest of the scenario. `_targets.py:320`
already confines `setPrimaryTarget` to top-level steps, so no block can cover one. A `manual` step has
no such limit, which is why a covering block is rejected too. Otherwise the block would bypass the
per-step rule and turn the loud stop into a silent pass.

A **skip unit** is what the run skips as one. The capability gap belongs to one step, but the
author's intent often spans more than one. A `setLocation` and the `assert` that reads the place it
set belong together. A bare non-visual `assert` needs no capability. A modifier on the `assert` alone would
never take effect. Grouping the two under one covered `group:` makes the `assert` skip with its cause. Expansion
records the unit as a hidden `Step.skip_unit_id`, which survives the run's reload of its own
`scenario.yaml` the way `report_group_id` does. A covered step outside any `group`, `use`, or target
group forms a unit of its own.

`Scenario` gains a map under the same key:

```python
# bajutsu/common/scenario/models/scenario/scenario.py
skip_if_unsupported: dict[Literal["erase", "seedPhotos", "permissions"], str] = Field(
    default_factory=dict, alias="skipIfUnsupported"
)
```

Each value is a non-blank reason, validated like the step modifier. An unknown key fails at load.

### The lint

`lint_text` and `lint_diagnostics` (`bajutsu/common/lint.py`) gain a keyword argument:

```python
def lint_text(
    text: str, *, real_device: RealDeviceLint | None = None, source: Path | None = None
) -> list[str]: ...
def lint_diagnostics(
    text: str, *, real_device: RealDeviceLint | None = None, source: Path | None = None
) -> list[Diagnostic]: ...
```

`RealDeviceLint` is keyed by target name. For each target it carries the run's capability set, the
resolved `erase` default, and whether the target is a real device.
`source` is the scenario file's path. The real-device checks expand components relative to it, so
they require `source` whenever `real_device` is given. With
`real_device=None`, the default, both functions behave as today. Given a value, they run three checks
once the grammar passes:

| Check | Finding when |
|---|---|
| Steps | `unsupported` names a step location that no `skipIfUnsupported` modifier covers; the `scenario.permissions` locations are excluded here, since no step modifier can cover a scenario-level field, and are left to the row below |
| Preconditions | `erase`, `seedPhotos`, or a `permissions` service is in effect, and the scenario map does not name it |
| `extract` on a skipped step | A step covered by `skipIfUnsupported` carries `extract` |

The third check exists because a later step may read the variable that `extract` sets. On a real
device the skipped step never sets it, and the later step would fail with an error that names the
wrong cause. `capture` stays allowed: the step records no evidence when skipped, and no later step
depends on evidence.

The real-device checks run on the expanded scenario, the same form the run preflight sees. Today the
lint validates the unexpanded file, and the preflight walk recurses into `if` and `forEach` alone. A
`setLocation` inside a `group:`, or inside a component that `use:` pulls in, would pass the lint and
then fail the real-device run. Checking the expanded form keeps the two verdicts identical.

Every step therefore records where it came from. Each step, expanded or not, carries a private
`source_loc`: the YAML location of its call site in the scenario file. The rules are these:

- A step that came from no expansion records its own position.
- A step inside a component records the `use:` step that pulled the component in.
- A step inside a `group:` records its own position before flattening.
- A child of a target group gets its location inside `_expand_target_groups` itself. That function
  runs in the `Scenario` validator at load time, before `expand.py`.
- A step that already has a `source_loc` keeps it when expansion runs again.

The flattening of target groups shifts every later step index, so `source_loc` is the one location
the lint trusts.

`lint_diagnostics` needs a structured location per finding. `unsupported()` returns readable strings
such as `"step 3 > if > then[0]: setLocation …"`, which the existing YAML node walk cannot read. So
`capability_preflight` gains a variant of `unsupported`, `unsupported_findings`, that yields each
finding with its covering skip unit. The unit is the outermost `skipIfUnsupported` on the step or on
any enclosing `if`, `forEach`, `web`, or `app` step, since an enclosing unit wins. It is `None` for an
uncovered step. Both the step check and the
`extract` check read it, and so does the run preflight below. The lint reads each step's `source_loc`, and the existing walk (`_resolve_line`, `lint.py:154`) maps it to a
line. A finding inside a component thus lands on the `use:` line that pulled the component in.

The command-line interface gains `--config <path>` and `--target <name>` in
`bajutsu/cli/commands/lint.py`. The lint resolves targets the way the run does:

- A scenario that declares its own `targets` needs no `--target`. The lint resolves each declared
  target from the config.
- A scenario that declares none uses the target `--target` names. For such a scenario, `--config`
  without `--target` exits 2 as a usage error rather than linting clean, so a forgotten flag cannot
  hide a finding (`doctor` already requires `--target`, `doctor.py:208`).

The checks follow the run preflight per target (`_preflight_targets`, `pipeline.py:339`). The lint
judges each target on the steps routed to it (`_steps_for_target`), against its own capability set.
The lint never flags a step whose routed target supports it, even when a sibling target lacks the
capability. Preconditions and `permissions` are scenario-level. The runner applies them to every
declared target (`_lease_targets`). The precondition check thus reports a precondition that any
declared target cannot apply. Without `--config`, the lint reports nothing new.

The serve editor's lint (`bajutsu/serve/operations/lint.py`) works on unsaved text, so it needs two
more inputs in the `/api/lint` request body. The first is the editor's selected target name, which the
server resolves against the request's bound config. The second is the scenario file's path, which
anchors component references the way `load_expanded_scenarios` does for a file on disk. The server
resolves that path with the existing `_scenario_path(scenarios_dir, p)` (`bajutsu/serve/helpers.py:549`)
against the bound config's scenarios directory. It passes that directory as the containment `root`,
and rejects a path outside it. Expansion
gains a text entry point for that purpose. With no target named, the editor's lint reports nothing
new.

Scoping the check to the resolved target is deliberate. A number of scenarios in `demos/showcase/`
use device-control steps and target the Simulator alone. The Simulator supports those steps, so they
lint clean with no annotation. A target-blind check would demand one from teams that never touch a
real device.

### Skipping at run time

The run preflight runs the same checks through `capability_preflight.run_preflight`. Both of its
call sites in `bajutsu/common/runner/pipeline.py` do so, per target and for a single target. A
finding that no unit covers fails the scenario before any device work, as today. A covered finding
instead adds its unit to the set the step loop skips. One unsupported step in a unit thus skips
every step in that unit. Two callers ask whether a backend can run a scenario at all, and they keep
calling `unsupported`: `doctor` and actuator selection (`backends.py`).

The step loop skips a step whose skip unit is in that set. It checks once, right before executing
the step, and records the outcome without touching a driver. A covered step whose target supports it
runs as usual. A step that runs and fails still fails, since a capability gap is the one trigger.

`StepOutcome` (`bajutsu/common/orchestrator/types/step_outcome.py`) has no skipped state today. It
carries `ok: bool` and `reason`. It gains `skipped: bool = False`, and `reason` holds the modifier's
text.

A skipped `assert` step removes a check rather than satisfying it. A check that reads state only a
Simulator can set, such as the place a `setLocation` set, is the case this item exists for. That
`assert` skips with its cause by sharing the cause's skip unit. Three rules keep a removed check from
reading as a pass:

- A skipped assertion is never counted as passed. The report lists it as skipped, with its reason.
- A scenario in which the run skipped every `assert` step fails, rather than reporting green with
  nothing checked. The scenario-level `expect` counts as an assertion here, and no skip removes it.
  A scenario in which the run skipped every step fails the same way.
- The lint names each covered `assert` step that a resolved target would skip as an advisory line,
  so the reviewer sees which checks that target's run drops. The advisory never fails the lint.

Each report format renders the skip at the granularity it has:

| Format | Rendering |
|---|---|
| Manifest | Each step record carries `skipped` and its reason |
| HyperText Markup Language (HTML) report | The step row reads as skipped, never as passed, with its reason |
| JUnit | Unchanged verdict: one `<testcase>` per scenario (`junit_xml`, `report/manifest.py:320-335`) |
| Common Test Report Format (CTRF) | The test keeps its status; its step record (`report/ctrf.py`) gets status `skipped` and the reason in `extra` |

JUnit has no step records, so it cannot show a skipped step as a status. Marking the whole
`<testcase>` `<skipped>` would misreport a scenario whose other steps and assertions ran. The scenario
therefore keeps its own verdict. Its `<properties>` gain `bajutsu.skippedSteps` and
`bajutsu.skippedPreconditions`, and `<system-out>` lists each skipped step with its reason. CTRF
already carries one record per step, so a skipped step shows there as a step status. Skipped
preconditions go in the test's `extra` field.

### The install-and-launch preconditions

On a real device, the XCUITest environment's start raises today (`xcuitest_environment.py:337-350`).
The comment there gives the reason: an option that cannot apply fails loudly rather than becoming a
silent no-op. This item keeps that default and adds the opt-out.

| Precondition | Real device, no opt-out | Real device, named in the scenario map |
|---|---|---|
| `preconditions.erase` | Fails at lint and in the run preflight | Skipped, with a notice and the reason in the report |
| `preconditions.seedPhotos` | Fails at lint and in the run preflight | Skipped, with a notice and the reason in the report |
| `permissions` | Fails at lint and in the run preflight | Skipped, with a notice and the reason in the report |
| The target's `appPath` install, and `reinstall` | Skipped, with a notice | Not applicable |

The opt-out exists because skipping a precondition changes what the scenario tests. A real device
keeps app data between runs, so a skipped `erase` lets one run's state reach the next. A skipped
`permissions` leaves each grant to the device's current state, and a permission dialog may appear.
The author who writes the reason accepts that trade-off for the real device alone. The load-time rule
that `seedPhotos` requires `erase: true` (`preconditions.py:38`) stays.

A skipped precondition is not a step, so `StepOutcome.skipped` cannot record it. `RunResult`
(`bajutsu/common/orchestrator/types/run_result.py`) gains `skipped_preconditions: dict[str, str]`,
written to the manifest as `skippedPreconditions` and mapping each name to its reason. The HTML report,
JUnit, and CTRF render the field the same way as skipped steps, never as a change to the verdict.

The `appPath` install needs no opt-out. The scenario cannot name it, since it lives in the target
config. A real-device target in BE-0238 already expects the app to be installed in advance.

`erase_precondition_supported` (`backends.py:247`) keeps returning `False` on a real device. That
function decides whether a crash retry may force an `erase`, which is a separate question.

### Out of scope

- **Android real-device narrowing.** The Android target config has no field that tells an emulator
  from a real device, so its capability set does not narrow on a real device yet. The opt-out
  already applies to whatever an Android target lacks. A later item can add the field and narrow the
  set, with no change to this design.
- **The live WebDriver route of BE-0238.** `xcuitest_live.py:87` rejects `erase` for its own reason.
  This item changes the local `deviceType: device` route alone.
- **A real-device alternative branch.** Running a different step on a real device would extend `if`.
  That extension is broader than an opt-out and belongs in its own item.
- **Steps inside `web:` and `app:` blocks.** The preflight does not walk into these blocks today
  (`capability_preflight.py:93-99`). This item inherits that gap for the Simulator-only classification
  and does not widen the walk. The `extract` check is the one exception, since a `skipIfUnsupported` on a
  `web:` / `app:` step skips the whole block at run time: that check walks `web.steps` / `app.steps` so
  a skipped `extract` inside a block is reported like any other.

## Alternatives considered

| Alternative | Summary | Why not adopted |
|---|---|---|
| Fail regardless of target | Flag every Simulator-only step in every lint unless annotated | Every Simulator-only showcase scenario would need an annotation, from teams that never use a real device |
| Opt-in flag (`--portable`) | Flag Simulator-only steps only under the flag | A forgotten flag means no detection, and the trigger is a real-device target, not a flag |
| YAML comment (`# bajutsu: skip`) | A `# noqa`-style suppression | The parser drops comments, so an edit or `record` round trip loses the comment; it is not a machine-readable declaration |
| Block form `simulatorOnly: {reason, steps}` | A wrapping block like `group` | A new control-flow construct means new codegen, editor, and nesting rules; a per-step modifier meets the need |
| A separate Simulator-only list | A table apart from the capability tokens | The list and the real-device narrowing would drift apart |
| Silence the lint without skipping | Suppress the finding but keep the run-time failure | One scenario still cannot serve both devices |
| Ignore preconditions automatically | Skip `erase`, `seedPhotos`, and `permissions` on a real device with a notice alone | A notice does not change the verdict; the scenario would pass or fail by the state an earlier run left behind |
| Key the opt-out on a real device (`skipOnRealDevice`) | The name this item first proposed | It names a device class, not the reason a step cannot run; the same gap on the live WebDriver route, Android, or the web would need a second name |
| A Boolean flag (`runOnlySupported: true`) | Mark a step to run only where supported | It carries no reason for the reviewer or the report; `false` repeats the default; on a block it reads as "run the supported children", the opposite of the skip-unit rule |
| Allow `extract` on a skipped step | Let the variable stay unset and fail on a later read | The later read fails with an error that names the wrong cause |

The target-blind check becomes worth revisiting once Android can express a real device in config.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add `skipped` to `StepOutcome`; render it per step in the manifest, HTML, and CTRF, and as
  metadata in JUnit, keeping the scenario verdict
- [x] Add the `skipIfUnsupported` step modifier with its load-time rules, including the
  `_no_modifiers_on_use` and `_target_group` exceptions
- [x] Stamp the modifier and one shared skip unit onto expanded steps in `use`, `group`, and
  target-group expansion
- [ ] Record `source_loc` on every step in `_expand_target_groups` and `expand.py` (kept on
  re-expansion), and add a text entry point to expansion
- [ ] Add the scenario-level `skipIfUnsupported` map with its load-time rules
- [x] ~~Extract `real_device_dropped_capabilities()` and share it with `capabilities_for_run`~~
  - Not needed: the checks read each target's own `capabilities_for_run` set, so no separate
    real-device set exists to extract.
- [x] Add the variant of `unsupported` that yields each finding with its covering skip unit
  (`unsupported_findings`, with `run_preflight` splitting failing findings from skipped units)
- [ ] Add the target checks (steps, preconditions, `extract` on a skipped step, device groups) to
  `lint_text` and `lint_diagnostics`, run on the expanded scenario
- [ ] Add `--config` and `--target` to `bajutsu lint`, resolving declared targets and checking each
  target on its routed steps
- [ ] Send the selected target and the file path in the serve editor's `/api/lint` request, and
  resolve the path within the bound scenarios directory
- [x] Exclude covered findings in the run preflight on every target, handing their skip units to
  the step loop
- [x] Skip covered steps in the step loop, and fail a scenario whose every assertion was skipped
- [ ] Skip the named preconditions where the target cannot apply them, and ignore the `appPath`
  install on a real device, each with a notice
- [ ] Add `skippedPreconditions` to `RunResult` and render it in the manifest, HTML, JUnit, and CTRF reports
- [ ] Document both opt-outs and the lint in `docs/` and `docs/ja/`: `dsl-grammar.md`, `scenarios.md`,
  `cli.md`, and `drivers.md`; update `DESIGN.md` and `docs/architecture.md` on the real-device route

Log:

- [#2211](https://github.com/bajutsu-e2e/bajutsu/pull/2211) — Units 1–3, 6, 7, 11, and 12. Renamed the opt-out from `skipOnRealDevice` to
  `skipIfUnsupported`, so it triggers on any capability gap rather than on a real device. A covered
  `group:`, `use:`, or target group now skips as one unit, which keeps an `assert` with the step it
  depends on. The step modifier now skips at run time and renders as skipped in every report. The
  pages `dsl-grammar.md`, `scenarios.md`, and `reporting.md` now document the modifier.

## References

- `bajutsu/common/backends.py:270-323` — `capabilities_for_run` and its real-device narrowing
- `bajutsu/common/capability/capability_preflight.py` — `_REQUIREMENTS` and `unsupported()`
- `bajutsu/common/runner/pipeline.py` — the run preflight call sites (`_preflight_targets` and the single-target path)
- `bajutsu/common/lint.py`, `bajutsu/cli/commands/lint.py` — today's config-free lint
- `bajutsu/common/config/accessors.py:79` — `xcuitest_targets_real_device`
- `bajutsu/common/orchestrator/types/step_outcome.py` — `StepOutcome`
- `xcuitest_environment.py:337-350` (under `bajutsu/common/platform_lifecycle/environments/xcuitest/`)
  — the real-device precondition raises
- `bajutsu/common/scenario/models/steps/step.py`, `steps/_shared.py` — `Step`, its validators, and
  `_MODIFIERS`
