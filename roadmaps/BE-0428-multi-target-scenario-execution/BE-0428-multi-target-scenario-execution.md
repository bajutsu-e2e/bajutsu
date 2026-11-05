**English** · [日本語](BE-0428-multi-target-scenario-execution-ja.md)

# BE-0428 — Multi-target scenario execution (steps interleaved across targets)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0428](BE-0428-multi-target-scenario-execution.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0428") |
| Implementing PR | [#2025](https://github.com/bajutsu-e2e/bajutsu/pull/2025) (unit 1), [#2028](https://github.com/bajutsu-e2e/bajutsu/pull/2028) (units 2-7) |
| Topic | Scenario authoring features |
| Related | [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md), [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md), [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md), [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md) |
<!-- /BE-METADATA -->

## Introduction

A scenario gains two new fields. A top-level `targets` names every [target](../../docs/glossary.md#target-app-device) the scenario drives. A per-step `target` names which one of them runs that particular step.

With both set, one scenario file acts on one target and checks another, freely interleaved. A scenario can tap "like" on an iOS target and assert that the count updated on a web target. It can then edit a comment on the web target and assert that the comment reached the iOS target. The whole sequence is a single deterministic [`bajutsu run`](../../docs/cli.md) invocation with one pass/fail verdict.

Every target in `targets` launches before the first step and tears down together after the last. A step that omits `target` keeps behaving precisely as it does today, so an existing single-target scenario needs no change at all.

This item covers `bajutsu run` only. `bajutsu crawl`, `bajutsu record`, and `serve`'s own dispatch UI each resolve a single target today, the same way `run` does. Each would need its own follow-up to carry `targets`/`target` through, and none is in scope here.

## Motivation

`bajutsu run --target <name>` accepts precisely one target. See [`bajutsu/run/cli.py:1293`](../../bajutsu/run/cli.py). The runner underneath matches that constraint at every layer:

- `run_scenario` takes precisely one `driver: base.Driver`. See [`bajutsu/common/orchestrator/loop/_functions.py:572-574`](../../bajutsu/common/orchestrator/loop/_functions.py).
- The pipeline that calls it launches precisely one driver per scenario run
  ([`bajutsu/common/runner/pool.py:408`](../../bajutsu/common/runner/pool.py), via
  [`launch_driver`](../../bajutsu/common/runner/launch.py)).
- The pipeline's `_ScenarioRunner` reflects the same constraint at the type level. It holds one `eff: Effective` field, shared read-only across every scenario in the run. See [`bajutsu/common/runner/pipeline.py:152-165`](../../bajutsu/common/runner/pipeline.py).

A scenario that needs to touch two different platforms has no way to do it in one run.

[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md) already names the workaround this forces: "if a team needs both faces, they run the scenario under two targets." That workaround costs more than the extra invocation, for two reasons.

- **No shared verdict.** Two separate runs produce two separate `RunResult`s and two separate reports. Nothing ties a passing app-side run to a passing web-side run as one verdict. A reviewer has to open both and confirm that neither regressed on its own.
- **No shared state.** `${vars.*}` is populated by a step's `extract` modifier ([`bajutsu/common/scenario/models/steps/step.py:117`](../../bajutsu/common/scenario/models/steps/step.py)) and held in `live_bindings`. See [`bajutsu/common/orchestrator/loop/_functions.py:697`](../../bajutsu/common/orchestrator/loop/_functions.py). It lives purely within one `run_scenario` call. A value that one target's step captures, such as the identifier a "like" action created earlier, cannot reach an assertion against the other target at all. A team testing that an app-side action produces the correct web-side effect (or the reverse) cannot name the specific record either side touched. The team can merely assert that *some* count changed, with no way to confirm it was the right one.

A team that only needs to confirm the acting target itself called the right endpoint already has a lighter, existing option: a `request` assertion against that one target's own network traffic. That option answers a different question. It confirms that the acting target *sent* the right call. It says nothing about whether the other platform's own client received, rendered, or otherwise reflected the call correctly. A team asking to check the app and the web client at once wants precisely that confirmed.

Once this ships, a reviewer can point to two concrete differences from today.

1. A scenario file whose `targets` names an iOS target and a web target runs as one `bajutsu run` invocation and produces one pass/fail verdict. Today the same check takes two independent runs that a reviewer reconciles by hand.
2. A value that an app-side step captures with `extract` is readable through `${vars.*}` in an assertion against the web-side target in the same run. Today that value cannot cross from one run to the other at all.

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

`load_scenario_file` accepts a top-level list of scenarios or a `{description, scenarios}` mapping. It never accepts a bare mapping that starts with a scenario's own `name` ([`bajutsu/common/scenario/load.py:48-63`](../../bajutsu/common/scenario/load.py), §6.1). For that reason, every scenario example in this item is a one-item list.

The example above is illustrative. No fixture in this repository shares one product across an iOS target and a web target.

A second example is built entirely from two fixtures this repository already ships. It shows the same mechanics against real, existing scenarios. `showcase-swiftui` ([`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml)) and `web` ([`demos/web/demo.config.yaml`](../../demos/web/demo.config.yaml)) are two genuinely independent apps with no shared backend. The value that one target's step captures thus carries no product meaning on the other. The example shows `targets`, per-step `target`, and `${vars.*}` sharing end to end on real ids. It does not claim a cross-app product check.

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

The iOS steps mirror `demos/showcase/scenarios/firstlook.yaml`'s own "favorite a horse" flow, down to the dotted-and-underscore id pairs that BE-0221 already requires for cross-platform ids. For brevity, the example skips its middle "still off" assertion, its `preconditions.launchEnv`, and its `capture` modifier. The web steps are the opening of `demos/web/scenarios/counter.yaml`'s own onboarding flow.

Both flows already run today, each in its own single-target scenario file. This example merely adds `targets`, `target`, and the `extract`/`${vars.*}` hop between them.

Running this exact file against this repository as it stands needs one more step, which the example glosses over. `showcase-swiftui` and `web` are declared in two separate config files:
[`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml) and
[`demos/web/demo.config.yaml`](../../demos/web/demo.config.yaml). The "Declaring participating targets" rule above resolves every name in `scenario.targets` against the one config that `bajutsu run` loads for that invocation, never multiple at once. This proposal adds no way to merge two config files into one.

Running the scenario as written thus needs a config that declares both `showcase-swiftui` and `web` as `targets.<name>` entries side by side. Copying `web`'s block from `demo.config.yaml` into a copy of `showcase.config.yaml`, or the reverse, is enough. Neither target's own config depends on being the sole target present. With such a config, `bajutsu run --scenario` can resolve both targets from one load.

The example is otherwise unchanged: real ids, a real per-step flow from each of the two existing scenario files, and no product-level claim about the two apps sharing data.

`Scenario` gains `targets: list[str] = Field(default_factory=list)`, alongside its existing `before`, `steps`, and `after` fields. See [`bajutsu/common/scenario/models/scenario/scenario.py:45-84`](../../bajutsu/common/scenario/models/scenario/scenario.py). Each entry names a `targets.<name>` config unit, the same [target](../../docs/glossary.md#target-app-device) that `--target` already resolves. Two rules apply to the entries:

- Every name must already exist in the loaded config. The check works the same way that `--target` is checked today.
- Repeating the same name twice in `targets` is rejected at load time rather than silently deduplicated.

`Step` gains `target: str | None = None`. The field joins the fixed tuple of orthogonal step modifiers, which today is `("capture", "extract", "name", "from_")`. See [`bajutsu/common/scenario/models/steps/_shared.py:8`](../../bajutsu/common/scenario/models/steps/_shared.py). Those modifiers are exempted from `Step`'s "exactly one action" rule
([`bajutsu/common/scenario/models/steps/step.py:136-139`](../../bajutsu/common/scenario/models/steps/step.py)), and `target` needs the same exemption.

The new rule needs a validator of its own. `_exactly_one`
([`bajutsu/common/scenario/models/_base.py:40`](../../bajutsu/common/scenario/models/_base.py)) runs from inside `Step`'s own `model_validator` and has no access to the enclosing scenario. The new validator is checked once at the `Scenario` level. It enforces the new field's meaning against `scenario.targets`:

- With zero or one entries, `target` must be omitted or must name that one entry. Today's single-target scenario thus needs no change and behaves precisely as it does now.
- With two or more entries, every action step must set `target` explicitly. Requiring it, rather than defaulting to "the first declared target", keeps a scenario that visibly interleaves two platforms from leaving a step's destination to an implicit rule that a reader has to memorize.

The validator walks `steps`, `before`, and every rule's `steps` in `after` recursively, into every nested list that an `if` or `forEach` step carries. A step three levels deep inside a `forEach` body must declare `target` precisely as a step at the top level must.

A step nested inside a `web:` block is the one exception. It carries no `target` of its own at all, and a scenario that gives it one is rejected at load time. The step always runs against the `WebContextDriver` bridge that the enclosing `web:` step already opened for its own resolved target. It runs the same way it does today, unaware that more than one target exists.

Two points after load can add steps that this validator never sees. Both mutate an already-validated `Scenario` in place rather than building a new one through `Scenario.model_validate`, and Pydantic does not re-run `model_validator` against a plain attribute assignment.

- `expand_components` ([`bajutsu/common/scenario/expand.py:104-114`](../../bajutsu/common/scenario/expand.py)) replaces a `use` step with its component's own steps. It assigns `scenario.steps = expand(...)`, and does the same for `before`, every `after` rule's `steps`, and every `interrupts` entry's `steps`. The assignment happens after `Scenario` has already loaded and validated. A component whose own steps omit `target` would satisfy every other per-step rule while silently evading this item's requirement.
- `with_lifecycle_phases` ([`bajutsu/common/runner/pipeline.py:959-983`](../../bajutsu/common/runner/pipeline.py)) folds a target's own config-level `before`/`after` hooks into the scenario via `model_copy`. Pydantic likewise never re-validates that copy.

This item closes both gaps by extracting the target-required check out of `Scenario`'s own `model_validator` into a plain function. The two mutation points call it again on their own result, once right after `expand_components` finishes a scenario and once right after `with_lifecycle_phases` builds its folded copy. The re-check raises the same load-time error that a scenario file with a bare targetless step already raises. A component or a config-level hook can no longer slip a targetless step through silently.

`with_lifecycle_phases` itself also becomes per-target, because a target's own `before`/`after` hooks come from that target's own config (see "Launching every declared target together" below). It stamps every hook step it folds in with that target's own name before the re-check runs. A config-level hook is thus unambiguous about which target it acts on, the same way an author-written step already must be. The re-check exists mainly as a defense against a future caller of either function forgetting to stamp a step it injects.

`if`, `forEach`, and `web` are different in kind. `_CONTROL_FLOW_ACTIONS`
([`bajutsu/common/scenario/models/_base.py:33`](../../bajutsu/common/scenario/models/_base.py)) names all three. `_no_modifiers_on_control_flow`
([`bajutsu/common/scenario/models/steps/step.py:141-149`](../../bajutsu/common/scenario/models/steps/step.py)) already exempts them from `capture` and `extract`, because each wraps a nested step list rather than acting on its own. (`name` stays allowed on them.)

The three take `target` for two different reasons:

- `web` takes `target` for the ordinary reason every action step does: which target's driver the block's own WebView bridge opens against.
- `if` and `forEach` take it because evaluating the condition itself means querying one target's element tree. `ForEach`'s own `sel: Selector` field confirms this. See [`bajutsu/common/scenario/models/steps/for_each.py:19`](../../bajutsu/common/scenario/models/steps/for_each.py). The query is independent of whatever `target` each nested step underneath sets for itself.

The scenario-level `expect` block needs the same field, for the same reason `steps` does. Today `expect` evaluates as one condition-wait poll against a single driver:
`_evaluate_expect(driver, expect, network, clock, ctx=...)`. See [`bajutsu/common/orchestrator/loop/_functions.py:157-171`](../../bajutsu/common/orchestrator/loop/_functions.py). `run_scenario`'s own passing path calls it, and so does its post-guard-dismissal retry
([`bajutsu/common/orchestrator/loop/_functions.py:748-760`](../../bajutsu/common/orchestrator/loop/_functions.py),
[`:1076`](../../bajutsu/common/orchestrator/loop/_functions.py)).

`Assertion`
([`bajutsu/common/scenario/models/assertions/assertion.py:23-56`](../../bajutsu/common/scenario/models/assertions/assertion.py))
thus gains `target: str | None = None`. It is excluded from `_ASSERTION_KINDS` the same way its existing `from_` provenance field already is. The `Scenario`-level validator's rule extends to `expect` entries unchanged: `target` is optional (or must match the one declared target) when `scenario.targets` has zero or one entries, and required when it has two or more.

`_evaluate_expect` groups `expect` by the target each entry names. It calls `_poll_asserts`
([`bajutsu/common/orchestrator/loop/_functions.py:115-123`](../../bajutsu/common/orchestrator/loop/_functions.py))
once per referenced target, using that target's own driver and network source from its `TargetRuntime`. It then merges the results back into one `expect_results` list in the scenario's own declared order.

`AssertionResult`
([`bajutsu/common/assertions/_common.py:29-38`](../../bajutsu/common/assertions/_common.py)) gains `target: str = ""` alongside its existing `kind`. It mirrors `StepOutcome.target` (below) for the same reporting reason. Inline `assert:` results are already scoped by their own step's `target`, so the field matters only for `expect_results`.

`Assertion` gains this field once, on the model itself. That makes the `target` key legal everywhere an `Assertion` appears, not merely in `expect`. Two other places hold an `Assertion`:

- the inline `assert:` list that `Step` carries, declared as `assert_: list[Assertion] | None`
  in [`bajutsu/common/scenario/models/steps/step.py:92`](../../bajutsu/common/scenario/models/steps/step.py)
- the `condition: Assertion` that `If` wraps, declared in
  [`bajutsu/common/scenario/models/steps/if_.py:20`](../../bajutsu/common/scenario/models/steps/if_.py)

Both already run against a target that the enclosing step's own `target` field fixes. A `target` set inside one of their assertions would either restate that value redundantly or, worse, silently contradict it.

The `Scenario`-level validator thus adds one more check alongside the `target` rules above. It walks `steps`, `before`, and `after` the same way it already does. It refuses at load time any `Assertion` that is reached through an inline `assert:` list or an `if`'s `condition` and that sets `target` at all. An `Assertion` reached through the top-level `expect` block alone may set it.

### The command-line interface (CLI): `--target` becomes optional once a scenario declares its own

`--target` stays required, precisely as it is today ([`bajutsu/run/cli.py:1293`](../../bajutsu/run/cli.py)), for a scenario whose own `targets` field is empty.

Once a scenario's `targets` is non-empty, the scenario is self-declaring. `bajutsu run --scenario <file>` resolves every named target from the loaded config without needing `--target` at all. `--scenario` is repeatable, and it always names individual files, never a directory. See [`bajutsu/run/cli.py:135-146`](../../bajutsu/run/cli.py).

Omitting `--target` requires at least one `--scenario`. A self-declaring scenario has no `targets.<name>.scenarios` directory ([`bajutsu/run/cli.py:149-159`](../../bajutsu/run/cli.py)) to fall back on. That directory-glob shorthand, which `bajutsu run --target <name>` alone offers, belongs to one target. A scenario naming multiple targets thus always names its files explicitly.

An explicit `--target` passed alongside such a scenario is checked for membership in `scenario.targets`. It matches if it names any one of the declared targets, and mismatches otherwise. The flag is checked rather than ignored, so a stale flag fails loudly. It does not silently select a target that the file no longer expects.

`--target` is one flag for the whole invocation, but `--scenario` is repeatable, so a batch can name multiple files at once. A legacy file (`targets` empty) has no `target` of its own to fall back on, as today. It needs the invocation's single `--target` to know which target to run against.

Omitting `--target` for a batch that includes even one legacy file thus leaves that file with nothing to resolve. This item refuses such a batch at load time. One legacy file and no `--target` in the same invocation is an error. The error names the file and asks for either `--target` or a `targets` field on it. A silent failure to resolve is avoided.

A batch that supplies `--target` alongside a mix of legacy and self-declaring files keeps working as today's single-target rules already describe. The legacy files use `--target` as they always have. The flag is checked for membership against each self-declaring file's own `scenario.targets`, per the mismatch rule above.

The directory glob is the only whole-suite shorthand `run` has, and it belongs to one target. This item makes that rule an enforced check rather than an author discipline kept by directory layout. The directory-glob path is a plain `bajutsu run --target <name>` with no `--scenario` override. At discovery time, that path refuses any globbed file whose own `targets` field is non-empty. It does not hand the file to the mismatch check above, and it does not launch it. The rejection is a plain error that names the file and points at `--scenario` instead. It happens before the run itself starts.

Without this check, the outcome would be bad either way once such a file lands in that directory:

- If `name` is not one of the file's own declared targets, the mismatch check refuses the file. That rejection fails every other scenario in the same batch alongside it.
- If `name` does happen to be one of its declared targets, the membership check lets the file through. The batch then launches, unnoticed, every other target the file declares too, well beyond the single target that the invocation named.

With the discovery-time rejection in place, a self-declaring scenario can only ever run through an explicit `--scenario <file>`. Neither bad outcome depends on where in the tree the file happens to sit. Keeping self-declaring scenarios in a directory of their own stays good practice for a reader mapping the tree, but this item no longer relies on it for correctness.

This item leaves the web backend's cross-engine matrix (`--browsers`, [`bajutsu/run/cli.py:264-307`](../../bajutsu/run/cli.py)) and `--headed` / `--browser` untouched. They keep applying to a run's single web-platform target precisely as today. A self-declaring scenario that names more than one web-platform target refuses them outright rather than guessing which target they mean. Extending the matrix axis to a multi-target run is a follow-up that this item does not cover.

### Launching every declared target together, and tearing all of them down together

Resolving one target name into one `Effective` config is already a named step. It does more than the bare lookup `resolve(config, target)` performs against `config.targets[target]`. See [`bajutsu/common/config/resolve.py:138-160`](../../bajutsu/common/config/resolve.py).

The caller that reaches it, `_load_effective_with_source` ([`bajutsu/cli/_shared.py:217-285`](../../bajutsu/cli/_shared.py)), finishes by rebasing the result. It calls `eff.rebased(root)`, or `eff.rebased(cfg_path.resolve().parent, confine=False)` for a local config. See [`bajutsu/cli/_shared.py:279-285`](../../bajutsu/cli/_shared.py). Rebasing makes `app_path` / `scenarios` / `baselines` / `schemas` / `goldens` resolve against the config file's own directory rather than the caller's working directory ([`bajutsu/common/config/effective/effective.py:98-108`](../../bajutsu/common/config/effective/effective.py), BE-0242).

A second declared target resolved through the bare lookup alone would resolve its own relative paths against the wrong directory, unnoticed. This item's per-name resolution thus reuses the whole `_load_effective_with_source` chain, once per name in `scenario.targets`, and never the bare `resolve()` alone. The CLI's own `_resolve_config_and_engines` ([`bajutsu/run/cli.py:264-307`](../../bajutsu/run/cli.py)) already calls the chain this way for the single `--target` case.

`_ScenarioRunner` holds one `eff: Effective` field today. `_ScenarioRunner` is built once per run and shared read-only across every scenario and every `ThreadPoolExecutor` worker. Its own docstring states that it "holds no per-scenario mutable state" for precisely that reason. See [`bajutsu/common/runner/pipeline.py:152-165`](../../bajutsu/common/runner/pipeline.py).

Two scenarios in the same run can declare different `targets`. The per-target, already-rebased `Effective` map thus cannot live on that shared object the way a single `eff` does today. It stays a local inside `_run_one_impl`. See [`bajutsu/common/runner/pipeline.py:329-331`](../../bajutsu/common/runner/pipeline.py). That method already keeps every other piece of per-scenario state local, per the same docstring.

`_ScenarioRunner` gains one new read-only field instead: the loaded `Config` that the resolution chain above reads from. `run`'s CLI does not hold on to a `Config` today. `_load_effective_with_source` discards it after resolving the single `--target`, and returns merely `(Effective, source, checkout_root)`. See [`bajutsu/cli/_shared.py:263`](../../bajutsu/cli/_shared.py). `_resolve_config_and_engines` passes merely that `Effective` on. See [`bajutsu/run/cli.py:264-307`](../../bajutsu/run/cli.py).

Both functions gain the loaded `Config` in their own return value. The `Config` flows from there down through `run_and_report` ([`bajutsu/common/runner/pipeline.py:1176-1178`](../../bajutsu/common/runner/pipeline.py)) and `run_all`. See [`bajutsu/common/runner/pipeline.py:986-987`](../../bajutsu/common/runner/pipeline.py). Neither takes a `Config` today, and both take merely the CLI's single `eff: Effective`. With the `Config`, `_run_one_impl` calls the resolution chain once per name in `scenario.targets`. It builds its per-scenario `dict[str, Effective]` locally.

Before any pool exists, one `DeviceLease` is already acquired for the run. This lease is a distinct, upstream concept from the per-scenario `pool.lease()`/`Lease`. `acquire_device(eff, udid)` ([`bajutsu/run/cli.py:1514`](../../bajutsu/run/cli.py)) calls the target's own `eff.device_provider` ([`bajutsu/common/config/schema/target_config.py:72`](../../bajutsu/common/config/schema/target_config.py), `targets.<name>.deviceProvider`, BE-0236). The provider is the built-in `local` provider when unset, and a real device-cloud provider otherwise. The call returns the `udid_spec` and the provisioning data. Every later step, `make_pool` included, resolves devices from them.

This call is unconditional on every run today, and it reads one target's `eff`. `deviceProvider` is per-target config, so this item calls `acquire_device` once per declared target instead, before building any pool. It releases each target's own `DeviceLease` at teardown, alongside its driver and pool.

Bringing every declared target's driver up needs its own pool, not one shared pool, because a pool is platform-specific today. `make_pool` resolves one `pool_actuator`/`pool_env` pair from one platform's udid list and pre-starts that platform's own collectors. See [`bajutsu/common/runner/pool.py:149-182`](../../bajutsu/common/runner/pool.py). An iOS target and a web target, this item's own headline example, need two differently built pools, not one pool handling both.

The launch works as follows:

- `run_all` builds one pool per distinct platform among every declared target across the whole scenario set, instead of the single pool it builds today. It still builds them once per run. Each pool is fed by that platform's own targets' `DeviceLease`s from the previous paragraph.
- `_run_one_impl` leases from whichever pool matches each of its scenario's declared targets.
- `_run_one_impl` launches one driver per resolved `Effective` through the existing `launch_driver`. See [`bajutsu/common/runner/launch.py:27-110`](../../bajutsu/common/runner/launch.py). This replaces the pool's current single per-scenario lease and launch. See [`bajutsu/common/runner/pool.py:408`](../../bajutsu/common/runner/pool.py).
- The results are collected into a local `dict[str, base.Driver]` keyed by target name, all before the first step runs.

Every step that names a target expects it already up. No target is launched lazily on first reference, per the interleaving this item is meant to support.

`_run_one_impl` derives multiple things from the single `self.eff` today:

- the actuator that `select_actuator_for_scenario` picks ([`bajutsu/common/runner/pool.py:260`](../../bajutsu/common/runner/pool.py))
- the locale
- the baseline `capture`
- `run_defaults.interrupts` composed with the scenario's own `s.interrupts` ([`bajutsu/common/runner/pipeline.py:913`](../../bajutsu/common/runner/pipeline.py))
- the target's own `launchEnv`, passed as `target_launch_env` ([`bajutsu/common/runner/pipeline.py:928-932`](../../bajutsu/common/runner/pipeline.py))

Every one of these resolves the same way per declared target's own `Effective` instead, keyed alongside its driver.

Five more arguments follow the same rule. `_run_on_lease` binds `sink`, `relaunch`, `control`, `webview_bridge`, and `transitions` from one lease today. See [`bajutsu/common/runner/pipeline.py:890-907`](../../bajutsu/common/runner/pipeline.py). Each is scoped to the one lease it came from. A `web:` step on a second declared target needs that target's own `webview_bridge`, not the first target's. Otherwise it would silently open the wrong app's WebView.

Two more pieces of state follow the same rule:

- The alert guard is resolved once per scenario from `self.alert_guard_for(s)`. See [`bajutsu/common/runner/pipeline.py:441`](../../bajutsu/common/runner/pipeline.py).
- The network collector is already built once per launched driver from that one target's own lease. See [`bajutsu/common/runner/pool.py:380-428`](../../bajutsu/common/runner/pool.py).

`ctx=EvalContext(..., golden=gc_with_screen)` ([`bajutsu/common/runner/pipeline.py:875-902`](../../bajutsu/common/runner/pipeline.py)) needs the same generalization for a different reason. `self.baselines_dir`, `self.schemas_dir`, and `self.golden_context` are each derived from the single `self.eff` too. Each names a directory that `Effective.rebased` already resolves per target. A `visual` or `golden` assertion on a second declared target must thus compare against that target's own baseline and goldens directories. The primary target's directories do not apply.

Two more fields move the same way. They correct an earlier draft of this design that kept them shared.

- **`caps`.** It is computed once per run from one target's actuator. See [`bajutsu/common/backends.py:194-236`](../../bajutsu/common/backends.py). A single check runs against the *whole* scenario before any device is leased ([`bajutsu/common/runner/pipeline.py:373`](../../bajutsu/common/runner/pipeline.py), BE-0082's fail-fast preflight). `caps` is a property of one backend's capability set, not of the run. Sharing it would preflight a second declared target's steps against the primary target's capabilities. That would wrongly reject a construct the second target supports, or wrongly allow one it does not. This item resolves `caps` per declared target the same way. It runs the preflight once per declared target, against purely that target's own steps (grouped by `step.target`, the same grouping that this item's `_evaluate_expect` change already introduces for `expect`) and that target's own capability set.
- **`mailbox`.** It is not run-level either. It already comes from one target's own config (`targets.<name>.mailbox`, [`bajutsu/common/config/schema/target_config.py:103`](../../bajutsu/common/config/schema/target_config.py)). An `email` / `totp` step reads whichever inbox the *run's* single target configured. See [`bajutsu/common/orchestrator/loop/_functions.py:281`](../../bajutsu/common/orchestrator/loop/_functions.py). Sharing one mailbox across declared targets would poll the wrong inbox for a step whose target configures a different one.

Both `caps` and `mailbox` join the other per-target fields above in `TargetRuntime` instead of staying on `_ScenarioRunner`.

Only `redactor` stays run-level. It applies the union of every declared target's own `secrets` to whichever target's evidence it redacts. A value that one target's config marks secret is thus scrubbed everywhere, rather than merely from that one target's own capture. Widening the redaction set is strictly safer than narrowing it. This differs from `caps` and `mailbox`, where sharing one target's own value produces a wrong answer for another.

`with_lifecycle_phases` ([`bajutsu/common/runner/pipeline.py:959-983`](../../bajutsu/common/runner/pipeline.py)) folds one target's config-level hooks into a scenario before the run starts. The `before` hooks fold in config-then-scenario order. The `after` hooks fold in scenario-then-config order. The CLI calls it once per run today, from its single `eff` (BE-0392). This item calls it once per declared target instead, each time with that target's own `eff`. Each declared target's own `targets.<name>.before`/`after` hooks are then the ones folded in for it, rather than the primary target's alone.

Merging more than one target's hooks into the scenario's single `before`/`after` lists needs an explicit order, which this item adds:

- Every declared target's own `before` hooks fold in ahead of the scenario's own `before` steps. One target's hooks come after another's in `scenario.targets`' declared order. This mirrors today's "config-then-scenario" order.
- The `after` order is the reverse. The scenario's own steps' cleanup comes first, and every declared target's own `after` hooks follow it in the same declared order. This mirrors today's "scenario-then-config" order.

Each folded-in hook step is stamped with its own target's name, as the validator re-check in the previous section already requires. An app-side `erase` hook and a web-side hook folded into the same scenario each still run against the right target.

`preconditions` and `permissions` need no such per-target split. Both are already scenario-level fields today (`scenario.preconditions`, `scenario.permissions`), passed once into `launch_driver`. See [`bajutsu/common/runner/launch.py:27-98`](../../bajutsu/common/runner/launch.py). Both are already backend-tolerant rather than iOS-specific. `env.start` interprets an iOS `Preconditions` field (erase, reinstall) through the simctl lifecycle. A web target's own `env.start` interprets it through a fresh browser context instead. A `--target ios` versus `--target web` run already exercises precisely this difference today, one backend at a time.

This item's per-target `launch_driver` calls pass the same `scenario.preconditions` and `scenario.permissions` to every declared target's own call. Each target's own backend keeps interpreting the parts that apply to it and ignoring the rest. This is not a new capability that the item must invent. A single-target run already relies on it whenever `--target` points at a backend to which some `Preconditions` field doesn't apply.

Acquiring one lease per declared target needs one more rule, which this item adds explicitly. `pool.lease()` blocks on `free.get()` against a queue seeded with the run's udids. See [`bajutsu/common/runner/pool.py:163-165, 255`](../../bajutsu/common/runner/pool.py). Suppose a scenario leases N device-backed targets, now drawn from as many as N different per-platform pools, while `--workers` runs multiple scenarios at once. Every worker can then hold one device from one pool and block forever acquiring its next one from another. The scenario can starve or deadlock.

Acquiring "as one atomic reservation" needs an actual protocol to mean anything, since neither `pool.lease()` nor a second, independent pool object offers a transaction across the two. This item's launch step orders every pool a scenario needs by a fixed, deterministic key (the declared platform name, sorted). Every worker acquires the pools strictly in that order. This is the standard lock-ordering discipline, which rules out circular wait, the shape every deadlock in this paragraph takes.

A worker that cannot acquire its next pool in the sequence blocks on that one pool's queue alone, holding merely the pools before it in the fixed order. No two workers can then be holding each other's next pool, since both approach every pool in the same order. A worker releases anything it already holds if a later pool in its sequence times out, rather than holding a partial set indefinitely.

Teardown brackets the whole set the same way one launch already brackets one scenario today.

- A launch that fails partway through the list tears down every driver that did start before propagating the failure. This mirrors `launch_driver`'s own guard around a readiness failure after `env.start`. See [`bajutsu/common/runner/launch.py:100-108`](../../bajutsu/common/runner/launch.py).
- The run's own end tears down every driver the same way `_run_on_lease`'s `finally: lz.release()` ([`bajutsu/common/runner/pipeline.py:955-956`](../../bajutsu/common/runner/pipeline.py)) already tears down its one lease today. It calls that same release once per declared target's lease.

### Routing a step to its driver, and sharing `${vars.*}` across all of them

`run_scenario`'s existing flat keyword parameters are, read together, everything the pipeline binds from one target's one lease today (per the previous section's enumeration):

`driver`, `sink`, `alert_guard`, `network`, `relaunch`, `control`, `ctx`, `mailbox`, `webview_bridge`, `transitions`, `interrupts`, `locale`, `capture`, `channel`, and `target_launch_env`. See [`bajutsu/common/orchestrator/loop/_functions.py:572-594`](../../bajutsu/common/orchestrator/loop/_functions.py).

A step that names no `target` keeps reading every one of them precisely as it does now. They stay `run_scenario`'s primary target's runtime, unchanged in shape, so every existing caller is untouched.

`run_scenario` gains one new parameter for every *other* declared target, instead of widening each of these into a per-target mapping in place: `target_runtimes: Mapping[str, TargetRuntime] | None = None`. `TargetRuntime` is a new small dataclass. It bundles that same list of fields, plus `caps`.

`caps` differs from the rest. It never reaches `run_scenario` as one of its own keyword arguments, because the preflight check that consults it runs in `_run_one_impl` before `run_scenario` is called at all. The preflight resolves it per declared target the same way, though. It travels alongside the others on each target's own `TargetRuntime`, so it needs no separate carrier. `_run_one_impl` builds one `TargetRuntime` instance per additional declared target, the same way the previous section already builds the driver map.

`_StepRunner`'s own methods already take the active driver as an explicit `active_driver: base.Driver` argument at every call, rather than reading it off shared state. See [`bajutsu/common/orchestrator/loop/_step_runner.py:78-118`](../../bajutsu/common/orchestrator/loop/_step_runner.py). One step kind, a `web:` block, already swaps in a different driver for its own nested steps this way. It builds a `WebContextDriver` and recurses into `exec_steps(step.web.steps, web_driver)` before control returns to the outer `active_driver`. See [`bajutsu/common/orchestrator/loop/_step_runner.py:242-293`](../../bajutsu/common/orchestrator/loop/_step_runner.py).

That swap stays scoped to one already-launched app's in-process WebView bridge, not a second independently launched target. It does confirm that the loop's primitives already pass per-target state explicitly per call, rather than reading it off one shared object. This is precisely the shape a `target`-keyed lookup needs.

`_run_one` ([`bajutsu/common/orchestrator/loop/_step_runner.py:89`](../../bajutsu/common/orchestrator/loop/_step_runner.py)) resolves which `TargetRuntime` governs a given step, the primary one or an entry from `target_runtimes`, by looking up `step.target`. It dispatches the step's driver through `active_driver` precisely as today. It does not use the primary target's own `driver` parameter. That would be wrong for a step nested inside a `web:` block: that step's `active_driver` is already the block's own `WebContextDriver`, and falling back past it would run the step against the wrong app surface without notice. Every top-level step's `active_driver` starts out as its resolved `TargetRuntime.driver`. The dispatch is a strict generalization of today's sole case, not a behavior change for it.

Sharing `${vars.*}` across targets needs no new plumbing. `live_bindings` is already one plain `dict[str, str]`, built once per `run_scenario` call and closed over by every phase, `before`, `steps`, and `after` alike. See [`bajutsu/common/orchestrator/loop/_functions.py:695-697`](../../bajutsu/common/orchestrator/loop/_functions.py). No one driver owns it. An `extract` step against one target already writes into the same dictionary that an assertion against a different target already reads from. Nothing about that mechanism assumes a single driver. It assumes merely that one `run_scenario` call assembles the dictionary once, which still holds.

### Report: naming which target produced each step

`StepOutcome` gains `target: str = ""`, alongside its existing `action: str`. See [`bajutsu/common/orchestrator/types/step_outcome.py:15-44`](../../bajutsu/common/orchestrator/types/step_outcome.py). The value follows three cases:

- It is empty for a scenario whose own `targets` is empty.
- It is the step's declared target name when the step sets one explicitly.
- It is that single declared name when `scenario.targets` has precisely one entry and the step omits `target` (per the validator above). A report is never blank for a self-declaring scenario just because its one target needed no per-step disambiguation.

This is the same "empty means not applicable" convention that `RunResult.engine` already uses for a single-engine run. See [`bajutsu/common/orchestrator/types/run_result.py:26-30`](../../bajutsu/common/orchestrator/types/run_result.py).

`RunResult`'s own `backend`, `device`, `device_name`, and `device_runtime` ([`bajutsu/common/orchestrator/types/run_result.py:25-40`](../../bajutsu/common/orchestrator/types/run_result.py)) each describe precisely one target. They stay meaningful precisely as they are today for a single-target run. A multi-target run needs this same information once per declared target instead of once for the whole scenario.

`RunResult` thus also gains `target_devices: dict[str, TargetDeviceInfo]`, keyed by target name. `TargetDeviceInfo` is a new small dataclass carrying `backend` / `engine` / `device` / `device_name` / `device_runtime`.

The existing singular fields stay empty for a multi-target run. They do not report one declared target's values over the others'. This applies the "empty means not applicable" convention from `StepOutcome.target` above, kept consistent rather than reversed for `RunResult` itself.

An existing reader compiled against today's `RunResult` shape, the JUnit and Common Test Report Format (CTRF) exports among them, sees the single-target case precisely as before. On a multi-target run it sees an empty value, rather than one target's values presented as if they spoke for the whole scenario.

The report's Steps view shows each step's target name beside its action. A new header block lists every declared target's device next to its own evidence. The run's existing single-device header stays for a scenario that declares no targets.

### One open question this proposal leaves to implementation

Every other piece of shared state that an earlier draft of this design left open resolves elsewhere in this design, or was confirmed not to need resolving at all.

- `_ScenarioRunner`'s `run_dir` derives from `runs_dir / run_id`. See [`bajutsu/common/runner/pipeline.py:1223`](../../bajutsu/common/runner/pipeline.py). That directory is a run-level artifact directory that was never derived from any target's `Effective`, so it needs no per-target generalization.
- `udid_spec`, `actuator`, `baselines_dir`, `schemas_dir`, and `golden_context` are precisely the fields that "Launching every declared target together, and tearing all of them down together" above resolves per declared target, alongside `caps` and `mailbox`.
- The optional per-scenario `resolve_actuator` callback (BE-0240, [`bajutsu/common/runner/pipeline.py:190`](../../bajutsu/common/runner/pipeline.py)) is explicitly out of scope rather than dropped without notice. A caller that passes it already opts out of the pool-based actuator selection that this item generalizes. Extending it to resolve one actuator per declared target, rather than one for the whole scenario, is deferred to whichever future item first needs `resolve_actuator` and multi-target scenarios together.

One question stays genuinely open. `gc_with_screen` ([`bajutsu/common/runner/pipeline.py:875-884`](../../bajutsu/common/runner/pipeline.py)) probes one driver's screen bounds for `golden` frame sanity (BE-0006). An iOS target and a web target generally report different screen geometries, and a single `GoldenContext` reaches `run_scenario` today. A `golden` assertion's behavior on a second declared target is thus open. This proposal has identified two shapes: per-target golden contexts, or restricting `golden` to one declared target. Choosing between them is deferred rather than guessed.

### Work breakdown (MECE)

1. **Schema.**
   - Add `Scenario.targets: list[str]`. Reject a duplicate name at load time.
   - Add `Step.target: str | None`.
   - Add `Assertion.target: str | None`. Exclude it from `_ASSERTION_KINDS`, like `from_`.
   - Add a `Scenario`-level validator that ties `target`'s requirement to `len(scenario.targets)`.
     `Step`'s own validator cannot see the enclosing scenario.
   - Walk `steps`, `before`, and every rule in `after`, recursing into every nested `if`/`forEach` list.
     A step nested inside a `web:` block must omit `target`. It always runs against the target the
     enclosing `web:` step already resolved.
   - Apply the same required-or-not rule to `expect`.
   - Reject `target` on any `Assertion` reached through an inline `assert:` list or an `if`'s
     `condition`.
   - Extract the whole check into a plain function. `expand_components` and `with_lifecycle_phases`
     each call it again on their own result. Both mutate an already-validated `Scenario` in place.
     Pydantic never re-runs `model_validator` against a plain attribute assignment or a `model_copy`.
2. **CLI.**
   - Make `--target` optional once `scenario.targets` is non-empty. Make `--scenario` mandatory in
     its place.
   - Check an explicit `--target` against `scenario.targets` rather than overriding it.
   - Resolve and validate every declared name against the loaded config before the run starts.
   - In the `--target`-only directory-glob path, reject any globbed file whose own `targets` field is
     non-empty. Do this at discovery time. Never hand such a file to the mismatch check or launch it.
   - Reject a batch that omits `--target` while including a legacy (empty-`targets`) file. Do this at
     load time, and name the file that has nothing to resolve `--target` from.
3. **Launch and teardown.**
   - Acquire one `DeviceLease` per declared target before any pool exists. Release it at teardown,
     alongside its driver and pool.
   - Return the loaded `Config` from `_load_effective_with_source` and `_resolve_config_and_engines`,
     alongside the `Effective` they already return.
   - Thread the `Config` from `run`'s CLI through `run_and_report` and `run_all` into a new read-only
     field on `_ScenarioRunner`. None of them takes a `Config` today.
   - Build one pool per distinct platform among the declared targets, in `run_all`, instead of
     today's single pool. Feed each pool with that platform's own targets' `DeviceLease`s.
   - Resolve one already-rebased `Effective` per declared target name into a local map in
     `_run_one_impl`. Never store the map on the shared `_ScenarioRunner`.
   - Acquire every pool a scenario needs in a fixed, deterministic order (the declared platform name,
     sorted). Do not issue one blocking `pool.lease()` per target in arbitrary order. This is the
     standard lock-ordering discipline, so no two workers can hold each other's next pool and
     deadlock.
   - Release anything already held if a later pool in the sequence times out.
   - Call `launch_driver` once per name and collect the results into a local `dict[str, base.Driver]`,
     all before the first step. Pass the scenario's own unchanged `preconditions`/`permissions` to
     every call.
   - Call `with_lifecycle_phases` once per declared target with that target's own `eff`. Stamp each
     folded-in hook step with its own target's name. Merge every declared target's hooks into the
     scenario's `before`/`after` in declared order.
   - On a launch failure partway through, tear down every driver that did start. At the run's end,
     tear down the whole set.
4. **Runner.**
   - Add a `TargetRuntime` dataclass. It bundles every argument `run_scenario` already binds from one
     lease: `driver`, `sink`, `alert_guard`, `network`, `relaunch`, `control`, `ctx`, `mailbox`,
     `webview_bridge`, `transitions`, `interrupts`, `locale`, `capture`, `channel`, and
     `target_launch_env`.
   - Add `caps` to `TargetRuntime`, resolved per target the same way. The preflight check in
     `_run_one_impl` consults `caps` directly rather than receiving it through `run_scenario`.
   - Give `run_scenario` the new `target_runtimes: Mapping[str, TargetRuntime] | None` parameter,
     alongside its unchanged flat parameters for the primary target.
   - Resolve `step.target` against `active_driver`/`target_runtimes` in the step-dispatch lookup of
     `_run_one`, instead of using the primary target's own values.
   - Build one `TargetRuntime` per declared target in `_run_one_impl`, the same way today's single set
     of per-lease arguments is already built once per scenario.
   - Group `expect` by target in `_evaluate_expect`. Call `_poll_asserts` once per referenced target's
     own driver and network source, and merge the results back in declared order.
5. **Report.**
   - Add `StepOutcome.target`, including the single-declared-target default.
   - Add `AssertionResult.target`, mirroring it for `expect_results` only. An inline `assert:` result
     stays scoped by its own step's `target`.
   - Leave `RunResult`'s singular fields empty on a multi-target run.
   - Add `RunResult.target_devices`.
   - Add the Steps view's per-step target label.
   - Add the header block that lists every declared target's device.
6. **Docs.**
   - `docs/scenarios.md`: the `targets`/`target` reference and a worked example.
   - `docs/cli.md`: `--target`'s new optional condition and `--scenario`'s new mandatory one.
   - `docs/run-loop.md`: the multi-driver step dispatch.
   - The `docs/ja/` mirrors of all three.
7. **Tests.**
   - Schema: test the target-required validator across zero, one, and two-or-more `targets`. Cover
     `steps`/`before`/`after`/`expect` and every nested `if`/`forEach`/`web` shape.
   - Schema: test duplicate-name rejection and the `Assertion.target`-outside-`expect` rejection.
     Test that the re-check survives `expand_components` and `with_lifecycle_phases`.
   - CLI: test the `--target` optional/mandatory switch and mismatch rejection. Test the
     directory-glob rejection of a self-declaring file and the mixed legacy/self-declaring batch
     rejection.
   - Launch: test that a launch failure partway through a multi-target set tears down what did start.
     Test that the lock-ordering acquisition never deadlocks two concurrent workers that need the same
     two platforms in opposite order.
   - Launch: test that per-target `caps` preflight refuses a construct only the primary target lacks.
     Test per-target `mailbox` selection.
   - Runner: test mixed-target step routing within one scenario (interleaving, not just two
     contiguous blocks). Test `${vars.*}` sharing across targets. Test `expect` grouped and merged
     back in declared order.
   - Report: test that a multi-target `RunResult`'s singular fields stay empty while `target_devices`
     is populated. Test that an existing JUnit/CTRF reader still parses a single-target run unchanged.

## Alternatives considered

- **An `actor`/`verifier` role split.** One target performs the scenario's `steps`, and a second one runs a separate `verify` block afterward. This was an earlier shape for this same item. Rejected. It covers merely one hand-off in one direction per scenario. It cannot express acting on one target, verifying on another, then acting on the first target again. A real cross-target test needs that interleaving, since a web-side edit reaching the app is precisely as common as an app-side action reaching the web.
- **A dedicated `switchTarget` step**, mirroring the `setViewport`/`emulate` step that [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md) already considered and rejected for device-mode switching. Rejected for the same reason. A switch step turns routing into an imperative action that a reader has to track across the whole step list. A `target` modifier keeps routing visible on the one step it governs, the same way `capture` and `extract` keep their own effect visible on the step they modify rather than as a preceding command.
- **Network-only cross-target assertions.** There is no second live driver at all. A `request` assertion alone, against the acting target, confirms that it called the other platform's application programming interface (API) with the right body. This is already possible today. It stays as the lighter option for a team that needs merely that. Rejected as the sole mechanism, because it answers a different question from the one this item's motivation names. It confirms that the acting target sent the right call. It does not confirm that the other platform's own client received and rendered the call correctly, which is what "check the result on the other platform" means.
- **Two chained scenario files**, with a new runner flag piping one file's captured `${vars.*}` into a second file run against a different target. Rejected. Splitting one logical test across two files duplicates `preconditions`, `before`, and `after` across both. It also cannot interleave: the first file finishes entirely before the second one starts, so a test needing app → web → app again would still need a third file.
- **A repeatable `--target`** (`list[str]`, mirroring `--scenario`'s own existing repeatable-option shape, [`bajutsu/run/cli.py:1294-1303`](../../bajutsu/run/cli.py)) instead of a scenario-level `targets` field. Rejected. Which platforms a scenario interleaves is a property of the scenario itself, not a per-invocation choice. A CI job invoking `bajutsu run --scenario a.yaml b.yaml ...` over a whole suite of files would otherwise need its `--target` list kept in lockstep with whichever file in that invocation happens to need which targets. The two would drift apart once one file's `targets` changes and the flags do not.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Schema: `Scenario.targets`, `Step.target`, `Assertion.target`, and the validator tying
      `target`'s requirement to `len(scenario.targets)` across `steps` and `expect` alike.
- [x] CLI: `--target` optional under a self-declaring scenario; mismatch rejection; config
      validation for every declared name.
- [x] Launch and teardown: the per-target resolved config threaded through to `_ScenarioRunner`;
      one pool per declared backend; one driver per declared target, launched together and torn
      down together.
- [x] Runner: the `TargetRuntime` bundle, `run_scenario`'s `target_runtimes` mapping, the per-target
      actuator/locale/capture/interrupts/guard/network/evidence-context construction, and
      `_evaluate_expect`'s per-target grouping.
- [x] Report: `StepOutcome.target`, `AssertionResult.target`, `RunResult.target_devices`, and the
      report views that show them.
- [x] Docs: `docs/scenarios.md`, `docs/cli.md`, `docs/run-loop.md`, and their `docs/ja/` mirrors.
- [x] Tests: schema validator (including the `expand_components`/`with_lifecycle_phases` re-check),
      CLI selection and rejection rules, multi-pool lease/deadlock coverage, per-target
      caps/mailbox/preflight, mixed-target step routing, and report backward compatibility.

Log:

- [#2025](https://github.com/bajutsu-e2e/bajutsu/pull/2025) — Unit 1 (Schema). `Scenario.targets`,
  `Step.target`, `Assertion.target`, and the validator tying `target`'s presence to
  `len(scenario.targets)`, recursively across `steps`/`before`/`after`/`interrupts` and every
  nested `if`/`forEach`/`web` shape. Closed a gap the design didn't call out: `apply_setups` is a
  third mutation point that rebuilds an already-validated `Scenario` without Pydantic re-running a
  `model_validator`, alongside `expand_components` and `with_lifecycle_phases` — all three now
  re-run the check on their own result. Resolved two open questions the design left unaddressed by
  failing closed rather than guessing: a `use:` step (component expansion discards its own
  `target`) and a non-empty `interrupts` (its `condition` has no target of its own to poll) are
  both refused once a scenario declares two or more targets, deferred to a later unit. Since
  nothing yet routes a step to more than one live target, every caller that would run or emit
  against such a scenario refuses it instead — `run_all` (shared by `run` and `audit --repeat`),
  `codegen`, and serve's own Codegen endpoint — rather than acting against the wrong one without a
  word.

- [#2028](https://github.com/bajutsu-e2e/bajutsu/pull/2028) — Units 2-7, covering the CLI through the tests. `--target` becomes optional once
  every `--scenario` file declares its own `targets`. Each declared name resolves against the same
  loaded config. Each takes its own `DeviceLease` and pool, then launches before the first step.
  The step loop sends each step to its own target's driver and evidence, over one shared
  `StepLoopState`. One numbering, one `${vars.*}` dictionary, and one verdict then span every
  target. Three choices differ from the design on purpose:

  - **The pipeline takes a resolved per-target map, not the loaded `Config`.** The design threaded
    `Config` into `_ScenarioRunner` and re-ran `_load_effective_with_source` per scenario. Instead
    the CLI resolves every declared name once, through that same chain, so rebasing stays
    identical. It passes a `Mapping[str, TargetPool]` the runner never mutates. That keeps a
    CLI-layer import out of the runner. It also stops a Git-sourced config being re-materialized
    per scenario per target.
    The map's key is the target name. Two scenarios declaring different subsets each still pick
    their own.
  - **A pool is keyed by actuator, not platform.** `device_pool` builds its environment from it. Two actuators on one platform would share a wrongly-built pool
    under a platform key. An actuator key separates every pair a platform key would, and that one
    besides. The lock-ordering acquisition sorts on the same key.
  - **The open question on `golden` resolves to per-target contexts.** Each target's
    `EvalContext` probes its *own* driver's screen bounds for golden frame sanity. It reads its
    own `baselines` / `schemas` / `goldens` directories where its config names them. Judging a web
    target's golden by an iOS target's geometry would fail a well-framed golden. Restricting
    `golden` to one declared target would have cost precisely that.

  Two refusals unit 1 recorded stay in place. They cover a `use:` step and a non-empty
  `interrupts`, under two or more declared targets. Both need a decision this item still has no
  answer for. Neither blocks the interleaving this item exists to deliver. The cross-browser matrix
  (`--browsers`) likewise stays single-target, per *Detailed design*. `run` refuses to combine it
  with a multi-target scenario, rather than guess.

  One limit the design does not cover. Two targets on one backend share that backend's device queue
  for a scenario's whole length. `run` thus refuses a pool holding fewer devices than a scenario
  needs. It also caps `--workers` to what the pools can serve. Blocking on a queue that will never
  free a device is the alternative.

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
