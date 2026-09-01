**English** · [日本語](ja/ai-boundary.md)

# What uses Claude, and what doesn't

> The canonical answer to "which parts of Bajutsu reach a model, and which run with nothing
> configured at all". This is a first-class, tested property of the tool
> ([BE-0101](../roadmaps/BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md)),
> the developer-experience companion to the "your AI, your key, your data" guarantee on the other
> side of the line ([self-hosting](self-hosting.md),
> [BE-0047](../roadmaps/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)).

Related: [cli](cli.md) · [concepts](concepts.md) · [recording](recording.md) · [self-hosting](self-hosting.md)

---

## The one line that matters

Bajutsu draws a hard architectural line: the deterministic `run` / CI gate calls **no model**, and
only the Tier-1 authoring / investigation paths reach Claude. The axis is **whether a path invokes
Claude at all** — not whether a particular credential is present. Claude can be reached four ways
(the Anthropic API, Amazon Bedrock, the Anthropic CLI `ant` under a browser-based OAuth/SSO
sign-in, or the Claude Code CLI `claude` billing a Claude Pro/Max/Console seat), so "needs an API
key" is the wrong unit; "uses Claude" is the honest one, and it stays correct as providers are
added.

Everything on the **Claude-free** side runs with **zero configuration** — no credential, no `.env`,
no login, no AI runtime. Clone the repo and it works immediately.

## The split

| | Command / path | What it does |
|---|---|---|
| **Claude-free** (zero-config) | `run` | run scenarios deterministically — pass/fail is machine-only, never a model |
| | `doctor` | check the environment is runnable and score the current screen |
| | `codegen` | generate native XCUITest source from a scenario |
| | `trace` | print a text timeline over a saved run |
| | `lint` / `schema` | validate scenarios / emit the JSON Schema, without running |
| | `approve` | promote a run's screenshots to visual baselines |
| | `audit` / `coverage` / `impact` / `flakiness` | score a scenario's determinism / map id-namespace coverage / flag the steps a source change likely affects / rank scenarios by verdict flips (all advisory) |
| | `report` / `export` / `stats` | re-render a finished run / archive it / aggregate a run directory into the whole-suite trend |
| | `mcp` / `worker` | serve run/doctor as MCP tools / run a background job worker |
| | `serve` | the local web UI — boots with nothing configured; its Claude tabs degrade gracefully |
| | `triage` | diagnose a failed run with the rule-based agent (no `--ai`) |
| **Uses Claude** | `record` | author a scenario by driving the app with Claude |
| | `crawl` | explore an app autonomously with Claude to build a screen map |
| | `triage --ai` | diagnose a failed run with Claude instead of the rule-based agent |

The classification is at the granularity of the **path**, not the command name: `triage` is
Claude-free, and a single `--ai` flag flips it onto the Claude path. `run` is Claude-free with **no
flag-gated exception at all** since
[BE-0402](../roadmaps/BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback.md).
Its alert guard (`--system-alert-handling`, on by default per scenario) used to be one: where the
native SpringBoard path could not name a prompt, the guard read a screenshot with a model and tapped
where it was told. That fallback is gone from `run`, so the guard needs no credential, consults none,
and leaves a prompt it cannot name alone — naming it in the blocked step's own failure instead.

This split is the [Tier-1 / Tier-2 boundary](concepts.md) made visible; nothing here puts a model on
the `run` / CI gate.

## Where you see it

The classification is defined once (in `bajutsu/capabilities.py`) and consumed everywhere, so the
surfaces can never disagree:

- **`bajutsu --help`** groups every top-level command under *Claude-free (zero-config)* or *Uses
  Claude*.
- **`doctor`** reports Claude readiness as a separate, clearly **optional** section: a host with no
  AI setup is still graded `Ready` for the deterministic path, with Claude shown as a distinct
  "not configured (optional)" line — never conflated with a blocking problem.
- **`serve`** shows the Claude tabs (`record` / `crawl`) but disables them with an inline
  explanation when Claude is unreachable, pointing at the in-UI key field; they re-enable the moment
  a key is set, Bedrock is configured, or the `ant` / `claude` CLI is signed in.

## Installing the Claude paths

The split is a packaging boundary too, not only a runtime one
([BE-0111](../roadmaps/BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency.md)):
the AI software development kit (SDK) is an opt-in extra, so the base install carries no AI
dependency at all.

- `pip install bajutsu` — the deterministic authoring / running paths (`run`, `doctor`, `lint`,
  `codegen`, `trace`, `approve`, and the rest of the Claude-free column above). No AI SDK is
  installed, and nothing here reaches a model.
- `pip install bajutsu[ai]` — adds the Anthropic SDK for the Claude paths (`record`, `crawl`,
  `triage --ai`) under the API-key, Bedrock, or `ant` provider. Use
  `bajutsu[bedrock]` instead for the Amazon Bedrock provider; it layers the Bedrock variant onto the
  same SDK. The `claude-code` provider needs neither extra — it shells out to the external `claude`
  CLI rather than the SDK.

Contributors get every extra at once through `uv sync --group dev`, so the gate keeps testing the
Claude paths regardless — the AI-free guarantee is about the *base* install, not about dropping test
coverage.

## Reaching Claude, when you want it

Any one of these mechanisms satisfies the "uses Claude" paths (details in [self-hosting](self-hosting.md) and
[recording](recording.md)):

- **Anthropic API** — set `ANTHROPIC_API_KEY` (or the env var named by `ai.keyEnv`).
- **Amazon Bedrock** — the standard AWS credential chain plus a provider-prefixed model id (`ai.model`
  or `$BAJUTSU_BEDROCK_MODEL`).
- **Anthropic CLI (`ant`)** — set `ai.provider: ant` and run `ant auth login`, drawing on a Claude
  Pro/Max/Console seat instead of a key (BE-0163).
- **Claude Code CLI (`claude`)** — set `ai.provider: claude-code` and sign in (`claude setup-token`,
  or an interactive login; a headless host sets `CLAUDE_CODE_OAUTH_TOKEN` instead), also drawing on a
  Claude Pro/Max/Console seat rather than a key (BE-0176).

Which mechanism authenticates is config (per
[BE-0047](../roadmaps/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) /
[BE-0053](../roadmaps/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md) /
[BE-0163](../roadmaps/BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md) /
[BE-0176](../roadmaps/BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md)); the
classification above is the same regardless of which you pick.

## Turning every Claude path off, in one line

The mechanisms above are opt-in, so a project that configures none of them reaches no model. That
silence is still an accident of the environment rather than a statement in the repository: nothing a
reviewer reads records the intent, and a key exported to author one scenario with `record` is
enough to put `record` and `crawl` on a model in every later shell. Setting the provider
to `none` states the policy instead
([BE-0394](../roadmaps/BE-0394-ai-provider-none-kill-switch/BE-0394-ai-provider-none-kill-switch.md)):

```yaml
defaults:
  ai:
    provider: none      # no AI path may run in this repository
```

With that line committed, the three authoring and investigation commands — `record`, `crawl`, and
`triage --ai` — exit with a message naming the setting rather than starting, and no code path can
construct an AI backend at all. `run` is unaffected either way: BE-0402 removed the one path of its
own that ever reached a model, so it neither needs the switch nor notices it. A configured
`ai.provider` outranks `$BAJUTSU_AI_PROVIDER`, so the setting also holds on a continuous-integration
runner whose environment nobody controls. The
[configuration guide](configuration.md#ai-provider-ai-be-0047) covers the precedence rules and the
one exception, `serve`'s Settings dropdown, which never offers `none`.
