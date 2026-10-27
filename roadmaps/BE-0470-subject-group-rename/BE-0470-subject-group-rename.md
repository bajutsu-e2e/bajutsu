**English** · [日本語](BE-0470-subject-group-rename-ja.md)

# BE-0470 — Rename the target's app group to subject

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0470](BE-0470-subject-group-rename.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0470") |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

A target describes what it tests under the key `app`. The key fits an iOS or Android app, but a web
target writes `app: { url: https://example.com }`, and a site is not an app. This item renames the
group to `subject`, the thing under test, in the target config and in a scenario's preconditions.
`targets.site.subject.url` and `targets.app-sim.subject.id` then read correctly for both kinds of
target. The fields inside the group keep their names and meanings.

## Motivation

The `app` group holds the same role on every platform: what Bajutsu launches and tests. On iOS and
Android it carries `id`, `path`, `build`, `reinstall`, and `launch`. On the web it carries `url`
and `launch.env`. Every platform shares `startWhen` and `idNamespaces`. The name fits one side of
that list. A reader of a web target meets `app.url` and has to learn that "app" here means a site,
and the config schema, the docs, and the error messages all repeat the word.

The mismatch matters more as Bajutsu adds platforms. A Flutter target, the next planned platform,
would fit `app`, but a web view or a site in mobile Safari would not. A name that describes the role
rather than one kind of software keeps fitting as platforms are added.

The outcome a reader can check after this ships is concrete. No target or scenario in the repository
writes `app:` as a group key, `bajutsu config schema` shows `subject` on every platform, and an old
`app` key fails the load with the path that replaces it.

## Detailed design

### The rename

| Old key | New key |
|---|---|
| `targets.<name>.app` | `targets.<name>.subject` |
| `defaults.platforms.<platform>.app` | `defaults.platforms.<platform>.subject` |
| `preconditions.app` (`reinstall`, `launch.env`, `launch.args`, `launch.deeplink`) | `preconditions.subject` |

Every field inside the group keeps its name and meaning. `subject.id` is still a bundle id or a
package name, `subject.url` still the page a web target opens, and `subject.startWhen` still the
condition a run waits for before its first step.

```yaml
targets:
  app-sim:
    platform: ios
    subject: { id: com.example.app, path: build/App.app }
  site:
    platform: web
    subject: { url: https://example.com }
```

### Migration

An old `app` key fails the load and names `subject`, as BE-0452 did for its own moves. The loader
never accepts both spellings. A scenario plan stored before the rename still loads. Two maps
change for that:

| Map | Change |
|---|---|
| `_PRECONDITION_MOVES` in `bajutsu/common/scenario/load.py` | Each destination moves from `app` to `subject`, for flat keys stored before BE-0452. The map also gains an `app` to `subject` step for a nested group stored after BE-0452, applied to stored plans alone. |
| `FLAT_PRECONDITION_KEYS` in `bajutsu/common/scenario/models/scenario/preconditions.py` | Each replacement path a migration error names moves to `subject`, so no message points at a key the loader no longer accepts. |

### Where the change lands

- The platform registry in `bajutsu/common/config/schema/platform/registry.py` names the group
  `subject` in `Group`, `GROUPS`, and `PlatformSchema`. `TargetConfig`, `PlatformDefaults`, and the JSON
  Schema builder in `json_schema.py` follow.
- The scenario's `Preconditions` model renames its `app` field to `subject`.
- Two readers in `bajutsu/serve/batch_provider/device_farm_batch_provider.py` follow by hand.
  They reach the group as a raw mapping, which no model catches:
  - The launch-env collision guard reads `preconditions["app"]["launch"]["env"]`.
  - The packaged Device Farm config gets its injected environment at `targets.<name>.app.launch.env`.
- Internal class names such as `IosApp` and `AppCommon` may follow in the same change or later. They
  are not part of the config surface.
- [Configuration](../../docs/configuration.md) and its `docs/ja/` mirror, the
  [CLI](../../docs/cli.md) page, and the [architecture](../../docs/architecture.md) page use
  `subject` throughout. The [glossary](../../docs/glossary.md) gains an entry for it.

### Relation to the other `runsOn` proposals

This item is independent of the companion proposals *Group runsOn by destination and drop the
scenario's runsOn* and *Let runsOn name an iOS device family and a set of device or browser kinds*. It touches a different group and no matching rule. Landing it in the same release as the
first of those keeps the breaking config changes to one migration for users.

### Prime-directive compliance

No LLM enters a run, and no behavior changes. The item renames a config key and keeps every
difference in config.

### Out of scope

- The scenario's `app:` step block and the `installApp` action. There "app" means the native
  context of a hybrid app, as opposed to its `web:` context, so the name stays correct.
- The `platform` key and its values (`ios`, `android`, `web`, `fake`).
- The internal `Effective` field names, which no config author sees.

### Work breakdown

1. **Schema.** Rename the group in the registry, `TargetConfig`, `PlatformDefaults`, the scenario's
   `Preconditions`, and the JSON Schema output. Fail an old `app` key with the new path, and map it
   for stored scenario plans.
2. **Configs and scenarios.** Rewrite `app` to `subject` in the `demos/` configs, the demo
   scenarios, and the test fixtures.
3. **Docs.** Update the bilingual docs named above, and add the glossary entry.

Each unit carries its own tests: the migration message, a stored plan that still loads, and the
JSON Schema output.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Keep `app` | It misnames every web target, and the mismatch grows as platforms are added. |
| `testTarget` | Bajutsu already calls each entry under `targets` a target, as do `--target` and the code. `targets.site.testTarget` would give the word two meanings in one path. |
| `product` | Plain, but beside `path` and `build` it reads as Xcode's Products, the build output. |
| `frontend` | It fits a UI end-to-end tool, but reads oddly beside build settings such as `frontend.build`. |
| `sut` (system under test) | Precise in testing jargon, but an abbreviation a first-time reader has to look up. |
| `artifact` | A web target's URL is not a build artifact. |
| Accept both `app` and `subject` for a grace period | Two spellings would coexist in the loader, the JSON Schema, and the docs. A failing load that names the new key moves a config in one edit. |

`subject` meets a milder form of the objection to `testTarget`. A scenario's `email.match` already
takes `subject` and `subjectMatches` for a mail header. One scenario file can then hold
`preconditions.subject` beside `email.match.subject`. We accept that collision. The two
uses sit in unrelated blocks and never share a path, while `targets.site.testTarget` repeats the
word inside one path. The glossary entry for `subject` names both senses.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1: schema
- [ ] Unit 2: configs and scenarios
- [ ] Unit 3: docs

## References

- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md): the item that grouped a target's config, introducing the `app` group this one renames.
- `bajutsu/common/config/schema/platform/registry.py` and `bajutsu/common/scenario/load.py`: the group registry and the stored-plan migration this item extends.
