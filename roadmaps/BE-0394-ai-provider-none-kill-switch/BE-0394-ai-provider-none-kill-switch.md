**English** · [日本語](BE-0394-ai-provider-none-kill-switch-ja.md)

# BE-0394 — Add a none provider that disables every AI path, including the vision alert guard

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0394](BE-0394-ai-provider-none-kill-switch.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0394") |
| Topic | AI provider configuration |
| Related | [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md), [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md), [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu reaches Claude from several paths. `record` and `crawl` author scenarios, `triage`
investigates a failure, and the reactive system-alert guard clears an operating-system prompt that
the application's own accessibility tree cannot see. The guard is the one path that runs inside
`run`, the deterministic gate. On the iOS XCUITest backend the guard clears the prompt natively, and
only where the native path cannot act does it fall back to sending a screenshot of the device to
Claude vision and tapping the pixel the model names.

A project that does not want a screenshot of the device to leave the machine has no way to say so.
Turning the whole setting off with `systemAlertHandling: false` also gives up the deterministic
native path, which is the half worth keeping. The one remaining lever is to leave the provider's
credential out of the environment, and an absent credential is an accident of the shell rather than
a statement in the repository.

This proposal registers a provider named `none` in the provider registry that
[BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) introduced.
Writing `ai: { provider: none }` in the configuration disables every AI path at once: the vision
fallback becomes a no-op while the native path keeps clearing prompts, the commands that need Claude
exit with a message naming the setting, and no code path can construct an AI backend at all, because
the provider's factory raises instead of returning one. The contribution is not a capability the
tool lacks — an unset key already produces most of the same behavior. The contribution is turning
that behavior from an environmental absence into a committed, reviewable, fail-closed statement.

## Motivation

The reactive guard fires while a step or a guarded wait is blocked. `AlertGuardConfig`
(`bajutsu/orchestrator/types.py:304`) first probes the native path, which reads the alert's buttons
through the SpringBoard query that
[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) added and taps
whichever label the scenario's `rules` or `instruction` names. When no rule and no candidate label
resolves, or when the surface is one the query cannot enumerate at all — an action sheet, a web-view
dialog, any backend that does not advertise the `HANDLE_SYSTEM_ALERT` capability — the guard falls
back to `SystemAlertGuard.dismiss` (`bajutsu/agents/alerts.py`). The fallback captures a screenshot
of the device, sends the image to Claude with the `resolve_alert` tool, and taps the coordinate the
model returns.

Three properties of that fallback are ones a project may need to rule out, and none of them can be
ruled out today.

The first property is the screenshot itself. Bajutsu masks secrets in the *text* it sends to a
model: a `--alert-instruction` passes through the `Redactor` before it reaches the request
([BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)). The image beside
it cannot be masked the same way, and the guard's own source records the gap. A run against a
staging environment holding production data, or an engagement whose contract forbids sending
captured screens to a third party, needs the guarantee that the path does not exist, not the
observation that it rarely fires.

The second property is non-determinism. The fallback resolves a button by coordinate estimate, so
when the native path cannot answer, the model's tap decides what happens next. A run that would have
failed on a prompt nobody taught it to handle instead continues down whatever branch the tap opened.
Prime directive 2 puts determinism first, and a project may prefer a failure it can read to a rescue
it cannot reproduce.

The third property is cost and latency. The guard fires mid-wait, so the round trip sits on the
run's critical path; the locator picks Sonnet over Opus for that reason, as its own comment records.
Across a suite in continuous integration the round trips accumulate into both wall-clock time and
spend, on a fallback that a native-capable backend needs only rarely.

Neither lever available today rules any of the three properties out. `systemAlertHandling: false`
disables the guard as a whole, native path included, so a scenario that wants only the deterministic
half loses the half it wants and starts failing on the notification prompt the native path used to
clear.

The other lever is the credential. `_build_alert_locator` (`bajutsu/cli/_shared.py:450`) reads the
provider's credential gap and returns `None` when the provider cannot authenticate, so the fallback
no-ops and the native path keeps working — exactly the shape a project wants, reached by leaving
`ANTHROPIC_API_KEY` unset. An absence is a poor way to hold a policy. It lives nowhere in the
repository, so a reviewer reading the configuration cannot tell that screenshots are not sent, and a
fresh continuous-integration runner inherits the policy only by not having been set up. The absence
is also shared with every other AI path, which makes it fragile in the one direction that matters:
an engineer who exports the key to author a scenario with `record` re-enables the vision fallback
for every `run` in the same shell, silently, for as long as the shell lives.

Once the `none` provider ships, a reader can tell the change arrived by exporting the provider's key
and still finding no AI traffic. A `run` over a suite whose configuration carries `ai: { provider:
none }` records no event in the AI usage ledger
([BE-0196](../BE-0196-ai-usage-cost-ledger/BE-0196-ai-usage-cost-ledger.md)) and prints the alert
guard's own note naming `ai.provider: none`, while the same run without the setting spends tokens on
every guarded wait the native path could not resolve.

## Detailed design

### The `none` provider

```yaml
defaults:
  ai:
    provider: none      # no AI path may run; the deterministic native alert path is unaffected
```

A new adapter module, `bajutsu/ai/disabled.py`, registers an `Adapter` under the name `none`
alongside the built-in adapters in `bajutsu/ai/registry.py`. It is the smallest adapter the contract
admits, and it is deliberately inert.

| Hook | Behavior |
|---|---|
| `factory` | raises `RuntimeError`, naming `ai.provider: none` as the reason; no `AiBackend` is ever constructed |
| `credential_gap` | returns the token `"ai-disabled"` |
| `announce` | replaces the default provider-and-model line with `🤖 AI: disabled (ai.provider: none)`, so no surface that discloses the provider names a model that will never be used |

The raising factory is what makes the setting fail closed rather than merely quiet. `create_backend`
is the single construction entry point every agent reaches through `ClaudeBackedAgent`
(`bajutsu/agents/claude_backed.py`), so a call site that skips the credential check gets an
exception at construction instead of a silent round trip. Even the alert guard's own best-effort
`except Exception`, which exists so a locator failure can never crash a run, cannot turn the raise
into a request: the guard would log a warning and tap nothing, and nothing would have been sent.

### Why the credential-gap seam, and not a new configuration field

Every consumer of `credential_gap` already fails closed on a non-`None` value, so registering the
provider gives each surface the right behavior without a new branch of its own.

| Site | Behavior under `provider: none` |
|---|---|
| `_build_alert_locator` (`bajutsu/cli/_shared.py:450`) | returns `None`, so the vision fallback no-ops and the native path is untouched |
| `_require_ai_credential` (`bajutsu/cli/_shared.py:172`) | a clean exit 2, so `record`, `crawl`, and `triage --ai` refuse to start rather than degrading |
| `doctor`'s `_claude_readiness` (`bajutsu/cli/commands/doctor.py:286`) | the optional Claude line reads as not configured, never the ✗ of a broken environment |
| `serve` settings (`bajutsu/serve/operations/config.py:508`) | unchanged — `provider_info` resolves the provider from the organization's saved selection rather than the target config, so `claudeAvailable` never sees this setting (see *`serve`* below) |
| `serve` enrichment and triage (`bajutsu/serve/operations/enrich.py:64`, `bajutsu/serve/operations/triage.py:73`) | HTTP 400 before the job is dispatched |

An `ai.enabled: false` field would have to be read at each of those sites in addition to the gap, and
would admit the contradictory configuration `{ enabled: false, provider: api-key }`. The provider
name carries the same information with one value and no new field.

### Messages

Two message tables gain a branch for the `"ai-disabled"` token, because the existing wording would
send the reader to fix an environment that is not broken.

| Site | Wording |
|---|---|
| `bajutsu/agents/availability.py`'s `message()`, rendered by `doctor` and `serve` | AI is disabled for this target (`ai.provider: none`) — select a provider to use the AI paths |
| `_credential_gap_message` (`bajutsu/cli/_shared.py:147`), printed by `record`, `crawl`, and `triage --ai` before they exit | the setting (`ai.provider: none`) and how to re-enable an AI path. Naming the *file* that sets it would need the `--config` path threaded into the message, because `Effective` (`bajutsu/config/effective.py:153`) carries the resolved config and no path back to its source; this item leaves that out |
| `_build_alert_locator`'s note, replacing the note about an unset key | the vision alert guard is off (`ai.provider: none`); on the iOS XCUITest backend the native path still clears common prompts |

### Precedence

`resolve_provider` (`bajutsu/agents/ai_config.py:78`) reads `ai.provider` from the configuration
first and falls back to `BAJUTSU_AI_PROVIDER` only when the configuration leaves the field unset. A
committed `provider: none` therefore cannot be undone by an environment variable, which is what
makes the setting hold on a continuous-integration runner whose environment nobody controls.
`BAJUTSU_AI_PROVIDER=none` remains available for a one-off run on a machine whose configuration says
nothing.

`_merge_ai` (`bajutsu/config/resolve.py:43`) merges `defaults.ai` with `targets.<name>.ai` field by
field, target winning, and the new provider needs no exception to that rule. A repository can
disable AI in `defaults` and re-enable it for one target, and the re-enabling is a line a reviewer
reads in the same file rather than an environment variable nobody sees.

### `serve`

`serve` needs one exception, and it is a subtraction rather than a special case: `none` is registered
but not **selectable**. The registry gains a `selectable_providers()` — `known_providers()` minus the
disabled provider — and `serve` validates against it at the three points that read `known_providers()`
today (`bajutsu/serve/operations/config.py:378` and `:392` on the load path, and the `set_provider`
write path at `:864`), so the Settings dropdown never offers it.

The reason is that an organization's Settings selection reaches a job only as an environment
variable. `provider_env` (`bajutsu/serve/operations/config.py:456`) emits `BAJUTSU_AI_PROVIDER` set to
the selection, and dispatch attaches that dict as the job's environment overlay. Because
`resolve_provider` reads the configuration first, an operator who picked `none` in the dropdown would
get no effect at all on a project whose configuration names an `ai.provider`: the jobs would keep
calling the model, and nothing would say so. A switch labeled off that silently stays on is the
failure mode the *Motivation* argues against for an unset key, and putting it in a dropdown would only
make it harder to notice. The kill switch is a statement a repository commits, not a per-organization
toggle.

One limitation follows, and this item accepts it rather than closing it. `provider_info`
(`bajutsu/serve/operations/config.py:491`) resolves the provider as the organization's saved selection
or, failing that, `resolved_provider()` with no `AiConfig` at all, and never reads `resolve(config,
target).ai` — unlike the enrichment and triage handlers, which do. A repository that sets
`ai.provider: none` therefore still reports `claudeAvailable: true`, and the web UI leaves the record
and crawl tabs enabled. The kill switch itself holds: a job started from one of those tabs is a
command-line invocation that resolves the repository configuration itself and exits 2 before reaching
a model. What is missing is only the pre-flight signal that would grey the tab out first. Teaching
`provider_info` to read the target's `ai` changes how `serve` reports reachability for every provider,
which this item's seam does not imply, so it belongs to a follow-up item.

### What this proposal does not change

The native alert path, the `systemAlertHandling` schema, and the resolution order that
[BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md) fixed all stay
as they are. In particular, an alert that the native path cannot handle stays a silent no-op under
`provider: none`, exactly as it is today under an absent credential. Turning that case into a step
failure would make the third motivation above — determinism — visible in the verdict, but it also
turns runs red that a vision tap used to carry, so it is a separate decision with its own migration.
A later item can take it up; the switch proposed here does not depend on it.

The switch is also global by design, not per scenario. A per-scenario `systemAlertHandling.vision:
false` would answer the alert-guard motivation alone and leave `record`, `crawl`, and triage
reachable, which is the opposite of what a policy against sending screenshots needs.

### Work breakdown

The units below are mutually exclusive and cover the design in full; the *Progress* checklist
mirrors them one for one.

1. **The adapter.** `bajutsu/ai/disabled.py` with the three hooks, registered in
   `_ensure_builtins` (`bajutsu/ai/registry.py`) so `known_providers()` reports it. Unit tests:
   `credential_gap` returns the token, `create_backend` raises, and `known_providers()` contains the
   name.
2. **The `serve` exclusion.** `selectable_providers()` in `bajutsu/ai/registry.py`, and the three
   `known_providers()` call sites in `bajutsu/serve/operations/config.py` reading it instead. A test
   that `set_provider` rejects `none` with a 400, so the dropdown cannot offer a switch that would
   reach a job only as an environment variable.
3. **The messages.** The `"ai-disabled"` branch in `bajutsu/agents/availability.py` and in
   `_credential_gap_message`, plus the reworded note in `_build_alert_locator`.
4. **The run-path test.** With the key present in the environment and `provider: none` in the
   configuration, `run` builds no locator, the guard's `vision` handler no-ops, the native path still
   clears an alert, and the usage ledger stays empty.
5. **The refusal test.** `record`, `crawl`, and `triage --ai` exit 2 naming the setting, and
   enrichment and triage return HTTP 400 before dispatch. The same test pins `serve`'s settings
   endpoint at `claudeAvailable: true` under a target config of `provider: none`, so the limitation
   the *`serve`* section records stays a documented fact rather than drifting into a false claim.
6. **Documentation.** The `ai:` section of `docs/configuration.md` and its `docs/ja/` mirror gain the
   provider, the precedence rule, and the `serve` exclusion; `docs/ai-boundary.md` and its mirror
   record that a repository can now state "no AI path runs here" in one line.

## Alternatives considered

| Alternative | Sketch | Why not adopted |
|---|---|---|
| `systemAlertHandling.vision: false` per scenario | A boolean beside `rules` and `instruction`, riding the BE-0177 resolution order | Answers the alert guard only. A policy against sending screenshots to a third party has to cover `record`, `crawl`, and triage as well, and a per-scenario switch cannot state it. Worth revisiting if a project ever wants vision off for one scenario and on for another, which no motivation here asks for |
| `ai.enabled: false` | A new boolean on `AiSettings` | Admits `{ enabled: false, provider: api-key }`, a configuration with two answers, and needs a new branch at every site that reads the credential gap. The provider name already carries the same information |
| `ai.provider: disabled` | The same adapter under a longer name | `none` reads as the natural opposite of a provider name and matches the vocabulary of other tools. It parses as the string `none` in YAML, not as a null, so the risk of confusion is a reader's rather than the parser's. Keeping one spelling avoids an alias nobody needs |
| A command-line flag alone (`--no-vision-alert-handling`) | A `run` flag, no configuration key | Leaves the policy out of the repository, so every call site must remember it and a reviewer cannot see it. It also cannot reach the `serve` job paths, which take no flags from the user |
| Document "unset the key" as the supported answer | No code change; a docs section explaining the existing behavior | Keeps the failure mode the motivation names: a key exported for `record` silently re-enables the fallback for `run`, and nothing in the repository records the intent |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] The `none` adapter and its registration, with unit tests
- [ ] `selectable_providers()` and the `serve` exclusion, with a rejected `set_provider` test
- [ ] The `"ai-disabled"` message branches and the reworded alert-guard note
- [ ] The `run`-path test: no locator, no ledger event, native path unaffected
- [ ] The refusal tests for `record`, `crawl`, `triage --ai`, and enrichment, plus the pinned
      `claudeAvailable: true` limitation
- [ ] Documentation in `docs/configuration.md` and `docs/ai-boundary.md`, both languages
- [ ] Reciprocal `Related` rows in BE-0047, BE-0104, and BE-0315, once CI allocates this item's id

## References

- [BE-0104 — Vendor-neutral AI backend](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) — the provider registry this item extends.
- [BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) — the `ai` block, the `keyEnv` rule, and the credential gap this item reuses.
- [BE-0315 — Native reactive alert handling](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md) — the deterministic path that keeps working when AI is off.
- [BE-0382 — Per-prompt rules for the system-alert guard](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md) — the rules the native path answers a prompt with.
- [BE-0196 — Record AI token usage and cost](../BE-0196-ai-usage-cost-ledger/BE-0196-ai-usage-cost-ledger.md) — the ledger the verifiable outcome reads.
- [`docs/configuration.md`](../../docs/configuration.md) — the `ai:` block and its resolution order.
- [`docs/ai-boundary.md`](../../docs/ai-boundary.md) — which paths may reach a model, and which may not.
