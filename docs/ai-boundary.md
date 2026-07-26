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
Claude at all** — not whether a particular credential is present. Claude can be reached three ways
(the Anthropic API, Amazon Bedrock, or the Anthropic CLI `ant` under a browser-based OAuth/SSO
sign-in), so "needs an API key" is the wrong unit; "uses Claude" is the honest one, and it stays
correct as providers are added.

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
| | `audit` / `coverage` | score a scenario's determinism / map id-namespace coverage (advisory) |
| | `report` / `export` | re-render or archive a finished run |
| | `mcp` / `worker` | serve run/doctor as MCP tools / run a background job worker |
| | `serve` | the local web UI — boots with nothing configured; its Claude tabs degrade gracefully |
| | `triage` | diagnose a failed run with the rule-based agent (no `--ai`) |
| **Uses Claude** | `record` | author a scenario by driving the app with Claude |
| | `crawl` | explore an app autonomously with Claude to build a screen map |
| | `triage --ai` | diagnose a failed run with Claude instead of the rule-based agent |
| | `run --alert-handling` | the alert guard — Claude clears an OS prompt that blocked a step |

The classification is at the granularity of the **path**, not the command name: `triage` is
Claude-free, and a single `--ai` flag flips it onto the Claude path; `run` is Claude-free, and the
alert guard (`--alert-handling`, on by default per scenario) is its Claude path. When there is no
credential, that guard **degrades to a no-op** — it never blocks a deterministic run.

This split is the [Tier-1 / Tier-2 boundary](concepts.md) made visible; nothing here puts a model on
the `run` / CI gate.

## Where you see it

The classification is defined once (in `bajutsu/capabilities.py`) and consumed everywhere, so the
surfaces can never disagree:

- **`bajutsu --help`** groups every command under *Claude-free (zero-config)* or *Uses Claude*.
- **`doctor`** reports Claude readiness as a separate, clearly **optional** section: a host with no
  AI setup is still graded `Ready` for the deterministic path, with Claude shown as a distinct
  "not configured (optional)" line — never conflated with a blocking problem.
- **`serve`** shows the Claude tabs (`record` / `crawl`) but disables them with an inline
  explanation when Claude is unreachable, pointing at the in-UI key field; they re-enable the moment
  a key is set, Bedrock is configured, or the `ant` CLI is signed in.

## Installing the Claude paths

The split is a packaging boundary too, not only a runtime one
([BE-0111](../roadmaps/BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency.md)):
the AI software development kit (SDK) is an opt-in extra, so the base install carries no AI
dependency at all.

- `pip install bajutsu` — the deterministic authoring / running paths (`run`, `doctor`, `lint`,
  `codegen`, `trace`, `approve`, and the rest of the Claude-free column above). No AI SDK is
  installed, and nothing here reaches a model.
- `pip install bajutsu[ai]` — adds the Anthropic SDK for the Claude paths (`record`, `crawl`,
  `triage --ai`, `run --alert-handling`). Use `bajutsu[bedrock]` instead for the Amazon Bedrock
  provider; it layers the Bedrock variant onto the same SDK.

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

Which mechanism authenticates is config (per
[BE-0047](../roadmaps/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) /
[BE-0053](../roadmaps/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md) /
[BE-0163](../roadmaps/BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md)); the
classification above is the same regardless of which you pick.
