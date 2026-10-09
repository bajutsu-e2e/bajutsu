**English** · [日本語](BE-XXXX-real-device-step-lint-ja.md)

# BE-XXXX — Lint Simulator-only steps against a real-device target, with an explicit skipOnRealDevice opt-out

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-real-device-step-lint.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md), [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md) |
<!-- /BE-METADATA -->

## Introduction

A scenario written against the iOS Simulator often holds steps that cannot run on a real iPhone.
`setLocation`, `push`, and the clipboard steps all drive `simctl`, which reaches the Simulator alone.
Today `bajutsu lint` cannot see that gap, because it never reads the target config.

This item makes `bajutsu lint` report each Simulator-only [step](../../docs/glossary.md#scenario-authoring)
and precondition. The check fires when the selected [target](../../docs/glossary.md#target-app-device)
is a real device. The explicit opt-out is `skipOnRealDevice`, always with a written reason:

- As a step modifier, `skipOnRealDevice: "<reason>"`, the runner skips the step on a real device.
- As a scenario-level map, `skipOnRealDevice: { erase: "<reason>" }`, the runner skips the named
  [precondition](../../docs/glossary.md#scenario-authoring). The map takes `erase`, `seedPhotos`,
  and `permissions`.

Anything Simulator-only that carries no opt-out fails the lint, and fails a real-device run before any
device work. The target's `appPath` install is the one exception: it lives in the target config, not
the scenario, so Bajutsu ignores it on a real device with a notice.

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
path. No device is leased first. Next, add `skipOnRealDevice` where the author accepts the gap. The
scenario then lints clean and runs in full on the Simulator. On the real device it runs too, and the
report lists each skipped step and precondition with its reason.

## Detailed design

The design has five parts: the classification, the two opt-outs, the lint, the run-time skip, and the
install-and-launch preconditions. Nothing here adds a model call or a fixed pause. Per-app differences
stay in the target config.

### Which steps and preconditions are Simulator-only

This item keeps no second list of Simulator-only steps. The single source of truth is one capability
set: the one `capabilities_for_run` drops for a real device (`backends.py:236-243`). A new function,
`real_device_dropped_capabilities()`, returns that set. Both `capabilities_for_run` and the lint call
the new function. A token added to the real-device narrowing thus changes the lint in the same commit.

The table that maps a step to its capability already exists. `_REQUIREMENTS` in
`bajutsu/common/capability/capability_preflight.py` pairs each device-control step with its BE-0212
token. The same table already maps each `permissions` service to its token, at the location
`scenario.permissions`. The lint calls a structured variant of `capability_preflight.unsupported` (see
*The lint*). It keeps the findings whose capability is in `real_device_dropped_capabilities()`.

`erase` and `seedPhotos` carry no capability token. The lint checks them directly. `erase` resolves
the same way `run` resolves it: the scenario's own value, else the target config's `erase`.

### The two `skipOnRealDevice` opt-outs

`Step` gains one modifier at the same level as `name` and `capture`:

```python
# bajutsu/common/scenario/models/steps/step.py
skip_on_real_device: str | None = Field(default=None, alias="skipOnRealDevice")
```

| Rule | Behavior |
|---|---|
| Value | A non-blank reason string, validated like `Sleep.reason`; an empty or blank value fails at load |
| Exactly-one-action check | `_MODIFIERS` in `steps/_shared.py` gains the field, so the check does not count it as an action |
| On `if`, `forEach`, `web`, or `app` | Covers every nested step |
| On `use` or `group` | `expand.py` copies the modifier onto each expanded step; `_no_modifiers_on_use` gains it as a second exception beside `target` (BE-0446) |
| On a target group (`steps:` with `target`) | `_target_group` accepts it; `_expand_target_groups` (`models/scenario/_targets.py`) and the component path in `expand.py` stamp it onto each nested step |
| On `manual` or `setPrimaryTarget` | Rejected at load |
| On an `if`, `forEach`, `web`, or `app` block holding a `manual` step | Rejected at load; on `use` or `group`, the copied modifier reaches the nested `manual` at expansion and is rejected there |

The two rejections have different reasons. A `manual` step exists to stop a run where a human took
over. Skipping it would hide that stop. A `setPrimaryTarget` step changes how later steps resolve their
target, so skipping it would change the meaning of the rest of the scenario. `_targets.py:320`
already confines `setPrimaryTarget` to top-level steps, so no block can cover one. A `manual` step has
no such limit, which is why a covering block is rejected too. Otherwise the block would bypass the
per-step rule and turn the loud stop into a silent pass.

`Scenario` gains a map under the same key:

```python
# bajutsu/common/scenario/models/scenario/scenario.py
skip_on_real_device: dict[Literal["erase", "seedPhotos", "permissions"], str] = Field(
    default_factory=dict, alias="skipOnRealDevice"
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
| Steps | `unsupported` names a step location that no `skipOnRealDevice` modifier covers |
| Preconditions | `erase`, `seedPhotos`, or a `permissions` service is in effect, and the scenario map does not name it |
| `extract` on a skipped step | A step covered by `skipOnRealDevice` carries `extract` |

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
`capability_preflight` gains a variant of `unsupported` that yields `(step, covering_reason, reason)`
triples. `covering_reason` is the nearest `skipOnRealDevice` on the step or on any enclosing `if`,
`forEach`, `web`, or `app` step, and is `None` for an uncovered step. Both the step check and the
`extract` check read it, and so does the run preflight below. The lint reads each step's `source_loc`, and the existing walk (`_resolve_line`, `lint.py:154`) maps it to a
line. A finding inside a component thus lands on the `use:` line that pulled the component in.

The command-line interface gains `--config <path>` and `--target <name>` in
`bajutsu/cli/commands/lint.py`. The lint resolves targets the way the run does:

- A scenario that declares its own `targets` needs no `--target`. The lint resolves each declared
  target from the config.
- A scenario that declares none uses the target `--target` names.

A target counts as a real device when `xcuitest_targets_real_device(eff)` holds for it
(`bajutsu/common/config/accessors.py:79`). The checks then follow the run preflight per target
(`_preflight_targets`, `pipeline.py:336-357`). Each target is judged on the steps routed to it
(`_steps_for_target`) against its own capability set. A step routed to a Simulator target is never
flagged, even when a sibling target is a real device. Preconditions and `permissions` are
scenario-level, and the runner applies them to every declared target (`_lease_targets`). The
precondition check therefore fires when any declared target is a real device. When no resolved target
is a real device, the lint reports nothing new.

The serve editor's lint (`bajutsu/serve/operations/lint.py`) works on unsaved text, so it needs two
more inputs in the `/api/lint` request body. The first is the editor's selected target name, which the
server resolves against the request's bound config. The second is the scenario file's path, which
anchors component references the way `load_expanded_scenarios` does for a file on disk. The server
resolves that path with the existing `_scenario_path(scenarios_dir, p)` (`bajutsu/serve/helpers.py:549`)
against the bound config's scenarios directory. It passes that directory as the containment `root`,
and rejects a path outside it. Expansion
gains a text entry point for that purpose. With no target named, the editor's lint reports nothing
new.

Scoping the check to a real-device target is deliberate. Several scenarios in `demos/showcase/` use
device-control steps and target the Simulator alone. A target-blind check would demand an annotation
on each of them from teams that never touch a real device.

### Skipping at run time

The run preflight (`bajutsu/common/runner/pipeline.py:356` and `:539`) runs the same three checks on a
real-device target. It excludes covered locations there, and only there. Every other caller of
`unsupported` keeps today's behavior: a Simulator run, other backends, `doctor`, and actuator selection
(`backends.py:402`). A covered `setLocation` thus still fails fast on an Android target, where nothing
would skip it.

The step loop skips a step that carries `skipOnRealDevice` when the step's routed target is a real
device. It checks once, right before executing the step. A step routed to a Simulator target runs as
usual, even in a scenario whose other targets are real devices.

`StepOutcome` (`bajutsu/common/orchestrator/types/step_outcome.py`) has no skipped state today. It
carries `ok: bool` and `reason`. It gains `skipped: bool = False`, and `reason` holds the modifier's
text.

A skipped `assert` step removes a check rather than satisfying it. The item still allows the opt-out
on an `assert`: a check that reads Simulator-only state, such as the place a `setLocation` set, is the
case this item exists for. Three rules keep a removed check from reading as a pass:

- A skipped assertion is never counted as passed. The report lists it as skipped, with its reason.
- A scenario in which every assertion is skipped on a real device fails, rather than reporting green
  with nothing checked. The scenario-level `expect` counts as an assertion here.
- The lint names each covered `assert` step on a real-device target as an advisory line, so the
  reviewer sees which checks a real-device run drops. The advisory never fails the lint.

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

- **Android real devices.** The Android target config has no field that tells an emulator from a
  real device. The lint thus has nothing to key on. A later item can add the field and reuse this design.
- **The live WebDriver route of BE-0238.** `xcuitest_live.py:87` rejects `erase` for its own reason.
  This item changes the local `deviceType: device` route alone.
- **A real-device alternative branch.** Running a different step on a real device would extend `if`.
  That extension is broader than an opt-out and belongs in its own item.
- **Steps inside `web:` and `app:` blocks.** The preflight does not walk into these blocks today
  (`capability_preflight.py:93-99`). This item inherits that gap and does not widen the walk.

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
| Allow `extract` on a skipped step | Let the variable stay unset and fail on a later read | The later read fails with an error that names the wrong cause |

The target-blind check becomes worth revisiting once Android can express a real device in config.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add `skipped` to `StepOutcome`; render it per step in the manifest, HTML, and CTRF, and as
  metadata in JUnit, keeping the scenario verdict
- [ ] Add the `skipOnRealDevice` step modifier with its load-time rules, including the
  `_no_modifiers_on_use` and `_target_group` exceptions
- [ ] Copy the modifier onto expanded steps in `use`, `group`, and target-group expansion
- [ ] Record `source_loc` on every step in `_expand_target_groups` and `expand.py` (kept on
  re-expansion), and add a text entry point to expansion
- [ ] Add the scenario-level `skipOnRealDevice` map with its load-time rules
- [ ] Extract `real_device_dropped_capabilities()` and share it with `capabilities_for_run`
- [ ] Add the variant of `unsupported` that yields `(step, covering_reason, reason)` triples
- [ ] Add the real-device checks (steps, preconditions, `extract` on a skipped step) to `lint_text` and
  `lint_diagnostics`, run on the expanded scenario
- [ ] Add `--config` and `--target` to `bajutsu lint`, resolving declared targets and checking each
  target on its routed steps
- [ ] Send the selected target and the file path in the serve editor's `/api/lint` request, and
  resolve the path within the bound scenarios directory
- [ ] Run the real-device checks in the run preflight on a real-device target alone
- [ ] Skip covered steps in the step loop on a real-device target, and fail a scenario whose every
  assertion was skipped
- [ ] Skip the named preconditions on a real device, and ignore the `appPath` install, each with a notice
- [ ] Add `skippedPreconditions` to `RunResult` and render it in the manifest, HTML, JUnit, and CTRF reports
- [ ] Document both opt-outs and the lint in `docs/` and `docs/ja/`: `dsl-grammar.md`, `scenarios.md`,
  `cli.md`, and `drivers.md`; update `DESIGN.md` and `docs/architecture.md` on the real-device route

## References

- `bajutsu/common/backends.py:194-243` — `capabilities_for_run` and its real-device narrowing
- `bajutsu/common/capability/capability_preflight.py` — `_REQUIREMENTS` and `unsupported()`
- `bajutsu/common/runner/pipeline.py:356,539` — the run preflight call sites
- `bajutsu/common/lint.py`, `bajutsu/cli/commands/lint.py` — today's config-free lint
- `bajutsu/common/config/accessors.py:79` — `xcuitest_targets_real_device`
- `bajutsu/common/orchestrator/types/step_outcome.py` — `StepOutcome`
- `xcuitest_environment.py:337-350` (under `bajutsu/common/platform_lifecycle/environments/xcuitest/`)
  — the real-device precondition raises
- `bajutsu/common/scenario/models/steps/step.py`, `steps/_shared.py` — `Step`, its validators, and
  `_MODIFIERS`
