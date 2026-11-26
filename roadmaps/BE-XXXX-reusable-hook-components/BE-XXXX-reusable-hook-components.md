**English** · [日本語](BE-XXXX-reusable-hook-components-ja.md)

# BE-XXXX — Bundle a scenario's preparation and teardown into a reusable `hooks` component

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-reusable-hook-components.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md), [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md), [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md), [BE-0471](../BE-0471-target-templates/BE-0471-target-templates.md), [BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks.md), [BE-0497](../BE-0497-tag-conditional-hooks/BE-0497-tag-conditional-hooks.md), [BE-0501](../BE-0501-scenario-command-hooks/BE-0501-scenario-command-hooks.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a **`hooks` component**. One file holds a preparation and its teardown.
The file takes parameters, and more than one place can apply it.

A `hooks` component holds two kinds of content:

- external commands that run around a run or a scenario
- steps for the `setup` and `cleanup` phases

A target applies a component under `targets.<name>.hooks.use`.
A scenario applies one under its own `hooks.use`.
Bajutsu expands the component while it loads the files.
The run loop then sees command entries and steps as today, filtered by tags per scenario.
The expansion calls no model and adds no wait.
Verdicts still come from the same assertions.

## Motivation

Bajutsu has two kinds of
[hooks](../../docs/configuration.md#external-commands-around-a-run-and-each-scenario-hooks-be-0491).
The step lists, `setup` and `cleanup`, hold scenario steps.
A step there can already call a [component](../../docs/scenarios.md#components-use--reusable-steps)
with `use`.
The command lists hold external commands: `beforeRun`, `beforeScenario`, `afterScenario`, and
`afterRun`.
No mechanism reuses a command entry.

A target template (`extends`) can share part of a target block.
A template takes no parameters, though.
A list in a later layer also replaces the earlier list.
A scenario cannot use a template at all.

Consider a suite where a dozen scenarios need a seeded user.
The preparation spans four places:

- a `beforeScenario` command that creates the user
- `setup` steps that sign in
- `cleanup` steps that sign out
- an `afterScenario` command that deletes the user

Three problems follow.

| Problem | What happens |
|---|---|
| The halves drift apart | The creating command and the deleting command sit in different lists. Bajutsu does not link them, so an author writes `when` twice. An edit can reach one half and miss the other |
| The same block gets copied | Every target and scenario that needs the user repeats all four parts. A new script argument means editing each copy |
| Variants need copies too | A free user and a premium user differ in one value. Each still needs a full set of entries |

Once this item ships, the suite files show the change.
The preparation and teardown of the seeded user live in one file.
Each place that needs the user holds one `use` entry with the file name and its values.

## Detailed design

### 1. The component file

A `hooks` component is a single mapping.
Every field is optional, but one of the six lists must hold an entry.

| Field | Content |
|---|---|
| `params` | The names a caller supplies, read as `${params.<name>}` |
| `beforeRun`, `afterRun` | Command entries, with the fields of a target's entry |
| `beforeScenario`, `afterScenario` | Command entries with the same fields |
| `setup` | Steps, as in a scenario's `setup` |
| `cleanup` | Cleanup rules, as in a scenario's `cleanup` |

```yaml
# hooks/seeded-user.hook.yaml
params: [plan]
beforeScenario:
  - name: user
    cmd: "./create-user.sh ${params.plan}"   # writes userId=... to $BAJUTSU_OUTPUT
setup:
  - type: { text: "${hooks.user.userId}", into: { id: login.username } }
  - tap: { id: login.submit }
cleanup:
  - on: always
    steps:
      - tap: { id: session.logout }
afterScenario:
  - cmd: "./delete-user.sh"                  # reads $BAJUTSU_HOOK__user__userId
```

A step in the file may call a step component with `use`.
The file cannot apply another `hooks` component.
It also holds no `interrupts` and no `sandbox`.
The current limits on a target's setup steps hold here too: no `group` and no `setMocks`.

Bajutsu substitutes parameters at load time.
The substitution covers `cmd`, the values of `env`, and steps.
For `cmd`, Bajutsu first splits the line into arguments, then substitutes into each one.
A value with a space or a quote thus stays inside its argument.
The command policy checks the substituted line.
The load stops on a missing or unknown parameter.
It also stops on a `${params.*}` token that stays unresolved.

A relative `cwd` resolves against the directory of the component file.
An entry without `cwd` runs in that directory too.
The component and its scripts can then move together.

The file must stay inside the suite root, as a step component does.
Do not place it directly in the scenarios directory.
Bajutsu loads every top-level `.yaml` file there as a scenario file.

A sandboxed command sees the directory of the config and nothing else.
Under a sandbox policy, a component file outside the config directory stops the run.
The check does so before any device work.

### 2. Applying a component

Both a target's `hooks` and a scenario's `hooks` gain a `use` list.

```yaml
targets:
  web:
    hooks:
      use:
        - hook: ./hooks/seeded-user.hook.yaml
          with: { plan: free }
```

```yaml
- name: upgrade offer
  hooks:
    use:
      - hook: ../hooks/seeded-user.hook.yaml
        with: { plan: premium }
        as: premium
        when: { tags: [payments] }
```

| Field | Meaning |
|---|---|
| `hook` | The component file, resolved from the file that holds the entry |
| `with` | The parameter values |
| `as` | An alias that keeps two applications apart |
| `when` | A tag condition for the whole application, with `tags` and `exclude` |

A scenario cannot apply a component that holds `beforeRun` or `afterRun`.
A scenario is not a run.
The load stops and names the field.

A target template may carry `hooks.use`.
A path in a template file resolves against that file, as its other paths do.
The `use` lists of `defaults`, templates, and the target join in layer order.
A later entry replaces an earlier one when its resolved `hook` path and `as` both match.
Other lists replace rather than join.
`use` joins because a replaced list would drop a shared component without notice.
A `null` clears the whole list.

### 3. Names and aliases

Today a command name is unique across all the targets a run drives.
Direct entries keep that rule.
Names that come from a component get one scope per target instead.
Two targets can then share a component through a template with no clash.

| Name | Must differ from |
|---|---|
| A direct entry of a target | Every other name in the run, as today |
| A direct entry of a scenario | Every name the scenario sees, as today |
| From a component of a target | The other names of that target, and every direct name |
| From a component of a scenario | Every name the scenario sees |

A token resolves by the target of the step or assertion that reads it.
Take a web target and an iOS target that both apply the seeded-user component.
`${hooks.user.userId}` in a web step reads the web value.
The same token in an iOS step reads the iOS value.
A step with no `target` follows the current rule for a multi-target scenario.

A step cannot read a component name of another target.
The load stops on such a token and names the target that holds it.
Commands follow the same scope.
A command of a target receives the component outputs of that target alone.
Bajutsu keeps run-level outputs per target in the same way.

A command of a scenario can meet one name that two driven targets hold.
Such a command receives no variable for that name.
Bajutsu never picks one of the two values.

Applying one component twice within a scope needs an `as` on one of the two.
Take `as: premium` as an example.
Outside the component, a step writes `${hooks.premium.user.userId}`.
An outside command reads `BAJUTSU_HOOK__premium__user__userId`.

Inside the component, steps and commands keep the local name `user`.
Bajutsu rewrites the step tokens during expansion.
The rewrite runs after nested step components expand, so it reaches their tokens too.
Each command of the application receives the variables under the local names.
A local name wins over an outer entry of the same name, in tokens and variables.
The rewrite touches the names that the component declares, and no others.

An alias follows the naming rule for an entry name.
An alias may not equal any entry name that the scenario sees.
Names hold no dot.
A three-part token and a four-part token thus stay distinct.

### 4. Order

A component wraps the entries written directly beside it.

| Lists | Order |
|---|---|
| `beforeRun`, `beforeScenario`, `setup` | Components in `use` order, then direct entries |
| `cleanup`, `afterScenario`, `afterRun` | Direct entries, then components in reverse `use` order |

Teardown thus runs in the reverse order of preparation.

As an example, a target applies the seeded-user component.
It also writes one direct entry in `beforeScenario`, `cleanup`, and `afterScenario`.
One scenario then runs in this order:

1. `beforeScenario`: the component's `create-user.sh`, then the direct entry.
2. The app launch.
3. `setup`: the component's sign-in steps, then the scenario's own `setup`.
4. The scenario's `steps` and `expect`.
5. `cleanup`: the scenario's rules, the direct rule, then the component's sign-out.
6. `afterScenario`: the direct entry, then the component's `delete-user.sh`.

The two levels keep their current nesting.
The target's entries and components stay outside those of the scenario.
Error handling also stays the same.
Within one level, a non-zero `beforeScenario` entry leaves every `afterScenario` entry to run.
A non-zero entry of a target still skips the scenario's own lists.
It also skips the components that the scenario applies.

### 5. Conditions

A `when` on an application covers every command and step the component adds.
Until now, no tag condition could apply to `setup` and `cleanup` steps.
A hand-written step still takes no `when`.
Bajutsu filters while it folds a target's `setup` and `cleanup` into each scenario.
Tags are static, so the load fixes the filtered list.

A command entry inside a component may carry its own `when`.
Both conditions must then match.

Some components hold `beforeRun` or `afterRun`.
A `when` on such an application needs a non-empty `tags`, as a run-level entry does today.

A scenario that reads a component's output must match the application's `when`.
The load checks that, as it does for a command entry.

A scenario may set `preconditions.inheritSetup: false`.
For such a scenario, the parts of a target behave as follows.

| Part | Written directly | Added by a component of the target |
|---|---|---|
| `setup` | Skipped | Skipped |
| `beforeScenario`, `afterScenario` | Skipped | Skipped |
| `cleanup` | Runs, as today | Skipped |
| `beforeRun`, `afterRun` | Run | Run, and outputs stay readable |

The one difference is `cleanup`.
A component's teardown reads what its own preparation made.
A teardown without its preparation would stop with an error.
Components that the scenario applies itself still run.

### 6. Reports and command policy

A report names the component file beside each command and step it added.
Bajutsu checks each added command before device work, as it checks direct entries.
The rules come from the
[worker command policy](../../docs/self-hosting.md#which-config-commands-a-worker-runs-be-0492).
Two files bear on a command: the applying file and the component file.
The policy uses the stricter mode of the two sources.
Content that a client uploads thus never gains the mode of the config.

### Work breakdown

1. The file model, the `use` list, parameter substitution, and path containment.
2. Expansion for targets and scenarios, with order, templates, and the run-list refusal.
3. Names: per-target scopes, aliases, token resolution, and the command environment.
4. The application-level `when` and the `inheritSetup: false` rule.
5. Reports, command policy, the sandbox check, and delivery of component files to workers.
6. Schemas and `codegen` for the expanded `setup` and `cleanup`.
7. Documentation for configuration, scenarios, and the grammar of the scenario language.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Add `before` and `after` to the step component | A step component expands where `use` stands. A command runs before the app launches. The written position and the run order would disagree |
| Reuse one command list at a time | A smaller change. Both halves still apply separately, so the first problem in Motivation stays open |
| Refuse a second application of one component | Simpler, with no alias. Two users with different plans would need two copies of the file |
| Allow no `when` on an application | Each command would carry its own condition. Steps could carry none, and the pair would break |
| Put direct entries outside components | Fits a direct entry that starts a server for a component to seed. This item puts the shared component outside, so entries of one target or scenario run inside its preparation. A suite can move the server start into a component and list it first |
| Extend target templates | Templates take no parameters, replace lists, and do not reach scenarios |
| Keep component names unique across the run | No new scope. Two targets that share a template would stop the run |
| Name the target inside a token | Reads values of other targets. The form collides with the alias form and needs a further rule |
| Let a component apply another component | Layers a shared base. It adds cycle detection and rules for order and aliases |
| Allow `interrupts` in a component | Ships dialog handling with a feature. Those handlers are no pair of preparation and teardown |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Component file and `use` list
- [ ] Expansion for targets and scenarios
- [ ] Name scopes and aliases
- [ ] Application-level `when` and `inheritSetup: false`
- [ ] Reports, command policy, and delivery to workers
- [ ] Schemas and `codegen`
- [ ] Documentation

## References

- [Configuration: external commands](../../docs/configuration.md#external-commands-around-a-run-and-each-scenario-hooks-be-0491)
- [Scenarios: `setup` and `cleanup`](../../docs/scenarios.md#setup--cleanup-setup-and-teardown-phases)
- [Scenarios: components](../../docs/scenarios.md#components-use--reusable-steps)
