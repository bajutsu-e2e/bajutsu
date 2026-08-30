**English** · [日本語](BE-0401-system-alert-handling-dsl-consolidation-ja.md)

# BE-0401 — Consolidate the systemAlertHandling DSL into one key per answer path

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0401](BE-0401-system-alert-handling-dsl-consolidation.md) |
| Author | [@akiramatsuda](https://github.com/akiramatsuda) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0401") |
| Implementing PR | [#1810](https://github.com/bajutsu-e2e/bajutsu/pull/1810), [#1822](https://github.com/bajutsu-e2e/bajutsu/pull/1822) |
| Topic | Scenario authoring features |
| Related | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0317](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling.md), [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md), [BE-0327](../BE-0327-rename-alert-handling-to-system-alert-handling/BE-0327-rename-alert-handling-to-system-alert-handling.md), [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md) |
<!-- /BE-METADATA -->

## Introduction

A scenario's `systemAlertHandling` setting clears an operating-system prompt that the application's
own accessibility tree cannot see — the "Allow Notifications" permission request, App Tracking
Transparency (ATT), the cross-process paste consent — by tapping one of the prompt's buttons
reactively, wherever the prompt interrupts a run. The setting reaches the buttons over two different
execution paths: a deterministic native path that queries SpringBoard for the alert's button labels
and taps one of them, and an artificial-intelligence (AI) vision fallback that reads a screenshot
when the native path cannot name a button. Four accumulated changes have left the setting's four
keys mapping onto those two paths through the *type* of a value rather than through the key an author
writes.

This proposal rebuilds the setting so that each key names exactly one answer path, and so that every
declaration an author writes stays in effect. It splits today's overloaded `instruction` key into
`labels` (the ordered button labels the native path taps) and `visionInstruction` (the free text only
the vision fallback reads), removes the `enabled` key in favor of the boolean shorthand the schema
already accepts, deletes the two deprecated spellings the setting still answers to, and replaces the
layering rules — four keys resolving four different ways, one of which deletes another layer's
declaration outright — with two rules chosen by the key's type.

Compatibility is deliberately not preserved. Every removed key fails to load with an error naming its
replacement, rather than being carried as an alias.

## Motivation

The setting's own runtime already has the shape this proposal gives the schema. `AlertGuardConfig`
(`bajutsu/orchestrator/types.py`) holds `labels` and `rules` for the native path and a separate
`vision` handler for the fallback, so the two paths are distinct data by the time the guard runs. The
conflation exists only in the on-disk schema, where one key, `instruction`, feeds both — and picks
which one by whether the author wrote a list or a string.

That type-driven dispatch inverts an author's stated intent, silently, on the default backend. Writing
`systemAlertHandling: { instruction: "tap Allow" }` reads as an instruction to accept the prompt. The
string form steers the vision fallback alone; the native path needs an exact label, so it ignores the
string and taps a default *dismissive* label instead. On the XCUITest backend — the default since
[BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md) — the
native tap happens first, so the prompt is refused. The file says accept and the run denies. Nothing
fails, because a scenario that never asserts on the permission has nothing to fail on. The list form
`instruction: ["Allow"]` grants correctly, so the two forms of one key produce opposite answers, and
the docstring at `bajutsu/scenario/models/scenario.py` already carries the warning in prose: the
string "is not a drop-in for the old vision-only behavior on such a backend".

A second defect is that one declaration deletes another. The target configuration's `rules` apply
only when neither the scenario nor `--alert-instruction` supplies its own `instruction`
(`bajutsu/cli/commands/run.py`). A scenario naming one literal button therefore drops every
project-wide rule, including rules answering prompts the scenario never mentioned.
[BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md) chose
that all-or-nothing precedence deliberately, to stop a project-wide rule from inverting a scenario's
own answer, and it is the strictly safer of the two available readings under today's schema. The cost
is that an author cannot predict, from the keys in front of them, which of their declarations will
run: adding `instruction` to a scenario silently disables a configuration block in a different file.

A third defect is a state the schema can express and the runtime ignores. `enabled` is an ordinary
key, so `{ enabled: false, rules: [...] }` parses, and the rules are discarded without a word. Two
spellings of "off" — the bare `false` and `{ enabled: false }` — coexist for the same reason.

Two deprecated spellings compound all of it. `alertHandling` and `dismissAlerts` are the setting's
former names, from [BE-0317](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling.md)
and [BE-0327](../BE-0327-rename-alert-handling-to-system-alert-handling/BE-0327-rename-alert-handling-to-system-alert-handling.md),
and both still parse behind a one-time notice, as do the `--alert-handling` and `--dismiss-alerts`
flags. One setting answers to three names in two surfaces.

A later reader can tell the contribution arrived by loading the inverting file above. Today
`systemAlertHandling: { instruction: "tap Allow" }` loads and, on the XCUITest backend, taps the
dismissive button. After this change the same file fails to load, with an error naming `labels` and
`visionInstruction`, and the file that replaces it taps "Allow". No combination of the setting's keys
taps the opposite button from the one the file names.

## Detailed design

### The schema

```
SystemAlertHandling ::= boolean                                   # true = on with the default policy, false = off
               | { rules?:             list(<SystemAlertRule>),   # answer by prompt name  — native path only
                   labels?:            list(string),              # answer by button label — native path, and a derived vision hint
                   visionInstruction?: string,                    # free text — AI vision fallback only
                   pollInterval?:      number }                   # native presence-query cadence, seconds, default 1

SystemAlertRule ::= { prompt: notifications | tracking | paste, choice: grant | deny }
```

```yaml
- name: onboarding — accept notifications, refuse tracking
  systemAlertHandling:
    rules:
      - { prompt: notifications, choice: grant }
      - { prompt: tracking,      choice: deny }
    labels: ["Not Now"]        # every alert no rule identifies
  steps:
    - tap:  { id: onboarding.start }
    - wait: { for: { id: home.title }, timeout: 10 }
```

`rules` and `pollInterval` keep the meaning
[BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md) and
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
gave them. `labels` is today's `instruction` list form under a name that states what its entries are.
`visionInstruction` is today's `instruction` string form, named for the one path that reads it.

Each key now reaches exactly one execution path. `rules` and `labels` steer the native path;
`visionInstruction` steers the vision fallback. `labels` additionally supplies the fallback with a
derived hint when no layer supplies a `visionInstruction` — the guard renders the candidates as "Tap
the button labeled one of, in order: …". A `visionInstruction` from any layer outranks that hint,
because a hint derived from labels is not a statement about the fallback and an explicit
`visionInstruction` is.

The hint derives from the **innermost layer that supplies `labels`**, not from the concatenation the
native path walks. The native path compares labels exactly, so a wider candidate list there only
lengthens a search that still taps a label some layer named. The fallback's instruction is free text
a locator interprets loosely, and a concatenated list hands it both answers at once: a target
configuration granting with `labels: ["Allow"]` under a scenario refusing with `labels: ["Don't
Allow"]` would render "…in order: Don't Allow, Allow", and the locator may take the second. That is
the hazard `rules` is kept out of the fallback for, one field over, so the same treatment applies —
the fallback hears one layer, and it is the most specific layer that spoke. `rules`
still contributes nothing to the fallback, for the reason BE-0382 recorded: every path that reaches
the fallback is one where no rule identified the alert, so a rule's label is by construction some
other prompt's answer, and passing it down would steer the locator to accept a prompt the scenario
never named.

### Applying an answer: two stages, and within a stage the more specific declaration wins

The guard reaches an alert's buttons over two stages, and each key belongs to exactly one of them.
The native path runs first and answers by itself when it can; the vision fallback runs only for what
the native path leaves unresolved.

```
native path:      rules  →  labels  (no layer supplies labels → the built-in dismissive labels)
                                │ nothing resolves
                                ▼
vision fallback:  visionInstruction  →  the hint derived from labels  →  the locator's own default
```

Within the native stage, a rule names a prompt and a label names a button, so the more specific
declaration is consulted first. The order extends the `rules`-then-`instruction` precedence BE-0382
established, rather than replacing it.

The built-in dismissive labels stand in for an absent `labels` list rather than extending a supplied
one. A scenario that names its buttons and meets an alert carrying none of them resolves nothing
natively and drops to the fallback, instead of tapping a button the author never named — the same
reason an ambiguous selector fails rather than tapping its first match. A candidate that appears on
two buttons at once is skipped for the next candidate, since it identifies no single button; that is
`pick_alert_label`'s existing rule and it does not change.

Within the native stage, specificity — not the layer a declaration came from — settles a conflict. A target
configuration rule for the tracking prompt answers the tracking prompt even in a scenario carrying
its own `labels`, because the rule names that prompt and the labels name no prompt at all. Both
declarations stay in effect: the rule answers tracking, the scenario's labels answer everything else.
A scenario overrides a configuration rule at the same specificity, by writing its own rule for that
prompt — an override whose reach is the one prompt the author named, rather than every rule in the
configuration.

### Composing the layers: the key's type decides

A setting reaches a run from as many as three layers — the target configuration
([BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md)), the
command line, and the scenario. Two rules cover every key, chosen by whether the key holds a list or
a scalar:

| Key | Type | Composition |
|---|---|---|
| `rules` | list | concatenated, innermost layer first: scenario, then target |
| `labels` | list | concatenated, innermost layer first: scenario, then command line, then target |
| `visionInstruction` | scalar | precedence, innermost layer wins: scenario, else command line, else target |
| `pollInterval` | scalar | precedence, innermost layer wins: scenario, else command line, else target |
| on / off | scalar | precedence: `--system-alert-handling` or `--no-system-alert-handling`, else scenario, else target, else on |

`rules` names only two layers because no flag supplies rules: an entry is a prompt paired with a
choice, which a single flag value cannot carry legibly. Every other key reaches all three layers.

A list composes because concatenation preserves both layers' entries: the scenario's answers are
tried first, and the target's remain reachable for whatever the scenario did not answer. A scalar
holds one value, so composition is not available and the innermost layer wins. The two rules replace
the four the setting resolves by today, and no rule deletes another layer's declaration.

Concatenating `rules` keeps the shadowing BE-0382 defined: matching returns on the first rule whose
prompt it identifies, so a scenario rule and a target rule for the same prompt resolve to the
scenario's. A duplicate prompt *within* one list stays a parse error, since silently taking the first
of two would hide an authoring mistake.

For the policy keys, the command-line layer keeps the position it holds today, between the scenario
and the target configuration: a flag is a deliberate override for one run, and a scenario file is more
specific still. The on/off flag is the exception, and stays the outermost override it is today, since
`--no-system-alert-handling` exists to disable the guard for a run whatever the files say.

### One notice where a configuration rule reaches a scenario that answers for itself

Composition restores a case BE-0382 removed on purpose: a target rule now answers its prompt inside a
scenario that supplies its own `labels` or `visionInstruction`. The behavior is correct under the
specificity ladder, and BE-0382's objection to it was that it is *silent* — a project-wide edit
changes a scenario that names no prompt.

The guard therefore prints a notice, at construction, naming the scenario and each target rule that
will answer for it. The notice keeps the composition and removes the silence. It rides `warn_once`
(`bajutsu/deprecations.py`), so it costs no device work and reaches no model.

`warn_once` dedupes by its code for the whole process, and the guard is built once per scenario, so
the code is the scenario's name together with the rule's prompt. Keyed on the prompt alone, a run of
many scenarios would warn for the first affected scenario and pass over the rest in the silence this
section removes. That key is also what makes the notice testable: a two-scenario run raises two
notices, one per affected scenario, rather than one for both. The module holding `warn_once` is
scoped by its own docstring to deprecation notices, and a rule reaching a scenario is not a
deprecation, so the same change widens that docstring to the surface the module actually covers.

### Removing `enabled`: the type carries on and off

The bare boolean becomes the only way to say on or off, and the mapping form always means on. The
scenario field's type states it:

```python
system_alert_handling: Literal[False] | SystemAlertHandling | None
```

`false` is off, a mapping is on and carries the policy, `true` normalizes to an empty mapping, and an
absent key inherits the layer above. The state `{ enabled: false, rules: [...] }` — a policy the
runtime discards without a word — is no longer representable, which is the same reason an ambiguous
selector fails rather than tapping its first match.

### Failing loudly on a removed key or an empty value

The scenario model sets `extra="forbid"`, so a removed key already fails to load — but with Pydantic's
generic "extra fields not permitted", which names no replacement. Each removed key is therefore
matched explicitly and rejected with the key that replaces it:

| Removed | Error names |
|---|---|
| `instruction` | `labels` for the list form, `visionInstruction` for the string form |
| `enabled` | the boolean shorthand |
| `alertHandling`, `dismissAlerts` | `systemAlertHandling` |

Three values that are silently normalized today become errors, for the same determinism-first reason.
An empty `labels` list and an empty `visionInstruction` fall through to the layers above and then to
the default dismissive policy, so a typo answers the opposite of what the author wrote. An empty
string among a `labels` list is dropped and the surviving entries stay in effect, which hides the typo
instead of inverting the answer — and drops to the dismissive default when every entry is empty. A
non-positive `pollInterval` already raises, and stays as it is.

### The command-line surface

The flags mirror the schema one for one:

| Flag | Replaces |
|---|---|
| `--alert-labels "Allow,OK"` | new; the native half of `--alert-instruction` |
| `--alert-vision-instruction` | `--alert-instruction`, renamed for the path it steers |
| `--alert-poll-interval` | new; `pollInterval` had no flag |
| `--system-alert-handling` / `--no-system-alert-handling` | unchanged |
| — | `--alert-handling` and `--dismiss-alerts`, deleted |

`record` and `crawl` take the renamed `--alert-vision-instruction` as well. Both build a vision-only
guard (`_build_alert_guard`, `bajutsu/cli/_shared.py`), so the new name describes those two commands
more accurately than the old one did.

The web interface derives its launch arguments from the command line's own option metadata
([BE-0134](../BE-0134-serve-cli-flag-mirror-drift/BE-0134-serve-cli-flag-mirror-drift.md)), and a
test asserts that every flag is classified, so each added and deleted flag is classified in the same
change. The two request-body aliases its dispatch layer accepts, `alertHandling` and `dismissAlerts`
(`bajutsu/serve/operations/dispatch.py`), are deleted with the schema aliases they mirror.

### The scenario schema version stays at 1

`SCHEMA_VERSION` (`bajutsu/scenario/models/scenario.py`) gates a file that declares a version newer
than the running bajutsu, and its own rule is to bump "only for a load-breaking change: removing a
required field's meaning, or a change an older bajutsu would misinterpret rather than merely reject".
A file written against this proposal carries keys an older bajutsu does not know, and `extra="forbid"`
rejects it — never misinterprets it. The version therefore stays at 1.

### Work breakdown

1. **Schema.** `SystemAlertHandling` gaining `labels` and `visionInstruction` and losing `enabled` and
   `instruction`, the `Literal[False]` union on both the scenario and the target configuration
   (`bajutsu/scenario/models/scenario.py`, `bajutsu/config/schema.py`), the explicit rejection of each
   removed key, and the empty-value validators.
2. **Layering.** `_alert_guard_factory` and `_apply_system_alert_handling`
   (`bajutsu/cli/commands/run.py`) composing lists by concatenation and scalars by precedence, the
   deletion of the target-rule suppression, the notice, and `_vision_instruction` taking the two
   fields rather than one.
3. **Command line.** The added, renamed, and deleted flags on `run`, `record`, and `crawl`, the
   deletion of `resolve_system_alert_handling_flag`'s alias merging (`bajutsu/cli/_shared.py`), and the
   web interface's flag classification and its two request-body aliases.
4. **Documentation.** The `systemAlertHandling` sections of `docs/scenarios.md`, `docs/dsl-grammar.md`,
   `docs/configuration.md`, `docs/cli.md`, `docs/cookbook.md`, and `docs/recording.md`, each with its
   `docs/ja/` mirror, plus a migration table for the removed keys.
5. **Demonstration scenarios.** The showcase scenarios and configuration that carry the old keys.
6. **Tests.** The deterministic suite covering each unit above.

### Machine-checkable outcome

The gate is `make check`, and every behavior above is covered by unit tests in the Simulator-free
suite: each removed key raises an error naming its replacement; an empty `labels`, an empty label, and
an empty `visionInstruction` raise; `{ enabled: false }` no longer parses while bare `false` still
disables the guard; the guard factory concatenates rules and labels across the scenario, flag, and
target layers and resolves the two scalars by precedence; a target rule answers its prompt in a
scenario carrying its own labels, and prints the notice once; and the vision fallback receives the
scenario's `visionInstruction` when given, a hint derived from the innermost layer's `labels` when
not, and nothing from `rules` either way. No assertion depends on a model, and no new call reaches one.

## Alternatives considered

**Keep `instruction` and warn when its string form is used on a native-capable backend.** A warning
costs one branch and breaks no file. It is rejected because the trap is the key's shape: an author
still has to know that a list and a string go to different paths, and a warning arrives after the file
is written rather than preventing it. Naming the paths in the keys makes the mistake unavailable.

**Merge `rules` and `labels` into one ordered `answers` list**, each entry either a prompt-and-choice
or a literal label. One list would need one composition rule and one application rule, which is
simpler than the pair this proposal defines. It is rejected because it hands safety back to the
author's ordering: a `{ label: "Allow" }` entry placed above a `{ prompt: tracking, choice: deny }`
entry grants tracking, which is exactly the silent inversion BE-0382 created `rules` to remove.
Keeping the two keys separate makes "a prompt name outranks a button label" a property of the schema
rather than a discipline the author must maintain.

**Keep the target-rule suppression BE-0382 defined.** The suppression is the safer of the two
readings available today, and reversing it does let a project-wide rule change a scenario that names
no prompt. It is rejected because the price is the property this proposal exists to establish: with
the suppression in place, an author cannot tell from the keys in front of them which of their
declarations will run. The specificity ladder gives the same protection at a finer grain — a scenario
overrides the one rule it disagrees with, by name — and the construction-time notice removes the
silence that was BE-0382's actual objection.

**Carry the removed keys as deprecated aliases.** The repository has done exactly that twice for this
setting, in BE-0317 and BE-0327, and both aliases are still live. It is rejected here because an alias
for `instruction` cannot be neutral: the key's meaning splits by type, so an alias would have to keep
sending a string to the vision path and a list to the native one — preserving the very defect this
proposal removes. Once `instruction` must break, carrying the other three names would only add to a
migration the same change already asks for.

**Drop `visionInstruction` entirely and let the fallback run on `labels` alone.** The fallback would
then need no free-text key, and every remaining key would be deterministic. It is rejected because the
fallback exists precisely for alerts the native path cannot name, including every alert on a backend
with no native path at all, where an author who cannot supply an exact label can still describe the
intent. Removing the key would leave those alerts with no steering but the built-in default.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Schema — `labels`, `visionInstruction`, the `Literal[False]` union, the removed-key errors, and the empty-value validators.
- [x] Layering — list concatenation, scalar precedence, the deleted suppression, and the notice.
- [x] Command line — the added, renamed, and deleted flags, and the web interface's mirror.
- [x] Documentation — the six pages and their Japanese mirrors, plus the migration table.
- [x] Demonstration scenarios — the showcase scenarios and configuration.
- [x] Tests — the deterministic suite covering each unit above.

- 2026-08-30 — Shipped in one change: the schema split, the type-driven layering with its
  construction-time notice, the command-line and web-interface surfaces, the six documentation pages
  with their Japanese mirrors, the showcase scenarios, and the deterministic tests.

## References

- [BE-0315 — Make the reactive alert guard deterministic and native, reusing BE-0316's SpringBoard path](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
  — the native path and the AI-vision fallback whose separation this proposal moves into the schema,
  and the `instruction` key it splits.
- [BE-0382 — Let the reactive system-alert guard answer each covered prompt by its own rule](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md)
  — the `rules` key this proposal keeps unchanged, the `rules`-then-`instruction` precedence the
  specificity ladder extends, and the target-rule suppression it reverses.
- [BE-0316 — Explicit mid-flow step for iOS permission-prompt alerts](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)
  — the proactive step whose `prompt` and `choice` vocabulary `rules` reuses.
- [BE-0320 — Make the iOS system-alert button selector deterministic under a non-English Simulator locale](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
  — the locale-keyed label table a rule resolves through, and the reason a literal label list is
  language-bound.
- [BE-0177 — Per-target config defaults for run-behavior settings](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md)
  — the target-configuration layer whose composition with the scenario this proposal redefines.
- [BE-0317 — Rename the dismissAlerts guard to alertHandling to match its grant-or-dismiss behavior](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling.md)
  — the first rename, and the `dismissAlerts` alias this proposal deletes.
- [BE-0327 — Rename the alertHandling guard to systemAlertHandling to name what it handles](../BE-0327-rename-alert-handling-to-system-alert-handling/BE-0327-rename-alert-handling-to-system-alert-handling.md)
  — the second rename, and the `alertHandling` alias this proposal deletes.
- [BE-0134 — Eliminate serve-to-CLI flag-mirror drift](../BE-0134-serve-cli-flag-mirror-drift/BE-0134-serve-cli-flag-mirror-drift.md)
  — the contract that makes the web interface follow each added and deleted flag.
- [`docs/scenarios.md`](../../docs/scenarios.md) — the `systemAlertHandling` reference this proposal
  rewrites.
