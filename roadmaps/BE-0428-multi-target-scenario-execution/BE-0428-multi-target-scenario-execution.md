**English** · [日本語](BE-0428-multi-target-scenario-execution-ja.md)

# BE-0428 — Multi-target scenario execution (steps interleaved across targets)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0428](BE-0428-multi-target-scenario-execution.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0428") |
| Topic | Scenario authoring features |
| Related | [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md), [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md), [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md), [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md) |
<!-- /BE-METADATA -->

## Introduction

A scenario gains two new fields: a top-level `targets`, naming every [target](../../docs/glossary.md#target-app-device)
the scenario drives, and a per-step `target`, naming which one of them runs that particular step.
With both set, one scenario file acts on one target and checks the result on another, freely
interleaved — tap "like" on an iOS target, assert the count updated on a web target, edit a comment
on the web target, assert it reached the iOS target — as a single deterministic
[`bajutsu run`](../../docs/cli.md) invocation with one pass/fail verdict. Every target named in
`targets` launches before the first step and tears down together after the last one; a step that
omits `target` keeps behaving exactly as it does today, so an existing single-target scenario needs
no change at all.

This item covers `bajutsu run` only. `bajutsu crawl`, `bajutsu record`, and `serve`'s own dispatch UI
each resolve a single target today the same way `run` does, and each would need its own follow-up to
carry `targets`/`target` through; none is in scope here.

## Motivation

`bajutsu run --target <name>` accepts exactly one target
([`bajutsu/run/cli.py:1293`](../../bajutsu/run/cli.py)), and the runner underneath it matches that
constraint at every layer: `run_scenario` takes exactly one `driver: base.Driver`
([`bajutsu/common/orchestrator/loop/_functions.py:572-574`](../../bajutsu/common/orchestrator/loop/_functions.py)),
and the pipeline that calls it launches exactly one driver per scenario run
([`bajutsu/common/runner/pool.py:408`](../../bajutsu/common/runner/pool.py), via
[`launch_driver`](../../bajutsu/common/runner/launch.py)). The pipeline's `_ScenarioRunner` reflects
the same constraint at the type level: it holds one `eff: Effective` field, shared read-only across
every scenario in the run
([`bajutsu/common/runner/pipeline.py:152-165`](../../bajutsu/common/runner/pipeline.py)). A scenario
that needs to touch two different platforms has no way to do it in one run.

[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md) already names
the workaround this forces: "if a team needs both faces, they run the scenario under two targets."
That workaround costs more than the extra invocation. Two separate runs produce two separate
`RunResult`s and two separate reports, so nothing ties a passing app-side run to a passing web-side
run as one verdict — a reviewer has to open both and confirm neither regressed on its own. The two
runs also share no state: `${vars.*}`, populated by a step's `extract` modifier
([`bajutsu/common/scenario/models/steps/step.py:117`](../../bajutsu/common/scenario/models/steps/step.py))
and held in `live_bindings`
([`bajutsu/common/orchestrator/loop/_functions.py:697`](../../bajutsu/common/orchestrator/loop/_functions.py)),
lives only within one `run_scenario` call. A value one target's step captures — the identifier a
"like" action created earlier, say — cannot reach an assertion against the other target at all, so a
team testing that an app-side action produces the correct web-side effect (or the reverse) cannot
name the specific record either side touched; they can only assert that *some* count changed, with
no way to confirm it was the right one.

A team that only needs to confirm the acting target itself called the right endpoint already has a
lighter, existing option: a `request` assertion against that one target's own network traffic. That
option answers a different question. It confirms the acting target *sent* the right call; it says
nothing about whether the other platform's own client received, rendered, or otherwise reflected it
correctly — which is exactly what a team asking to check the app and the web client at once wants
confirmed.

Once this ships, a reviewer can point to two concrete differences from today. First, a scenario file
whose `targets` names an iOS target and a web target runs as one `bajutsu run` invocation and
produces one pass/fail verdict, instead of two independent runs a reviewer has to reconcile by hand.
Second, a value an app-side step captures with `extract` is readable through `${vars.*}` in an
assertion against the web-side target in the same run — closing the gap the paragraph above names,
where today that value cannot cross from one run to the other at all.

## Detailed design

### Declaring participating targets: `targets` and per-step `target`

```yaml
- name: liking a post on the app shows up on the web, and a web comment reaches the app
  targets: [showcase-app, showcase-web]
  steps:
    - target: showcase-app
      tap: { id: post.like }
      extract:
        postId: { sel: { id: post.id } }

    - target: showcase-web
      wait: { for: { id: "post.${vars.postId}.likeCount" }, timeout: 10 }
    - target: showcase-web
      assert:
        - value: { sel: { id: "post.${vars.postId}.likeCount" }, equals: "1" }

    - target: showcase-web
      tap: { id: "post.${vars.postId}.comment.input" }
    - target: showcase-web
      type: { text: "nice!" }

    - target: showcase-app
      wait: { for: { id: "post.${vars.postId}.comment.latest" }, timeout: 10 }
    - target: showcase-app
      assert:
        - value: { sel: { id: "post.${vars.postId}.comment.latest" }, equals: "nice!" }
```

`load_scenario_file` accepts a top-level list of scenarios or a `{description, scenarios}` mapping,
never a bare mapping starting with a scenario's own `name`
([`bajutsu/common/scenario/load.py:48-63`](../../bajutsu/common/scenario/load.py), §6.1) — every
scenario example in this item, this one included, is a one-item list for exactly that reason.

The example above is illustrative — no fixture in this repository shares one product across an iOS
target and a web target the way it depicts. A second example, built entirely from two fixtures this
repository already ships, shows the same mechanics against real, existing scenarios: `showcase-swiftui`
([`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml)) and `web`
([`demos/web/demo.config.yaml`](../../demos/web/demo.config.yaml)) are two genuinely independent apps
with no shared backend, so the value one target's step captures here carries no product meaning on
the other — the point is only to show `targets`, per-step `target`, and `${vars.*}` sharing working
end to end against real ids, not to claim a cross-app product check:

```yaml
- name: favorite a horse on the iOS showcase, then carry what it captured into the web demo
  targets: [showcase-swiftui, web]
  steps:
    - target: showcase-swiftui
      wait: { for: { id: [stable.row.1, stable_row_1] }, timeout: 10 }
    - target: showcase-swiftui
      tap: { id: [stable.row.1, stable_row_1] }

    - target: showcase-swiftui
      wait: { for: { id: [horse.favorite, horse_favorite] }, timeout: 5 }
    - target: showcase-swiftui
      tap: { id: [horse.favorite, horse_favorite] }
      extract:
        favorited: { sel: { id: [horse.favorite.value, horse_favorite_value] } }

    - target: web
      tap: { id: onboarding.start }
    - target: web
      type: { text: "favorited-${vars.favorited}@example.com", into: { id: auth.email } }
    - target: web
      type: { text: "pw", into: { id: auth.password } }
    - target: web
      tap: { id: auth.submit }
    - target: web
      wait: { for: { id: home.title }, timeout: 5 }
    - target: web
      tap: { id: counter.increment }
  expect:
    - target: showcase-swiftui
      value: { sel: { id: [horse.favorite.value, horse_favorite_value] }, equals: "on" }
    - target: web
      value: { sel: { id: counter.value }, equals: "1" }
```

The iOS steps mirror `demos/showcase/scenarios/firstlook.yaml`'s own "favorite a horse" flow (down to
the dotted-and-underscore id pairs BE-0221 already requires for cross-platform ids), skipping only its
middle "still off" assertion, its `preconditions.launchEnv`, and its `capture` modifier for brevity;
the web steps are the opening of `demos/web/scenarios/counter.yaml`'s own onboarding flow. Both
already run today, each in its own single-target scenario file — this example only adds `targets`,
`target`, and the `extract`/`${vars.*}` hop between them.

Running this exact file against this repository as it stands needs one more step this example glosses
over: `showcase-swiftui` and `web` are declared in two separate config files —
[`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml) and
[`demos/web/demo.config.yaml`](../../demos/web/demo.config.yaml) — and the "Declaring participating
targets" rule above resolves every name in `scenario.targets` against the one config `bajutsu run`
loads for that invocation, never several at once; this proposal adds no way to merge two config files
into one. Running this scenario as written needs a config that declares both `showcase-swiftui` and
`web` as `targets.<name>` entries side by side — copying `web`'s block from `demo.config.yaml` into a
copy of `showcase.config.yaml`, or the reverse, is enough, since neither target's own config depends
on being the only one present — before `bajutsu run --scenario` can resolve both from one load. The
example is otherwise unchanged: real ids, a real per-step flow from each of the two existing scenario
files, and no product-level claim about the two apps sharing data.

`Scenario` gains `targets: list[str] = Field(default_factory=list)`, alongside its existing `before`,
`steps`, and `after` fields
([`bajutsu/common/scenario/models/scenario/scenario.py:45-84`](../../bajutsu/common/scenario/models/scenario/scenario.py)).
Each entry names a `targets.<name>` config unit — the same [target](../../docs/glossary.md#target-app-device)
`--target` already resolves — every name must already exist in the loaded config, checked the same
way `--target` is checked today, and repeating the same name twice in `targets` is rejected at load
time rather than silently deduplicated.

`Step` gains `target: str | None = None`, joining the fixed tuple of orthogonal step modifiers —
today `("capture", "extract", "name", "from_")`
([`bajutsu/common/scenario/models/steps/_shared.py:8`](../../bajutsu/common/scenario/models/steps/_shared.py)),
exempted from `Step`'s "exactly one action" rule
([`bajutsu/common/scenario/models/steps/step.py:136-139`](../../bajutsu/common/scenario/models/steps/step.py))
the same way `target` needs to be. The new rule needs a validator of its own: `_exactly_one`
([`bajutsu/common/scenario/models/_base.py:40`](../../bajutsu/common/scenario/models/_base.py)) runs
from inside `Step`'s own `model_validator` and has no access to the enclosing scenario. That
validator, checked once at the `Scenario` level, enforces the new field's meaning against
`scenario.targets`: with zero or one entries, `target` must be omitted, or must name that one entry,
so today's single-target scenario needs no change and behaves exactly as it does now; with two or
more entries, every action step must set `target` explicitly. Requiring it rather than defaulting to "the first declared target" keeps a scenario that
visibly interleaves two platforms from ever leaving a step's destination to an implicit rule a reader
has to memorize. The validator walks `steps`, `before`, and every rule's `steps` in `after`
recursively, into every nested list an `if` or `forEach` step carries — a step three levels deep
inside a `forEach` body is exactly as required to declare `target` as one at the top level. A step
nested inside a `web:` block is the one exception: it carries no `target` of its own at all (rejected
at load time if given one), since it always runs against the `WebContextDriver` bridge the enclosing
`web:` step already opened for its own resolved target — the same way it runs today, unaware that
more than one target exists.

Two points after load can add steps this validator never sees, because both mutate an
already-validated `Scenario` in place rather than building a new one through
`Scenario.model_validate` — and Pydantic does not re-run `model_validator` against a plain attribute
assignment. `expand_components`
([`bajutsu/common/scenario/expand.py:104-114`](../../bajutsu/common/scenario/expand.py)) replaces a
`use` step with its component's own steps by assigning `scenario.steps = expand(...)` (and the same
for `before`, every `after` rule's `steps`, and every `interrupts` entry's `steps`) after `Scenario`
has already loaded and validated; a component whose own steps omit `target` would otherwise satisfy
every other per-step rule while silently evading this item's requirement. `with_lifecycle_phases`
([`bajutsu/common/runner/pipeline.py:959-983`](../../bajutsu/common/runner/pipeline.py)) folds a
target's own config-level `before`/`after` hooks into the scenario via `model_copy`, which Pydantic
likewise never re-validates. This item closes both gaps by extracting the target-required check out
of `Scenario`'s own `model_validator` into a plain function the two mutation points call again on
their own result — once right after `expand_components` finishes a scenario, and once right after
`with_lifecycle_phases` builds its folded copy — raising the same load-time error a scenario file
with a bare targetless step already raises, rather than letting a component or a config-level hook
slip one through silently. `with_lifecycle_phases` itself also needs to become per-target (a
target's own `before`/`after` hooks come from that target's own config, per "Launching every declared
target together" below) and stamps every hook step it folds in with that target's own name before the
re-check runs, so a config-level hook is unambiguous about which target it acts on the same way an
author-written step already must be, and the re-check exists mainly as a defense against a future
caller of either function forgetting to stamp a step it injects.

`if`, `forEach`, and `web` are different in kind: `_CONTROL_FLOW_ACTIONS`
([`bajutsu/common/scenario/models/_base.py:33`](../../bajutsu/common/scenario/models/_base.py)) names
all three, and `_no_modifiers_on_control_flow`
([`bajutsu/common/scenario/models/steps/step.py:141-149`](../../bajutsu/common/scenario/models/steps/step.py))
already exempts them from `capture` and `extract`, since each wraps a nested step list rather than
acting on its own (`name` stays allowed on them). `web` still takes `target` for the ordinary reason
every action step does: which target's driver the block's own WebView bridge opens against. `if` and
`forEach` take it for a different reason — evaluating the condition itself means querying one
target's element tree, confirmed by `ForEach`'s own `sel: Selector` field
([`bajutsu/common/scenario/models/steps/for_each.py:19`](../../bajutsu/common/scenario/models/steps/for_each.py)) —
independently of whatever `target` each nested step underneath sets for itself.

The scenario-level `expect` block needs the same field for the same reason `steps` does: today it
evaluates as one condition-wait poll against a single driver —
`_evaluate_expect(driver, expect, network, clock, ctx=...)`
([`bajutsu/common/orchestrator/loop/_functions.py:157-171`](../../bajutsu/common/orchestrator/loop/_functions.py)),
called from `run_scenario`'s own passing path and from its post-guard-dismissal retry
([`bajutsu/common/orchestrator/loop/_functions.py:748-760`](../../bajutsu/common/orchestrator/loop/_functions.py),
[`:1076`](../../bajutsu/common/orchestrator/loop/_functions.py)) — so `Assertion`
([`bajutsu/common/scenario/models/assertions/assertion.py:23-56`](../../bajutsu/common/scenario/models/assertions/assertion.py))
gains `target: str | None = None`, excluded from `_ASSERTION_KINDS` the same way its existing `from_`
provenance field already is. The `Scenario`-level validator's rule extends to `expect` entries
unchanged: optional (or matching the one declared target) when `scenario.targets` has zero or one
entries, required when it has two or more. `_evaluate_expect` groups `expect` by the target each
entry names, and calls `_poll_asserts`
([`bajutsu/common/orchestrator/loop/_functions.py:115-123`](../../bajutsu/common/orchestrator/loop/_functions.py))
once per referenced target — using that target's own driver and network source from its
`TargetRuntime` — merging the results back into one `expect_results` list in the scenario's own
declared order. `AssertionResult`
([`bajutsu/common/assertions/_common.py:29-38`](../../bajutsu/common/assertions/_common.py)) gains
`target: str = ""` alongside its existing `kind`, mirroring `StepOutcome.target` (below) for the same
reporting reason — inline `assert:` results are already scoped by their own step's `target`, so this
matters only for `expect_results`.

`Assertion` gaining this field once, on the model itself, makes the same `target` key syntactically
legal everywhere an `Assertion` appears, not only in `expect` — the inline `assert:` list `Step`
carries (`assert_: list[Assertion] | None`,
[`bajutsu/common/scenario/models/steps/step.py:92`](../../bajutsu/common/scenario/models/steps/step.py))
and the `condition: Assertion` `If` wraps
([`bajutsu/common/scenario/models/steps/if_.py:20`](../../bajutsu/common/scenario/models/steps/if_.py))
both already run against a target the enclosing step's own `target` field fixes, so a `target` set
inside one of their assertions would either restate that value redundantly or, worse, silently
contradict it. The `Scenario`-level validator adds one more check alongside the `target` rules above:
walking `steps`, `before`, and `after` the same way it already does, it rejects at load time any
`Assertion` reached through an inline `assert:` list or an `if`'s `condition` that sets `target` at
all — only an `Assertion` reached through the top-level `expect` block may set it.

### The command-line interface (CLI): `--target` becomes optional once a scenario declares its own

`--target` stays required, exactly as it is today
([`bajutsu/run/cli.py:1293`](../../bajutsu/run/cli.py)), for a scenario whose own `targets` field is
empty. Once a scenario's `targets` is non-empty, it is self-declaring: `bajutsu run --scenario
<file>` (repeatable — `--scenario` always names individual files, never a directory
[`bajutsu/run/cli.py:135-146`](../../bajutsu/run/cli.py)) resolves every named target from the loaded
config without needing `--target` at all. Omitting `--target` requires at least one `--scenario`,
since a self-declaring scenario has no `targets.<name>.scenarios` directory
([`bajutsu/run/cli.py:149-159`](../../bajutsu/run/cli.py)) to fall back on — that directory-glob
shorthand, which `bajutsu run --target <name>` alone offers, belongs to one target, so a scenario
naming several always names its files explicitly instead. An explicit `--target` passed alongside
such a scenario is checked for membership in `scenario.targets` — matching if it names any one of the
declared targets, mismatching otherwise — rather than ignored, so a stale flag left over from editing
the scenario fails loudly instead of silently selecting a target the file no longer expects.

`--target` is one flag for the whole invocation, but `--scenario` is repeatable, so a batch can name
several files at once — and a legacy file (`targets` empty) has no `target` of its own to fall back
on, the same way it does not today: it needs the invocation's single `--target` to know which one to
run against. Omitting `--target` for a batch that includes even one legacy file therefore leaves that
file with nothing to resolve, so this item rejects such a batch at load time — one legacy file and no
`--target` in the same invocation is an error naming the file and asking for either `--target` or a
`targets` field on it, rather than a silent failure to resolve. A batch that supplies `--target`
alongside a mix of legacy and self-declaring files keeps working exactly as today's single-target
rules already describe: the legacy files use it as they always have, and it is checked for membership
against each self-declaring file's own `scenario.targets` per the mismatch rule above.

Because that directory glob is the only whole-suite shorthand `run` has, and it belongs to one
target, this item makes the rule an enforced check rather than an author discipline kept by directory
layout: the directory-glob path a plain `bajutsu run --target <name>` with no `--scenario` override
takes rejects, at discovery time, any file it globs whose own `targets` field is non-empty, rather
than handing it to the mismatch check above or launching it — a plain error naming the file and
pointing at `--scenario` instead, before the run itself starts. Without this check the outcome would
be bad either way once such a file lands in that directory: if `name` is not one of the file's own
declared targets, the mismatch check rejects it and fails every other scenario in that same batch
alongside it; if `name` does happen to be one of its declared targets, the membership check lets it
through and the batch silently launches every other target the file declares too, well beyond the
single target that invocation named. With the discovery-time rejection in place, a self-declaring
scenario can only ever run through an explicit `--scenario <file>`, so neither bad outcome depends on
where in the tree the file happens to sit — keeping self-declaring scenarios in a directory of their
own remains good practice for a reader mapping the tree, but this item no longer relies on it for
correctness.

This item leaves the web backend's cross-engine matrix (`--browsers`,
[`bajutsu/run/cli.py:264-307`](../../bajutsu/run/cli.py)) and `--headed` / `--browser` untouched: they
keep applying to a run's single web-platform target exactly as today, and a self-declaring scenario
naming more than one web-platform target rejects them outright rather than guessing which one they
mean. Extending the matrix axis to a multi-target run is a follow-up this item does not cover.

### Launching every declared target together, and tearing all of them down together

Resolving one target name into one `Effective` config is already a named step, and it does more than
the bare lookup `resolve(config, target)` performs against `config.targets[target]`
([`bajutsu/common/config/resolve.py:138-160`](../../bajutsu/common/config/resolve.py)): the caller
that reaches it, `_load_effective_with_source`
([`bajutsu/cli/_shared.py:217-285`](../../bajutsu/cli/_shared.py)), finishes by rebasing the result —
`eff.rebased(root)`, or `eff.rebased(cfg_path.resolve().parent, confine=False)` for a local config
([`bajutsu/cli/_shared.py:279-285`](../../bajutsu/cli/_shared.py)) — so that `app_path` / `scenarios` /
`baselines` / `schemas` / `goldens` resolve against the config file's own directory rather than the
caller's working directory
([`bajutsu/common/config/effective/effective.py:98-108`](../../bajutsu/common/config/effective/effective.py),
BE-0242). A second declared target resolved through the bare lookup alone would have its own relative
paths silently resolve against the wrong directory, so this item's per-name resolution reuses the
whole `_load_effective_with_source` chain — the CLI's own `_resolve_config_and_engines`
([`bajutsu/run/cli.py:264-307`](../../bajutsu/run/cli.py)) already calls it this way for the single
`--target` case — once per name in `scenario.targets`, never the bare `resolve()` alone.

`_ScenarioRunner`, which holds one `eff: Effective` field today, is built once per run and shared
read-only across every scenario and every `ThreadPoolExecutor` worker — its own docstring already
states it "holds no per-scenario mutable state" for exactly that reason
([`bajutsu/common/runner/pipeline.py:152-165`](../../bajutsu/common/runner/pipeline.py)). Two
scenarios in the same run can declare different `targets`, so the per-target, already-rebased
`Effective` map cannot live on that shared object the way a single `eff` does today; it stays a local
inside `_run_one_impl`
([`bajutsu/common/runner/pipeline.py:329-331`](../../bajutsu/common/runner/pipeline.py)), the method
that already keeps every other piece of per-scenario state local, per that same docstring.
`_ScenarioRunner` gains one new read-only field instead — the loaded `Config` the resolution chain
above reads from. `run`'s CLI itself does not hold on to one today: `_load_effective_with_source`
discards it after resolving the single `--target`, returning only `(Effective, source,
checkout_root)` ([`bajutsu/cli/_shared.py:263`](../../bajutsu/cli/_shared.py)), and
`_resolve_config_and_engines` passes only that `Effective` on
([`bajutsu/run/cli.py:264-307`](../../bajutsu/run/cli.py)). Both gain the loaded `Config` in their own
return value, threaded from there down through `run_and_report`
([`bajutsu/common/runner/pipeline.py:1176-1178`](../../bajutsu/common/runner/pipeline.py)) and
`run_all` ([`bajutsu/common/runner/pipeline.py:986-987`](../../bajutsu/common/runner/pipeline.py)),
neither of which takes one today (both take only the CLI's single `eff: Effective`). With it,
`_run_one_impl` calls the resolution chain once per name in `scenario.targets`, building its
per-scenario `dict[str, Effective]` locally.

Before any pool exists, one `DeviceLease` is already acquired for the run — a distinct, upstream
concept from the per-scenario `pool.lease()`/`Lease` this item has generalized so far.
`acquire_device(eff, udid)` ([`bajutsu/run/cli.py:1514`](../../bajutsu/run/cli.py)) calls the target's
own `eff.device_provider` ([`bajutsu/common/config/schema/target_config.py:72`](../../bajutsu/common/config/schema/target_config.py),
`targets.<name>.deviceProvider`, BE-0236) — the built-in `local` provider when unset, a real
device-cloud provider otherwise — and returns the `udid_spec` and provisioning data every later step,
including `make_pool`, resolves devices from. This call is unconditional on every run today, and it
reads one target's `eff`; since `deviceProvider` is per-target config, this item calls it once per
declared target instead, before building any pool, and releases each target's own `DeviceLease` at
teardown alongside its driver and pool.

Bringing every declared target's driver up needs its own pool, not one shared pool, because a pool is
platform-specific today: `make_pool` resolves one `pool_actuator`/`pool_env` pair from one platform's
udid list and pre-starts that platform's own collectors
([`bajutsu/common/runner/pool.py:149-182`](../../bajutsu/common/runner/pool.py)) — an iOS target and a
web target, this item's own headline example, need two differently-built pools, not one pool handling
both. `run_all` builds one pool per distinct platform among every declared target across the whole
scenario set instead of the single pool it builds today, still once per run, each fed by that
platform's own targets' `DeviceLease`s from the previous paragraph, and `_run_one_impl` leases from
whichever pool matches each of its scenario's declared targets. It launches one driver per resolved
`Effective` through the existing `launch_driver`
([`bajutsu/common/runner/launch.py:27-110`](../../bajutsu/common/runner/launch.py)) — in place of the
pool's current single per-scenario lease and launch
([`bajutsu/common/runner/pool.py:408`](../../bajutsu/common/runner/pool.py)) — collecting the results
into a local `dict[str, base.Driver]` keyed by target name, all before the first step runs. Every
step that names a target expects it already up, never launched lazily on first reference, per the
interleaving this item is meant to support. `_run_one_impl` derives several things from the single
`self.eff` today: the actuator `select_actuator_for_scenario` picks
([`bajutsu/common/runner/pool.py:260`](../../bajutsu/common/runner/pool.py)), the locale, the
baseline `capture`, `run_defaults.interrupts` composed with the scenario's own `s.interrupts`
([`bajutsu/common/runner/pipeline.py:913`](../../bajutsu/common/runner/pipeline.py)), and the target's
own `launchEnv` passed as `target_launch_env`
([`bajutsu/common/runner/pipeline.py:928-932`](../../bajutsu/common/runner/pipeline.py)). Every one of
these resolves the same way per declared target's own `Effective` instead, keyed alongside its
driver. So do the five more arguments `_run_on_lease` binds from one lease today —
`sink`, `relaunch`, `control`, `webview_bridge`, and `transitions`
([`bajutsu/common/runner/pipeline.py:890-907`](../../bajutsu/common/runner/pipeline.py)) — since each
is scoped to the one lease it came from; a `web:` step on a second declared target needs that
target's own `webview_bridge`, not the first target's, or it would silently open the wrong app's
WebView. So does the alert guard, resolved once per scenario from `self.alert_guard_for(s)`
([`bajutsu/common/runner/pipeline.py:441`](../../bajutsu/common/runner/pipeline.py)), and the network
collector, already built once per launched driver from that one target's own lease
([`bajutsu/common/runner/pool.py:380-428`](../../bajutsu/common/runner/pool.py)). `ctx=EvalContext(...,
golden=gc_with_screen)` ([`bajutsu/common/runner/pipeline.py:875-902`](../../bajutsu/common/runner/pipeline.py))
needs the same generalization for a different reason: `self.baselines_dir` / `self.schemas_dir` /
`self.golden_context` are each derived from the single `self.eff` too, and each names a directory
`Effective.rebased` already resolves per target (per the paths this item's per-target resolution
already carries) — so a `visual` or `golden` assertion on a second declared target must compare
against that target's own baseline and goldens directories, not the primary target's.

Two more fields move the same way, correcting an earlier draft of this design that kept them shared.
`caps`, computed once per run from one target's actuator
([`bajutsu/common/backends.py:194-236`](../../bajutsu/common/backends.py)) and checked once against
the *whole* scenario before any device is leased
([`bajutsu/common/runner/pipeline.py:373`](../../bajutsu/common/runner/pipeline.py), BE-0082's
fail-fast preflight), is a property of one backend's capability set, not of the run — sharing it
would preflight a second declared target's steps against the primary target's capabilities, wrongly
rejecting a construct the second target supports or wrongly allowing one it does not. This item
resolves `caps` per declared target the same way, and runs the preflight once per declared target
against only that target's own steps (grouped by `step.target`, the same grouping this item's
`_evaluate_expect` change already introduces for `expect`) and that target's own capability set.
`mailbox` is not run-level either: it already comes from one target's own config
(`targets.<name>.mailbox`, [`bajutsu/common/config/schema/target_config.py:103`](../../bajutsu/common/config/schema/target_config.py)),
and an `email` / `totp` step reads whichever inbox the *run's* single target configured
([`bajutsu/common/orchestrator/loop/_functions.py:281`](../../bajutsu/common/orchestrator/loop/_functions.py)) —
sharing one mailbox across declared targets would poll the wrong inbox for a step whose target
configures a different one. Both `caps` and `mailbox` join the other per-target fields above in
`TargetRuntime` instead of staying on `_ScenarioRunner`. Only `redactor` stays run-level: it applies
the union of every declared target's own `secrets` to whichever target's evidence it redacts, so a
value one target's config marks secret is scrubbed everywhere rather than only from that one target's
own capture — widening the redaction set is strictly safer than narrowing it, unlike `caps` or
`mailbox`, where sharing one target's own value produces a wrong answer for another.

`with_lifecycle_phases`
([`bajutsu/common/runner/pipeline.py:959-983`](../../bajutsu/common/runner/pipeline.py)) folds one
target's config-level `before` (config-then-scenario order) and `after` (scenario-then-config order)
hooks into a scenario before the run starts, called once per run today from the CLI's single `eff`
(BE-0392). This item calls it once per declared target instead, each with that target's own `eff`, so
each declared target's own `targets.<name>.before`/`after` hooks are the ones folded in for it rather
than the primary target's alone. Merging more than one target's hooks into the scenario's single
`before`/`after` lists needs an explicit order this item adds: every declared target's own `before`
hooks fold in ahead of the scenario's own `before` steps, one target's hooks after another's in
`scenario.targets`' declared order, mirroring today's single "config-then-scenario" order; `after`
mirrors it in reverse, the scenario's own steps' cleanup first and every declared target's own `after`
hooks behind it in the same declared order, mirroring today's "scenario-then-config" order. Each
folded-in hook step is stamped with its own target's name — the fix the previous paragraph's
validator re-check already requires of it — so an app-side `erase` hook and a web-side hook folded
into the same scenario each still run against the right target.

`preconditions` and `permissions` need no such per-target split: both are already scenario-level
fields today (`scenario.preconditions`, `scenario.permissions`), passed once into `launch_driver`
([`bajutsu/common/runner/launch.py:27-98`](../../bajutsu/common/runner/launch.py)), and already
backend-tolerant rather than iOS-specific — `env.start` already interprets an iOS `Preconditions`
field (erase, reinstall) through the simctl lifecycle and a web target's own `env.start` through a
fresh browser context instead, exactly the difference a `--target ios` versus `--target web` run
already exercises today, one backend at a time. This item's per-target `launch_driver` calls pass the
same `scenario.preconditions` and `scenario.permissions` to every declared target's own call, and each
target's own backend keeps interpreting the parts that apply to it and ignoring the rest — not a new
capability this item must invent, since a single-target run already relies on it whenever `--target`
points at a backend some `Preconditions` field doesn't apply to.

Acquiring one lease per declared target needs one more rule this item adds explicitly: `pool.lease()`
blocks on `free.get()` against a queue seeded with the run's udids
([`bajutsu/common/runner/pool.py:163-165, 255`](../../bajutsu/common/runner/pool.py)), so a scenario
that leases N device-backed targets — now drawn from as many as N different per-platform pools, per
the previous paragraphs — while `--workers` runs several scenarios at once can starve or deadlock:
every worker holds one device from one pool and blocks forever acquiring its next one from another.
Acquiring "as one atomic reservation" needs an actual protocol to mean anything, since neither
`pool.lease()` nor a second, independent pool object offers a transaction across the two: this item's
launch step orders every pool a scenario needs by a fixed, deterministic key (the declared platform
name, sorted) and acquires them strictly in that order, every worker included — the standard
lock-ordering discipline that rules out circular wait, the shape every deadlock in this paragraph
takes. A worker that cannot acquire its next pool in the sequence blocks on that one pool's queue
alone, holding only the pools before it in the fixed order; no two workers can then be holding each
other's next pool, since both approach every pool in the same order. A worker releases anything it
already holds if a later pool in its sequence times out, rather than holding a partial set
indefinitely.

Teardown brackets the whole set the same way one launch already brackets one scenario today. A launch
that fails partway through the list tears down every driver that did start before propagating the
failure, mirroring `launch_driver`'s own guard around a readiness failure after `env.start`
([`bajutsu/common/runner/launch.py:100-108`](../../bajutsu/common/runner/launch.py)). The run's own
end tears down every driver the same way `_run_on_lease`'s `finally: lz.release()`
([`bajutsu/common/runner/pipeline.py:955-956`](../../bajutsu/common/runner/pipeline.py)) already tears
down its one lease today, by calling that same release once per declared target's lease.

### Routing a step to its driver, and sharing `${vars.*}` across all of them

`run_scenario`'s existing flat keyword parameters — `driver`, `sink`, `alert_guard`, `network`,
`relaunch`, `control`, `ctx`, `mailbox`, `webview_bridge`, `transitions`, `interrupts`, `locale`,
`capture`, `channel`, and `target_launch_env`
([`bajutsu/common/orchestrator/loop/_functions.py:572-594`](../../bajutsu/common/orchestrator/loop/_functions.py)) —
are, read together, everything the pipeline binds from one target's one lease today (per the previous
section's enumeration). A step that names no `target` keeps reading all of them exactly as it does
now: they stay `run_scenario`'s primary target's runtime, unchanged in shape, so every existing caller
is untouched. `run_scenario` gains one new parameter for every *other* declared target instead of
widening each of these into a per-target mapping in place: `target_runtimes: Mapping[str,
TargetRuntime] | None = None`, where `TargetRuntime` (a new small dataclass) bundles that same list of
fields, plus `caps`: unlike the rest, `caps` never reaches `run_scenario` as one of its own keyword
arguments, since the preflight check that consults it runs in `_run_one_impl` before `run_scenario` is
called at all, but it is resolved per declared target the same way, so it travels alongside the others
on each target's own `TargetRuntime` rather than needing a separate carrier — one instance per
additional declared target, built by `_run_one_impl` the same way
the previous section already builds the driver map. `_StepRunner`'s own methods already take the
active driver as an explicit `active_driver: base.Driver` argument at every call, rather than reading
it off shared state
([`bajutsu/common/orchestrator/loop/_step_runner.py:78-118`](../../bajutsu/common/orchestrator/loop/_step_runner.py)) —
one step kind, a `web:` block, already swaps in a different driver for its own nested steps this way,
building a `WebContextDriver` and recursing into `exec_steps(step.web.steps, web_driver)` before
control returns to the outer `active_driver`
([`bajutsu/common/orchestrator/loop/_step_runner.py:242-293`](../../bajutsu/common/orchestrator/loop/_step_runner.py)).
That swap stays scoped to one already-launched app's in-process WebView bridge, not a second
independently-launched target, but it confirms the loop's primitives already pass per-target state
explicitly per call rather than reading it off one shared object — exactly the shape a `target`-keyed
lookup needs. `_run_one`
([`bajutsu/common/orchestrator/loop/_step_runner.py:89`](../../bajutsu/common/orchestrator/loop/_step_runner.py))
resolves which `TargetRuntime` (the primary one, or an entry from `target_runtimes`) governs a given
step by looking up `step.target`, and dispatches the step's driver through `active_driver` exactly as
today — not the primary target's own `driver` parameter, which would be wrong for a step nested
inside a `web:` block: that step's `active_driver` is already the block's own `WebContextDriver`, and
falling back past it would silently run the step against the wrong app surface. Every top-level
step's `active_driver` starts out as its resolved `TargetRuntime.driver`, so this is a strict
generalization of today's only case, not a behavior change for one.

Sharing `${vars.*}` across targets needs no new plumbing. `live_bindings` is already one plain
`dict[str, str]`, built once per `run_scenario` call and closed over by every phase — `before`,
`steps`, and `after` alike
([`bajutsu/common/orchestrator/loop/_functions.py:695-697`](../../bajutsu/common/orchestrator/loop/_functions.py))
— rather than owned by any one driver. An `extract` step against one target already writes into the
same dictionary an assertion against a different target already reads from; nothing about that
mechanism assumes a single driver, only that a single `run_scenario` call assembles it once, which
still holds.

### Report: naming which target produced each step

`StepOutcome` gains `target: str = ""`, alongside its existing `action: str`
([`bajutsu/common/orchestrator/types/step_outcome.py:15-44`](../../bajutsu/common/orchestrator/types/step_outcome.py)) —
empty for a scenario whose own `targets` is empty, set to the step's declared target name when it
sets one explicitly, and set to that single declared name when `scenario.targets` has exactly one
entry and the step omits `target` (per the validator above) — a report is never blank for a
self-declaring scenario just because its one target needed no per-step disambiguation. This is the
same "empty means not applicable" convention `RunResult.engine` already uses for a single-engine run
([`bajutsu/common/orchestrator/types/run_result.py:26-30`](../../bajutsu/common/orchestrator/types/run_result.py)).

`RunResult`'s own `backend`, `device`, `device_name`, and `device_runtime`
([`bajutsu/common/orchestrator/types/run_result.py:25-40`](../../bajutsu/common/orchestrator/types/run_result.py))
each describe exactly one target, and stay meaningful exactly as they are today for a single-target
run. A multi-target run needs this same information once per declared target instead of once for the
whole scenario, so `RunResult` also gains `target_devices: dict[str, TargetDeviceInfo]` (a new small
dataclass carrying `backend` / `engine` / `device` / `device_name` / `device_runtime`), keyed by
target name. The existing singular fields stay empty for a multi-target run rather than reporting one
declared target's values over the others' — the same "empty means not applicable" convention this
item just applied to `StepOutcome.target` above, kept consistent rather than reversed for `RunResult`
itself. An existing reader compiled against today's `RunResult` shape — the JUnit and Common Test
Report Format (CTRF) exports among them — sees the single-target case exactly as before and an empty
value on a multi-target run, rather than one target's values presented as if they spoke for the whole
scenario. The report's Steps view shows each step's target name beside its action, and a new header
block lists every declared target's device next to its own evidence, alongside the run's existing
single-device header for a scenario that declares none.

### One open question this proposal leaves to implementation

Every other piece of shared state an earlier draft of this design left open resolves elsewhere in
this design, or was confirmed not to need resolving at all. `_ScenarioRunner`'s `run_dir` derives from
`runs_dir / run_id` ([`bajutsu/common/runner/pipeline.py:1223`](../../bajutsu/common/runner/pipeline.py)),
a run-level artifact directory that was never derived from any target's `Effective`, so it needs no
per-target generalization. `udid_spec`, `actuator`, `baselines_dir`, `schemas_dir`, and
`golden_context` are exactly the fields "Launching every declared target together, and tearing all of
them down together" above resolves per declared target, alongside `caps` and `mailbox`. The optional
per-scenario `resolve_actuator` callback (BE-0240,
[`bajutsu/common/runner/pipeline.py:190`](../../bajutsu/common/runner/pipeline.py)) is explicitly out
of scope rather than silently dropped: a caller that passes it already opts out of the pool-based
actuator selection this item generalizes, and extending it to resolve one actuator per declared target
— rather than one for the whole scenario — is deferred to whichever future item first needs
`resolve_actuator` and multi-target scenarios together.

One question remains genuinely open. `gc_with_screen`
([`bajutsu/common/runner/pipeline.py:875-884`](../../bajutsu/common/runner/pipeline.py)) probes one
driver's screen bounds for `golden` frame sanity (BE-0006); an iOS target and a web target generally
report different screen geometries, and only one `GoldenContext` reaches `run_scenario` today, so a
`golden` assertion's behavior on a second declared target is open — per-target golden contexts, or
restricting `golden` to one declared target, are the two shapes this proposal has identified, and
choosing between them is deferred rather than guessed.

### Work breakdown (MECE)

1. **Schema.** `Scenario.targets: list[str]`, rejecting a duplicate name at load time; `Step.target:
   str | None`; `Assertion.target: str | None` (excluded from `_ASSERTION_KINDS` like `from_`); a
   `Scenario`-level validator (not `Step`'s own, which cannot see the enclosing scenario) tying
   `target`'s requirement to `len(scenario.targets)`, walking `steps`, `before`, and every rule in
   `after` recursively into every nested `if`/`forEach` step list — a step nested inside a `web:`
   block is the one exception, required to omit `target` rather than declare one, since it always
   runs against the target the enclosing `web:` step already resolved — applying the same
   required-or-not rule to `expect`, and rejecting `target` outright on any `Assertion` reached
   through an inline `assert:` list or an `if`'s `condition` rather than through `expect`; extracting
   that whole check into a plain function `expand_components` and `with_lifecycle_phases` each call
   again on their own result, since both mutate an already-validated `Scenario` in place and Pydantic
   never re-runs `model_validator` against a plain attribute assignment or a `model_copy`.
2. **CLI.** `--target` becomes optional once `scenario.targets` is non-empty, and `--scenario`
   becomes mandatory in its place; an explicit `--target` is checked against `scenario.targets`
   rather than silently overridden; the scenario-file loader resolves and validates every declared
   name against the loaded config before the run starts; the `--target`-only directory-glob path
   rejects, at discovery time, any globbed file whose own `targets` field is non-empty, rather than
   handing a self-declaring scenario to the mismatch check or launching it; a batch that omits
   `--target` while including a legacy (empty-`targets`) file is rejected at load time, naming the
   file that has nothing to resolve `--target` from.
3. **Launch and teardown.** One `DeviceLease` acquired per declared target before any pool exists,
   released at teardown alongside its driver and pool; `_load_effective_with_source` and
   `_resolve_config_and_engines` returning the loaded `Config` alongside the `Effective` they already
   return; threading it from `run`'s CLI through `run_and_report` and `run_all` into a new read-only
   field on `_ScenarioRunner`, none of which takes one today; `run_all` building one pool per distinct
   platform among every declared target instead of today's single pool, each fed by that platform's
   own targets' `DeviceLease`s; `_run_one_impl` resolving one already-rebased `Effective` per declared
   target name into a local map (never stored on the shared `_ScenarioRunner`); acquiring every pool a
   scenario needs in a fixed, deterministic order (the declared platform name, sorted) rather than one
   blocking `pool.lease()` per target in arbitrary order — the standard lock-ordering discipline, so no
   two workers can hold each other's next pool and deadlock, releasing anything already held if a
   later pool in the sequence times out; one `launch_driver` call per name, collected into a local
   `dict[str, base.Driver]`, all before the first step, passing the scenario's own unchanged
   `preconditions`/`permissions` to every call; `with_lifecycle_phases` called once per declared
   target with that target's own `eff`, stamping each folded-in hook step with its own target's name
   and merging every declared target's hooks into the scenario's `before`/`after` in declared order; a
   launch failure partway through tears down every driver that did start; the run's own end tears down
   the whole set.
4. **Runner.** A new `TargetRuntime` dataclass bundling every argument `run_scenario` already binds
   from one lease (`driver`, `sink`, `alert_guard`, `network`, `relaunch`, `control`, `ctx`,
   `mailbox`, `webview_bridge`, `transitions`, `interrupts`, `locale`, `capture`, `channel`,
   `target_launch_env`), plus `caps`, resolved per target the same way but consulted directly by the
   preflight check in `_run_one_impl` rather than forwarded through `run_scenario`;
   `run_scenario`'s new `target_runtimes: Mapping[str, TargetRuntime] | None`
   parameter, alongside its unchanged flat parameters for the primary target; the step-dispatch
   lookup in `_run_one` that resolves `step.target` against `active_driver`/`target_runtimes` instead
   of the primary target's own values; one `TargetRuntime` built per declared target in
   `_run_one_impl`, the same way today's single set of per-lease arguments is already built once per
   scenario; `_evaluate_expect` grouping `expect` by target and calling `_poll_asserts` once per
   referenced target's own driver and network source, merging the results back in declared order.
5. **Report.** `StepOutcome.target` (including the single-declared-target default);
   `AssertionResult.target` mirroring it for `expect_results` only (an inline `assert:` result stays
   scoped by its own step's `target`); `RunResult`'s singular fields left
   empty on a multi-target run; `RunResult.target_devices`; the Steps view's per-step target label;
   the header block listing every declared target's device.
6. **Docs.** `docs/scenarios.md` (the `targets`/`target` reference and a worked example),
   `docs/cli.md` (`--target`'s new optional condition and `--scenario`'s new mandatory one), and
   `docs/run-loop.md` (the multi-driver step dispatch), and their `docs/ja/` mirrors.
7. **Tests.** Schema: the target-required validator across zero/one/two-or-more `targets`, on
   `steps`/`before`/`after`/`expect` and every nested `if`/`forEach`/`web` shape, duplicate-name
   rejection, the `Assertion.target`-outside-`expect` rejection, and the re-check surviving
   `expand_components` and `with_lifecycle_phases`. CLI: `--target` optional/mandatory switch,
   mismatch rejection, the directory-glob rejection of a self-declaring file, and the mixed
   legacy/self-declaring batch rejection. Launch: a launch failure partway through a multi-target set
   tears down what did start; the lock-ordering acquisition never deadlocks two concurrent workers
   needing the same two platforms in opposite order; per-target `caps` preflight rejects a construct
   only the primary target lacks; per-target `mailbox` selection. Runner: mixed-target step routing
   within one scenario (interleaving, not just two contiguous blocks); `${vars.*}` sharing across
   targets; `expect` grouped and merged back in declared order. Report: a multi-target `RunResult`'s
   singular fields stay empty while `target_devices` is populated, and an existing JUnit/CTRF reader
   still parses a single-target run unchanged.

## Alternatives considered

- **An `actor`/`verifier` role split** (one target performs the scenario's `steps`, a second one runs
  a separate `verify` block afterward) — an earlier shape for this same item. Rejected: it only
  covers one hand-off in one direction per scenario, and cannot express acting on one target,
  verifying on another, then acting on the first target again — the interleaving a real cross-target
  test needs, since a web-side edit reaching the app is exactly as common as an app-side action
  reaching the web.
- **A dedicated `switchTarget` step**, mirroring the `setViewport`/`emulate` step
  [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md) already
  considered and rejected for device-mode switching. Rejected for the same reason: a switch step
  turns routing into an imperative action a reader has to track across the whole step list, rather
  than a property visible on the one step it governs. A `target` modifier keeps that visibility, the
  same way `capture` and `extract` already keep their own effect visible on the step they modify
  rather than as a preceding command.
- **Network-only cross-target assertions** (no second live driver at all; only a `request` assertion
  against the acting target, confirming it called the other platform's application programming
  interface (API) with the right body) —
  already possible today, and kept as the lighter option for a team that only needs it. Rejected as
  the sole mechanism because it answers a different question from the one this item's motivation
  names: it confirms the acting target sent the right call, not that the other platform's own client
  received and rendered it correctly, which is what "check the result on the other platform" means.
- **Two chained scenario files**, with a new runner flag piping one file's captured `${vars.*}` into
  a second file run against a different target. Rejected: splitting one logical test across two files
  duplicates `preconditions`, `before`, and `after` across both, and cannot interleave — the first
  file finishes entirely before the second one starts, so a test needing app → web → app again would
  still need a third file.
- **A repeatable `--target`** (`list[str]`, mirroring `--scenario`'s own existing repeatable-option
  shape, [`bajutsu/run/cli.py:1294-1303`](../../bajutsu/run/cli.py)) instead of a scenario-level
  `targets` field. Rejected: which platforms a scenario interleaves is a property of the scenario
  itself, not a per-invocation choice — a CI job invoking `bajutsu run --scenario a.yaml b.yaml ...`
  over a whole suite of files would otherwise need its `--target` list kept in lockstep with whichever
  file in that invocation happens to need which targets, drifting out of sync the moment one file's
  `targets` changes and the invocation's flags do not.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Schema: `Scenario.targets`, `Step.target`, `Assertion.target`, and the validator tying
      `target`'s requirement to `len(scenario.targets)` across `steps` and `expect` alike.
- [ ] CLI: `--target` optional under a self-declaring scenario; mismatch rejection; config
      validation for every declared name.
- [ ] Launch and teardown: `Config` threaded through to `_ScenarioRunner`; one pool per declared
      platform; one driver per declared target, launched together and torn down together.
- [ ] Runner: the `TargetRuntime` bundle, `run_scenario`'s `target_runtimes` mapping, the per-target
      actuator/locale/capture/interrupts/guard/network/evidence-context construction, and
      `_evaluate_expect`'s per-target grouping.
- [ ] Report: `StepOutcome.target`, `AssertionResult.target`, `RunResult.target_devices`, and the
      report views that show them.
- [ ] Docs: `docs/scenarios.md`, `docs/cli.md`, `docs/run-loop.md`, and their `docs/ja/` mirrors.
- [ ] Tests: schema validator (including the `expand_components`/`with_lifecycle_phases` re-check),
      CLI selection and rejection rules, multi-pool lease/deadlock coverage, per-target
      caps/mailbox/preflight, mixed-target step routing, and report backward compatibility.

## References

- [`docs/scenarios.md`](../../docs/scenarios.md) — the scenario file format this item extends.
- [`docs/run-loop.md`](../../docs/run-loop.md) — `run_scenario`'s single-driver step loop this item
  generalizes.
- [`docs/glossary.md#target-app-device`](../../docs/glossary.md#target-app-device) — what a target is,
  and how it differs from the app and the device it drives.
- [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) — the
  backend-agnostic core this item's multi-driver run still depends on: each target keeps driving
  through the same `Driver` interface, one instance per declared target instead of one for the whole
  run.
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md) — the
  `before`/`after` lifecycle phases this item's per-step `target` also applies to.
- [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md) —
  the `${vars.*}` mechanism this item shares across targets unchanged.
- [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md) — names
  today's workaround ("run the scenario under two targets") this item replaces with one run, and the
  rejected `switchTarget`-style step this item's design avoids repeating.
