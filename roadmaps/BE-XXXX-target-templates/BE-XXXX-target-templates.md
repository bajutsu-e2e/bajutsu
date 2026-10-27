**English** · [日本語](BE-XXXX-target-templates-ja.md)

# BE-XXXX — Target templates: share target settings by extending a named template

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-target-templates.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Configuration sourcing |
| Related | [BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md), [BE-0174](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment.md), [BE-0422](../BE-0422-inline-scenario-components/BE-0422-inline-scenario-components.md), [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md), [BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings.md) |
<!-- /BE-METADATA -->

## Introduction

A [target](../../docs/glossary.md#target-app-device) declares every per-app difference in
`bajutsu.config.yaml`. Two layers shape it today: `defaults` for every target, and
`defaults.platforms.<platform>` for every target of one platform. Nothing sits between "all targets"
and "this one target". This item adds that middle layer, the **target template**.

A template is a named, partial target block. A target, or another template, pulls it in with
`extends`, the way a scenario pulls in a component. The template is a building block and never a
target: it lives under its own top-level key `templates:`, so `--target` cannot select it and no
code that walks `targets:` sees it. A template can be written inline in the config or kept in a
file of its own.

## Motivation

Targets that share an app differ in a few fields and repeat the rest. `demos/showcase/showcase.config.yaml`
declares 15 targets. Six of them repeat the same `driver.runner.testRunner` path, and 14 repeat the
`SHOWCASE_UITEST` launch environment. A change to the shared part, such as a new runner path, means
editing every copy. A copy that someone misses drifts without any error, because each copy is valid
on its own.

The two existing layers do not reach this case. `defaults` is too wide: a setting for the four
SwiftUI targets does not belong to the Compose targets or the web targets. `defaults.platforms.ios`
is still too wide, because the showcase's iOS targets are not all the same app. YAML anchors and
merge keys do not help either, as the alternatives below show.

The observable result is the config itself. After the showcase config moves its shared blocks into
templates, each block appears once, and a test shows that `resolve` returns an identical `Effective`
for every one of the 15 targets before and after the move. A later reader can check both claims:
the line count of the file, and the unchanged `Effective`.

## Detailed design

### Behavior

A template is a partial target block. It may hold any of the eleven target keys
([BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md)) and none is
required, so it can omit `app.id` or `platform`. Both a target and a template take one new key,
`extends`, holding a template name or a list of them.

```yaml
templates:
  showcase-ios:                    # inline: any block of a target, none required
    platform: ios
    app:
      launch: { env: { SHOWCASE_UITEST: "1" } }
    driver:
      runner: { testRunner: ../../BajutsuKit/Runner/build/dd/Build/Products/BajutsuRunner.xctestrun }
  masked-password:
    evidence: { redact: { fields: [password] } }

targets:
  showcase-swiftui:
    extends: [showcase-ios, masked-password]     # later entries win over earlier ones
    app: { id: com.bajutsu.showcase.ios.swiftui }
  showcase-uikit:
    extends: ./templates/uikit.template.yaml     # a file reference
    app: { id: com.bajutsu.showcase.ios.uikit }
```

- **Layer order.** From the weakest to the strongest: `defaults`, then
  `defaults.platforms.<platform>`, then each `extends` entry in listed order, then the target
  itself. A template that extends another template places the base first, so the extending
  template wins over its base.
- **The merge rules are the existing ones.** The five rules in
  [Defaults](../../docs/configuration.md#defaults) apply to every layer: dictionaries merge key by
  key, `null` clears a key, lists replace, `run.secrets` and each `evidence.redact` list union, and
  `null` on those union lists or their parent blocks fails the load. No new list syntax is added.
- **A name or a path.** An `extends` entry that contains `/` or ends in `.yaml` or `.yml` is a file.
  Any other entry is the name of an inline template. The rule is the one BE-0422 gives `use`.
  A file holds a single template block. The path resolves against the directory of the file that holds the `extends`.
  It must stay inside the suite root, as
  [BE-0174](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment.md)
  requires. A config parsed from a document with no file may name inline templates alone.
- **A file template resolves its own relative paths.** A relative value inside a template file resolves against that file's directory, as in a component file. The rule covers three kinds of value.
  - the nested `extends` entries;
  - the path fields `app.path`, `runsOn.seedPhotos`, `driver.runner.testRunner`, and `paths.*`;
  - the component a `hooks` step calls with `use`.

  When the loader reads the file, it rewrites each such value to resolve against the config file's directory. Everything after the merge therefore sees one base. A file template's `extends` names files alone. The template file has no `templates:` of its own to look names up in. Shell commands such as `app.build` are not paths. `services.server.cwd`, `services.server.dockerfile`, and `services.mockServer.stubs` do not resolve against the config file today. The loader leaves them unchanged, so they keep the base they have today.
- **`platform`.** A template may declare it. The target's platform is its own `platform`, else the one
  its templates declare. Two layers that declare different platforms fail the load, naming both. A
  target with no platform from any layer fails as it does today.
- **A template is never a target.** `bajutsu run --target <name>` and every serve endpoint accept
  only names under `targets:`. When the name matches a template, the error says so and lists the
  targets: `'showcase-ios' is a template, not a target`. A template and a target may share a name,
  since `extends` looks up templates alone.
- **Errors fail the load.** An unknown template name lists the known ones. A cycle prints its path
  (`a → b → a`). A template that two entries both reach, such as a shared base, is folded once.
- **A template is checked through its targets.** The loader validates a template's keys against the
  eleven target keys plus `extends`. The full group validation runs on each target after the layers merge, so a
  template that no target extends has its nested keys unchecked. `bajutsu config schema` narrows that gap for editors. A template that declares `platform` is checked against that platform's target shape. Every field in it is optional. A template with no `platform` has its platform-independent groups checked in full. Its `app`, `runsOn`, and `driver` are checked against any platform's partial shape.

### Implementation

The change touches the config schema, plus the one caller outside it that runs the defaults merge on raw YAML. `resolve` and everything downstream read
the merged target as before.

| Piece | Where | Change |
|---|---|---|
| Layer fold | `bajutsu/common/config/schema/_merge.py` | `merge_defaults(defaults, target)` becomes a fold that puts an ordered list of layers onto the defaults base. It picks `defaults.platforms.<platform>` by the platform the layers resolve, not by the target's own key alone. The old call is the one-layer case. |
| Device Farm condition check | `_check_no_conditions` in `bajutsu/serve/batch_provider/device_farm_batch_provider.py` | Today it calls `merge_defaults` on the raw YAML to find a target's `runsOn` conditions. It switches to the expansion and the fold, so a condition that a template supplies is still refused. |
| `extends` expansion | new `bajutsu/common/config/schema/_templates.py` | Turns a target's `extends` into the ordered layer list: depth-first, bases first, each template once. Detects cycles and unknown names, reads files under the suite root, and reports platform conflicts. |
| Path rebasing | `_templates.py`, sharing one table of path fields with `Effective.rebased` in `bajutsu/common/config/effective/effective.py` | Rewrites a file template's relative path values to resolve against the config file's directory instead of its own. Both this rewrite and `Effective.rebased` read the same table, so a new path field is added in one place. |
| Entry point | `Config._merge_defaults` in `schema/config.py` | Calls the expansion, then the fold, for each target, including when the config has no `defaults`. Adds `templates: dict[str, dict[str, Any]]` to `Config`. |
| Target-name error | `resolve` in `bajutsu/common/config/resolve.py` | The `KeyError` for an unknown target names the template when the name matches one. |
| Editor schema | `bajutsu/common/config/json_schema.py` | Adds `extends` to each platform's target. Adds a `templates` map whose entries are a `oneOf` of per-platform partial targets keyed by `platform`, plus one branch with no `platform`, in which the platform-shaped groups take any platform's partial shape. |

The fold must apply the layers to the defaults one at a time, never merge the templates into one block
first. A template that writes `null` to clear a value `defaults` supplies must still find that value
when it is applied. Flattening the templates first would drop the `null` and let `defaults` restore
the value.

`extends` is consumed before `TargetConfig` validates, so `TARGET_KEYS` keeps its eleven entries
and the strict unknown-key guard is unchanged.

`ai` keeps its field-by-field merge with `defaults.ai` in `resolve`. A template's `ai` folds into
the target's `ai` in the layer order above, and the result then merges with `defaults.ai` as today.

### Out of scope

| Excluded | Reason |
|---|---|
| Template parameters (`with:`) | Passing values needs a substitution order and a boundary with `${secrets.X}`. A later item can add it. |
| A new list syntax for append or remove | The existing rules (replace, plus the union lists) already apply to every layer. |
| References across config files or into a Git repository | The file form stays inside the suite root. Sharing across repositories is a separate sourcing question. |
| A target extending another target | A target is runnable, so editing it would change every target that extends it. |

### Work breakdown

1. Refactor `merge_defaults` into a layer fold with no behavior change.
2. Add the `templates:` field, the `extends` expansion with a file template's path rebasing, and its errors.
3. Add the template-name error to `resolve`, and `extends` and `templates` to the JSON Schema.
4. Add tests: layer order, `null` clearing, union lists, file references and containment, relative paths inside a file template, cycle,
   unknown name, platform conflict, and the template-as-target error, and a Device Farm job refused for a `runsOn` condition that a template supplies.
5. Document the feature in `docs/configuration.md` and `docs/ja/configuration.md`, and update
   `docs/architecture.md` and `DESIGN.md` where they describe the config layers.
6. Move the shared blocks of the showcase config into templates, with a test that every target's
   `Effective` is unchanged.

## Alternatives considered

| Alternative | Why not |
|---|---|
| YAML anchors and merge keys (`<<: *base`) | Works today with no code, but the merge is shallow: a nested `app` block in the target replaces the anchor's whole `app`. Lists replace even where `run.secrets` must union. An anchor stays inside one file, and a mistake raises no error from Bajutsu. |
| Resolve a file template's paths against the config file's directory | Simpler, since no value needs rewriting. But a template file would then read differently from a component file, and a moved template would break silently until a path failed at run time. Resolving against the template's own directory keeps the rule that component files already follow. |
| A marker inside `targets:` (`abstract: true`) | The block stays among the targets, so every code path that walks `config.targets` (serve, provisioning, `resolve`) needs an exclusion, and a missed one lists a template as runnable. A separate key makes the exclusion structural. |
| A single `extends` entry, without chaining | Composing two axes, such as platform and environment, then needs one template per combination. A list plus chaining composes them. |
| A target extending another target | The base is runnable, so a change to it silently changes its dependents. It also leaves no way to declare a block that must never run. |
| Named groups under `defaults` | The same idea under another name, but it adds a second way to say "this target is shaped like X" next to `defaults.platforms`. `templates:` keeps `defaults` for what applies to every target. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Refactor `merge_defaults` into a layer fold, with no behavior change
- [ ] Add `templates:`, the `extends` expansion with file-template path rebasing, and its load errors
- [ ] Add the template-name error to `resolve`, and `extends` and `templates` to the JSON Schema
- [ ] Add the tests for layer order, clearing, files, cycles, and platform conflicts
- [ ] Document the feature in `docs/` and `docs/ja/`, and update `docs/architecture.md` and `DESIGN.md`
- [ ] Move the showcase config's shared blocks into templates, with an unchanged `Effective` per target

## References

- `bajutsu/common/config/schema/_merge.py` — `merge_layer` and `merge_defaults`, today's two-layer merge
- `bajutsu/common/config/schema/config.py` — `Config._merge_defaults`, where the merge runs before validation
- `bajutsu/common/config/schema/target_config.py` — `TARGET_KEYS` and `TargetConfig`
- `bajutsu/common/config/resolve.py` — `resolve` and `parse_config_dict`, which carries the suite root
- `bajutsu/common/config/json_schema.py` — the editor schema, including the `defaults.platforms` shape
- `bajutsu/common/config/effective/effective.py` — `Effective.rebased`, the path fields resolved against the config file's directory
- `docs/scenarios.md` — the component `use` step, whose name-or-path rule `extends` copies
- `demos/showcase/showcase.config.yaml` — the first consumer
