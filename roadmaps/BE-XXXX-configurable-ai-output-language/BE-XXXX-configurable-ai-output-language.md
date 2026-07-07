**English** · [日本語](BE-XXXX-configurable-ai-output-language-ja.md)

# BE-XXXX — Configurable AI output language for record and crawl

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-configurable-ai-output-language.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | AI provider configuration |
<!-- /BE-METADATA -->

## Introduction

The AI-assisted authoring paths — `record` and `crawl` — emit free-text prose written by the
model: the `from:` provenance strings that `record` writes into a scenario, and the reasoning
narration `crawl` streams as it explores. Today the language of that prose is not something the
user chooses. In `record` it is emergent — it follows whichever language the natural-language
goal happened to be written in — so a Japanese goal yields Japanese provenance and an English
goal yields English, with no way to fix the output language independently of the input. In
`crawl` the model's prose comes out in English regardless. This proposal adds an explicit,
configurable **AI output language** so a team gets consistent, chosen-language prose from both
paths, set once in config and overridable per invocation.

This setting governs **only the language the AI writes its own generated prose in**. It never
enters the deterministic `run` / CI gate (which calls no model), so it does not touch a
pass/fail verdict — it is a Tier-1 authoring/investigation knob, consistent with prime directive 1.

## Motivation

- **Consistency, not luck.** In `record`, the output language is a side effect of the goal's
  language rather than a decision. A team that authors goals in Japanese but wants English
  provenance (or the reverse) has no lever. Provenance strings (`from:`) are the durable,
  human-read record of *why* each step exists ([BE-0044](../BE-0044-scenario-provenance/BE-0044-scenario-provenance.md));
  their language should be a project choice, not a per-goal accident.
- **`crawl` is stuck in English.** A team working in Japanese cannot get Japanese reasoning
  narration out of `crawl` at all, even though the model is perfectly capable of it.
- **The knob has an obvious home.** `record` and `crawl` already share `AiSettings` / `AiConfig`
  (`provider` / `model` / `base_url` / `key_env` / `effort`), resolved per-target and globally and
  already threaded into every AI call site. An output-language field slots in beside `effort` with
  no new plumbing, and the serve Web UI already renders `effort` as a dropdown — the new setting is
  a sibling `<select>`.
- **This is distinct from the existing `locale`.** The `locale` field
  (`Preconditions.locale`, applied at Simulator launch via `-AppleLocale` / `-AppleLanguages`) sets
  the **device / app UI language** — an orthogonal layer. Conflating the two would be a footgun
  (e.g. testing a Japanese-localized app while the AI narrates in English, or vice versa), so this
  is deliberately a separate setting.

## Detailed design

The work is MECE across four units.

### 1. Config field: `ai.language`

Add an `language` field to `AiConfig` / `AiSettings` (`bajutsu/config.py`), parallel to `effort`:

- **Values:** an enum — `ja` | `en` | `auto`. `auto` preserves today's `record` behavior (the
  model follows the goal's language) and is the **default**, so existing projects are unchanged.
  For `crawl`, `auto` resolves to the current English default (there is no goal to follow).
- Resolvable both globally and per-target (`targets.<name>.ai.language`), reusing the existing
  `AiSettings` resolution so precedence matches `effort`.

### 2. Prompt threading

Thread the resolved language into the AI call sites so the model constrains its free-text output:

- **`record`** — append a single output-language instruction to the authoring system prompt
  (`bajutsu/claude_agent.py`) and the enrichment agent (`bajutsu/claude_enrich_agent.py`), of the
  form "write all free-text output (reasoning, intent, and provenance) in `<language>`". When the
  value is `auto`, append nothing (today's behavior). This is the same mechanism the existing
  `--alert-instruction` flag uses — a resolved string folded into the system prompt.
- **`crawl`** — append the same instruction to the crawl guide / tabs system prompts
  (`bajutsu/crawl_guide.py`, `bajutsu/crawl_tabs.py`) so the model's generated prose
  (`Proposal.thought`, the streamed reasoning) comes out in the chosen language.

### 3. CLI flag: `--language`

Add `--language {ja,en,auto}` to both `record` and `crawl` (`bajutsu/cli/commands/record.py`,
`bajutsu/cli/commands/crawl.py`), overriding the resolved config value for that invocation —
mirroring how `--effort` overrides `ai.effort`. Absent, the config value (default `auto`) applies.

### 4. serve Web UI dropdown

Add an **Output language** `<select>` to the serve AI settings panel, beside the existing
Reasoning-effort dropdown (`#ai-effort`, `data-testid="settings.effort"` in
`bajutsu/templates/serve.html.j2` / `serve.js`). It reads and writes the same `ai.language` value,
with a `data-testid` of `settings.language` so the web-backend dogfood suite can drive it.

### Scope boundary — what this does *not* localize

The setting governs **model-generated prose only**. The persisted `crawl` report
(`screenmap.json` / `screenmap.html`) is *not* LLM prose: its text is either copied verbatim from
the app's own on-screen UI (`TabTarget.label`) or hardcoded English f-strings
(`bajutsu/crawl_flows.py`, `bajutsu/crawl_repro.py`). Localizing those hardcoded report strings is
a separate concern — an i18n of Bajutsu's own report chrome, not "what language does the AI write
in" — and is explicitly **out of scope** here. It is noted as a possible future item so the
distinction stays on record. (A consequence: because `crawl`'s `Proposal.thought` is currently
log/stream-only and not written into the report, the visible effect for `crawl` is on its streamed
reasoning; `record`'s durable `from:` provenance is the path where the setting shows up in
committed artifacts.)

## Alternatives considered

- **Free-text language string (like `--alert-instruction`).** Accept any language name
  ("日本語", "français"). More flexible, but harder to validate and to render as a dropdown, and it
  invites inconsistent values across a team. Rejected in favor of a small enum; the enum can grow
  if a concrete need appears.
- **Drop `auto`, always require an explicit language.** Makes `record` fully deterministic in
  output language, but it is a breaking change for every existing project (whose provenance is
  today emergent) and removes the "just follow my goal" behavior some users may want. Kept `auto`
  as the default instead.
- **Reuse the existing `locale` field.** Rejected — `locale` is the device/app UI language, an
  orthogonal layer; overloading it would make "Japanese app under English AI narration"
  impossible to express and confuse two distinct concerns.
- **Localize the hardcoded `crawl` report strings in the same change.** Out of scope (see the
  scope boundary above): that is report-chrome i18n, a different problem from constraining the
  model's output language, and folding it in would blur this item's MECE boundary.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `ai.language` config field (`AiConfig` / `AiSettings`), enum `ja` | `en` | `auto`, default `auto`
- [ ] Prompt threading in `record` (authoring + enrichment) and `crawl` (guide + tabs)
- [ ] `--language` CLI flag on `record` and `crawl`
- [ ] Output-language dropdown in the serve AI settings panel

## References

- [BE-0044 — Scenario provenance (`from:`)](../BE-0044-scenario-provenance/BE-0044-scenario-provenance.md)
  — the durable `record` prose this setting governs.
- [BE-0104 — Vendor-neutral AI backend interface](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
  and [BE-0103 — Right-size the model and reasoning effort](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md)
  — the `AiSettings` knobs (`effort`) this field sits beside.
- `bajutsu/config.py` (`AiConfig` / `AiSettings`), `bajutsu/claude_agent.py`,
  `bajutsu/crawl_guide.py`, `bajutsu/cli/commands/{record,crawl}.py`,
  `bajutsu/templates/serve.html.j2` — the surfaces the four work units touch.
