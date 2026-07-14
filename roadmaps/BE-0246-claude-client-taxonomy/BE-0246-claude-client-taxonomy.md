**English** · [日本語](BE-0246-claude-client-taxonomy-ja.md)

# BE-0246 — Clarify the module taxonomy for talking to Claude

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0246](BE-0246-claude-client-taxonomy.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0246") |
| Implementing PR | [#1012](https://github.com/bajutsu-e2e/bajutsu/pull/1012), [#1030](https://github.com/bajutsu-e2e/bajutsu/pull/1030), [#_pending_](https://github.com/bajutsu-e2e/bajutsu/pull/_pending_) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

The code that decides "how do we reach a model" is split across three overlapping layers —
credential-gap checks, provider resolution, and provider-agnostic config resolution — plus a
handful of naming choices that mislead about what a module actually holds. None of this is
wrong; each layer works, and the docstrings explain the split carefully. But the fact that the
split *needs* a paragraph of docstring to justify is itself the symptom this item targets: a
reader has to hold three modules' relationships in their head to answer a one-sentence
question. This item proposes a taxonomy cleanup — renames, one merge, and de-duplication of
boilerplate that has grown up around the confusion — with no behavior change.

## Motivation

**Three layers answer "how do we reach a model," and two of them aren't independent.**
`bajutsu/anthropic_client.py:309` defines `credential_gap`, `bajutsu/ai/registry.py:148`
defines a second `credential_gap`, and `bajutsu/ai_availability.py:20` wraps both in a third,
`availability`. The registry's version does not duplicate the check; it dispatches
(`_adapter(ai).credential_gap(ai)`) to `anthropic_client`'s function for the `api-key`,
`bedrock`, and `ant` providers, and to `ai/claude_code.py`'s own `credential_gap` for the
`claude-code` provider. Similarly there are two provider resolvers: `provider` in
`anthropic_client.py:140`, and `_provider_name` / `resolved_provider` in
`ai/registry.py:105` and `:126`. Because the registry's checks mostly delegate rather than
duplicate, the three-layer split is not redundant — but a reader can only tell that by reading
all three files, and the fact that this needs explaining is the smell: a resolver, a
dispatcher, and a convenience wrapper for the same question should read as one obvious shape,
not three files a contributor has to reconcile by hand.

**`anthropic_client.py` is misnamed.** Despite the vendor name, it holds provider-agnostic
config resolution used by every backend, including `claude-code`: `resolve_model` (line 236),
`resolve_effort` (254), `resolve_language` (273), and `language_instruction` (291), plus a
re-export of `AiConfig` (line 42). `ai/claude_code.py:38` and `ai/registry.py:22` both import
from it — i.e. the module that exists to let Bajutsu talk to *any* provider is named after one
specific vendor's SDK. A contributor skimming imports for "where does model/effort/language
resolution live" has no reason to look in a file named `anthropic_client`.

**`agent.py` vs. `agents.py` is a singular/plural readability trap.** `agent.py` holds the
`Agent` / `EnrichmentAgent` protocols and DTOs (`Observation`, `Proposal`); `agents.py` is a
44-line factory (`make_agent`, `make_enrichment_agent`). The names differ by one letter and
sort adjacently in every file listing, so the distinction is invisible until you open both.

**Seven `Claude*` classes reimplement the same backend-and-usage boilerplate.** Each of
`ClaudeAgent` (`bajutsu/claude_agent.py:632`), `ClaudeTriageAgent` and
`ClaudeCrossRunTriageAgent` (`bajutsu/claude_triage.py:253` and `:473`),
`ClaudeEnrichmentAgent` (`bajutsu/claude_enrich_agent.py:190`), `ClaudeActionProposer`
(`bajutsu/crawl_guide.py:360`), `ClaudeTabLocator` (`bajutsu/crawl_tabs.py:192`), and
`ClaudeAlertLocator` (`bajutsu/alerts.py:178`) carries a byte-identical `_ensure_backend`:

```python
def _ensure_backend(self) -> AiBackend:
    if self._backend is None:
        self._backend = create_backend(ai=self._ai)
    return self._backend
```

— plus a near-identical `__init__` (`self._backend`, `self._ai`, `self._redactor`, `self._model`
via `resolve_model`) and a near-identical `usage.record(response.usage, provider=...,
model=...)` call after every model turn. This is the same shape BE-0140 already deduplicated
once, as `ensure_client`; BE-0104's later move from a raw SDK client to the `AiBackend`
abstraction obsoleted that helper; each class quietly grew its own `_ensure_backend` copy
instead of a shared replacement being introduced, and `ensure_client` itself is now dead code
in `anthropic_client.py` with zero call sites remaining. A dedicated proposal removes the dead
wrapper; this item is about giving the seven classes a shared base to call instead, so the
next class doesn't copy the pattern by hand a third time.

**Prompt construction is scattered.** Eight inline system prompts exist across the same files.
The prime-directive boundary line — a sentence reminding the model it only proposes, never
judges pass/fail — is hand-copied in four places across three files: `claude_triage.py:52` and
`:289` ("You are advisory only — you never decide pass/fail, you diagnose and suggest."),
`crawl_guide.py:186` ("You only choose what to TRY. You never decide pass/fail and never judge
results."), and `crawl_tabs.py:131` ("You only report where the tabs are. You never decide
pass/fail."). Each copy is worded slightly differently, which is exactly the drift risk of
hand-copying a boundary statement that exists to keep the AI paths honest about prime
directive 1. Separately, the element-tree-to-text renderer that turns a UI snapshot into
model-readable text is reimplemented five times with subtly different formats: inline in
`claude_agent.py:416`, `_render_elements` in `claude_enrich_agent.py:132`, `_render` and
`_render_evidence` in `claude_triage.py:159` and `:391`, and `_render_elements` in
`crawl_guide.py:251` — differing in field order, quoting, and which traits get omitted. The
codebase already has a precedent for centralizing exactly this kind of shared fragment: the
`_TARGET_PROPS` tool-schema fragment in `claude_agent.py:173` is imported directly by
`claude_enrich_agent.py:31` rather than redefined.

**`record.py`'s private helpers are de-facto shared API.** `_describe_step`,
`_screenshot_bytes`, `_settle_step`, `_execute`, and `_clear_blocking` are all
underscore-prefixed in `record.py`, signaling "module-private" — yet `_screenshot_bytes` is
imported by `alerts.py`, `crawl_guide.py`, and `enrich.py`; `_describe_step` and
`_settle_step` are imported by `claude_enrich_agent.py`; and `_clear_blocking` / `_execute`
are imported by `enrich.py`. The same pattern repeats one module over: `alerts.py`'s
`_png_size` and `_fraction` are imported by `crawl_tabs.py:36`. A leading underscore is
supposed to mean "don't import this from outside," but in practice every one of these
functions has at least one cross-module consumer — the naming actively misrepresents the
module's real public surface.

**None of this touches the deterministic gate.** Every symbol above lives on the AI
authoring/investigation paths — `record`, `enrich`, `triage`, alert dismissal, crawl guidance
and tab location. None of the seven `Claude*` classes, the prompt fragments, or the element
renderer is reachable from `run` or the CI verdict path, so this item does not risk prime
directive 1: it is a pure internal-taxonomy cleanup, not a change to what gets judged
pass/fail or how.

## Detailed design

The work is behavior-preserving throughout — renames, one merge, and hoisting duplicated code
to a shared home, with no change to CLI behavior, scenario schema, or `run`/CI outcomes.
Six independent units of work, MECE by the surface each touches:

1. **Rename `anthropic_client.py` to reflect what it holds.** Move the provider-agnostic
   config resolution (`resolve_model`, `resolve_effort`, `resolve_language`,
   `language_instruction`, the `AiConfig` re-export) to a name that doesn't imply a single
   vendor — e.g. `ai_config.py`, or folded directly into the `bajutsu/ai/` package alongside
   `registry.py` and `claude_code.py`, whichever the implementer finds reads better once the
   import graph is in front of them. Whatever is genuinely Anthropic-SDK-specific (client
   construction such as `make_client`, and the `ant` CLI subprocess path) stays behind in a
   module that keeps the vendor name, since that part of the split is accurate.
2. **Collapse the two `credential_gap` / provider-resolver pairs onto one resolver in
   `bajutsu/ai/`.** Fold `anthropic_client.provider` and `ai/registry._provider_name` /
   `resolved_provider` into a single provider-resolution function, and likewise fold
   `anthropic_client.credential_gap` and `ai/registry.credential_gap` (and the
   `ai_availability.availability` wrapper, if it no longer earns its keep once the other two
   collapse) into one credential-gap check that dispatches per-provider internally. The
   dispatch behavior for `claude-code` (delegating to `ai/claude_code.py`) is preserved; only
   the number of public entry points shrinks.
3. **Introduce a shared `ClaudeBackedAgent` base** holding the common `_ai` / `_redactor` /
   `_backend` / `_model` / `_lang` attributes, a shared `_ensure_backend()`, and a
   `_record_usage(response)` helper, and have the seven classes (`ClaudeAgent`,
   `ClaudeTriageAgent`, `ClaudeCrossRunTriageAgent`, `ClaudeEnrichmentAgent`,
   `ClaudeActionProposer`, `ClaudeTabLocator`, `ClaudeAlertLocator`) inherit from it instead of
   each reimplementing the same three-line `_ensure_backend` and repeated `usage.record(...)`
   call. This is the shared home the now-dead `ensure_client` in `anthropic_client.py` was
   meant to be before BE-0104 changed the shape it needed to wrap; a separate proposal removes
   the dead wrapper itself, so this unit starts from a clean base rather than migrating
   `ensure_client` in place.
4. **Rename `agent.py` / `agents.py` to remove the singular/plural trap.** Either rename to
   names that read apart at a glance — e.g. `agent_protocols.py` for the `Protocol` / DTO
   definitions and `agent_factory.py` for the 44-line construction factory — or merge the
   factory directly into `agent.py` given its size, whichever the implementer finds leaves a
   cleaner import story once the call sites are in view.
5. **Create `bajutsu/ai/prompts.py`** centralizing the shared instruction fragments currently
   hand-copied across system prompts: an `ADDRESSING_RULES`-style constant for shared
   phrasing, and a single `NEVER_JUDGE_BOUNDARY` constant for the prime-directive boundary
   sentence, composed into each of the eight system prompts instead of retyped. Add one
   `render_elements(elements, *, compact)` function used by all five current call sites
   (`claude_agent.py`, `claude_enrich_agent.py`, `claude_triage.py` ×2, `crawl_guide.py`),
   parameterized for the format differences (compact vs. verbose) that are actually
   load-bearing today, with the incidental differences (quoting, field order) unified. The
   `_TARGET_PROPS` fragment already centralized in `claude_agent.py:173` and imported by
   `claude_enrich_agent.py` is the existing precedent this generalizes.
6. **Promote the genuinely cross-module `record.py` / `alerts.py` private helpers to a public
   module** — e.g. `screenshots.py` or `ai/vision.py` for `_screenshot_bytes`, `_png_size`, and
   `_fraction` — dropping the leading underscore for the functions that already have external
   callers (`_screenshot_bytes`, `_describe_step`, `_settle_step`, `_clear_blocking`,
   `_execute`, `_png_size`, `_fraction`), and updating every cross-module import
   (`alerts.py`, `crawl_guide.py`, `enrich.py`, `claude_enrich_agent.py`, `crawl_tabs.py`) to
   the new home. Any helper that turns out, on inspection, to have no caller outside its own
   module stays private and is left out of the move.

Each unit is independently shippable and independently testable: the existing test suite for
each touched class (`tests/test_claude_agent.py`, `tests/test_claude_triage.py`,
`tests/test_claude_enrich_agent.py`, `tests/test_crawl_guide.py`, `tests/test_crawl_tabs.py`,
`tests/test_alerts.py`, `tests/test_record.py`) is the regression net that confirms each rename
or hoist is behavior-preserving, since nothing here changes what a class does — only where its
code lives and what it's called.

## Alternatives considered

- **Leave the resolvers where they are and just document the layering more thoroughly.**
  Rejected: the current docstrings already work hard to justify why `credential_gap` and
  provider resolution are split three ways, and that effort is itself the signal that the
  split is confusing rather than self-evident. A better comment is not a substitute for a
  name and location that don't need explaining.
- **Leave `anthropic_client.py` named as-is and rely on module-level docstrings to clarify
  that it's the shared config hub.** Rejected for the same reason: a vendor name on a
  provider-agnostic module misleads on first contact, before a reader has read any docstring
  at all — the fix is a name or a move, not more prose.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Rename `anthropic_client.py` to a name that reflects its provider-agnostic config
      resolution, leaving genuinely Anthropic-SDK-specific code behind under a vendor name —
      config resolution moved to a new top-level `ai_config.py`; `anthropic_client.py` keeps only
      the Anthropic SDK client factory and `ant` CLI token IO
- [x] Collapse the two `credential_gap` / provider-resolver pairs
      (`anthropic_client.py` + `ai/registry.py`, plus the `ai_availability.availability`
      wrapper) onto one resolver in `bajutsu/ai/`
- [x] Introduce a shared `ClaudeBackedAgent` base for the seven `Claude*` classes'
      `_ensure_backend` / usage-record boilerplate — new `bajutsu/claude_backed_agent.py` holds the
      `_backend` / `_ai` / `_redactor` / `_model` attributes, the byte-identical `_ensure_backend`,
      and a `_record_usage(response, category)` helper; the seven classes inherit it
- [x] Rename `agent.py` / `agents.py` (or merge the factory into `agent.py`) to remove the
      singular/plural readability trap — `agent.py` → `agent_protocols.py` (protocols/DTOs),
      `agents.py` → `agent_factory.py` (construction factory)
- [x] Create `bajutsu/ai/prompts.py` centralizing the prime-directive boundary line and other
      shared prompt fragments, plus one shared `render_elements` helper for the five
      element-tree-to-text call sites — new `bajutsu/ai/prompts.py` holds `NEVER_JUDGE_BOUNDARY`
      (composed into the four triage/crawl system prompts) and `render_elements(elements, *,
      compact)` (used by all five call sites). The `ADDRESSING_RULES` fragment named in the design
      turned out to appear only once (`crawl_guide`), so it was left in place rather than invented as
      a shared constant
- [ ] Promote the cross-module `record.py` / `alerts.py` private helpers
      (`_screenshot_bytes`, `_describe_step`, `_settle_step`, `_clear_blocking`, `_execute`,
      `_png_size`, `_fraction`) to a public module

**Log**

- [#1012](https://github.com/bajutsu-e2e/bajutsu/pull/1012) — Unit 4: renamed `bajutsu/agent.py` → `bajutsu/agent_protocols.py` (the `Agent` /
  `EnrichmentAgent` protocols and `Observation` / `Proposal` DTOs) and `bajutsu/agents.py` →
  `bajutsu/agent_factory.py` (the `make_agent` / `make_enrichment_agent` construction factory),
  updating every import site, the docstring-lint and E2E-relevance allowlists, and the
  architecture/recording docs. Behavior-preserving; the existing suite is the regression net.
- [#1030](https://github.com/bajutsu-e2e/bajutsu/pull/1030) — Units 1 + 2 (rebased onto `main` after #1012 merged): extracted the provider-agnostic config resolution
  from `anthropic_client.py` into a new top-level `ai_config.py` (`resolve_model` / `resolve_effort` /
  `resolve_language` / `language_instruction` / `resolve_provider`, the shared env constants, and the
  `AiConfig` re-export), leaving `anthropic_client.py` holding only the Anthropic SDK client factory
  (`make_client`) and the `ant` CLI token IO. Folded the duplicated provider resolution onto the one
  `ai_config.resolve_provider` (reused by `ai/registry`), moved the Anthropic-family `credential_gap`
  into the `bajutsu/ai/anthropic` adapter, and dropped the redundant `ai_availability.availability`
  passthrough so `bajutsu.ai.credential_gap` is the single entry point. Behavior-preserving; the
  existing suites are the regression net.
- [#1034](https://github.com/bajutsu-e2e/bajutsu/pull/1034) — Unit 3: added `bajutsu/claude_backed_agent.py` with the `ClaudeBackedAgent` base
  (the `_backend` / `_ai` / `_redactor` / `_model` attributes, the byte-identical `_ensure_backend`,
  and a `_record_usage(response, category)` helper) and had the seven `Claude*` classes
  (`ClaudeAgent`, `ClaudeTriageAgent`, `ClaudeCrossRunTriageAgent`, `ClaudeEnrichmentAgent`,
  `ClaudeActionProposer`, `ClaudeTabLocator`, `ClaudeAlertLocator`) inherit it instead of each
  reimplementing the same plumbing, dropping the repeated `_ensure_backend` and `usage.record`
  boilerplate. Also updated the docstring-lint path list and the import-linter core-independence
  contract for the new module. Behavior-preserving; the existing per-class suites are the regression
  net.
- [#_pending_](https://github.com/bajutsu-e2e/bajutsu/pull/_pending_) — Unit 5: added
  `bajutsu/ai/prompts.py` holding `NEVER_JUDGE_BOUNDARY` (the prime-directive-1 boundary sentence,
  composed into the two `claude_triage` system prompts, `crawl_guide`, and `crawl_tabs` instead of
  hand-copied) and `render_elements(elements, *, compact)` (the single element-tree-to-text renderer
  used by `claude_agent`, `claude_enrich_agent`, `claude_triage` ×2, and `crawl_guide`), with field
  order, `repr` quoting, and traits formatting unified across the call sites; `claude_agent` keeps its
  BE-0194 large-screen cap around it. The `ADDRESSING_RULES` fragment the design imagined proved to
  appear only once, so it was left in place. Behavior-preserving on the AI paths; nothing reaches the
  `run` / CI verdict. The existing per-module suites plus a new `tests/ai/test_prompts.py` are the
  regression net.

## References

- `bajutsu/anthropic_client.py:140` (`provider`), `:236` (`resolve_model`), `:254`
  (`resolve_effort`), `:273` (`resolve_language`), `:291` (`language_instruction`), `:309`
  (`credential_gap`), `:223` (the now-dead `ensure_client`)
- `bajutsu/ai/registry.py:22` (imports from `anthropic_client`), `:105` (`_provider_name`),
  `:126` (`resolved_provider`), `:148` (`credential_gap`)
- `bajutsu/ai/claude_code.py:38` (imports `AiConfig`, `resolve_effort`, `resolve_model` from
  `anthropic_client`), `:388` (its own `credential_gap`)
- `bajutsu/ai_availability.py:20` (`availability`)
- `bajutsu/agent.py` (protocols/DTOs), `bajutsu/agents.py` (44-line factory)
- `bajutsu/claude_agent.py:632` (`ClaudeAgent._ensure_backend`), `:173` (`_TARGET_PROPS`),
  `:416` (inline element renderer)
- `bajutsu/claude_triage.py:52` and `:289` (prime-directive boundary line), `:159` and `:391`
  (`_render` / `_render_evidence`), `:253` (`ClaudeTriageAgent._ensure_backend`), `:473`
  (`ClaudeCrossRunTriageAgent._ensure_backend`)
- `bajutsu/claude_enrich_agent.py:31` (imports `_TARGET_PROPS` from `claude_agent.py`), `:132`
  (`_render_elements`), `:190` (`ClaudeEnrichmentAgent._ensure_backend`)
- `bajutsu/crawl_guide.py:186` (prime-directive boundary line), `:251` (`_render_elements`),
  `:360` (`ClaudeActionProposer._ensure_backend`)
- `bajutsu/crawl_tabs.py:131` (prime-directive boundary line), `:192`
  (`ClaudeTabLocator._ensure_backend`), `:36` (imports `_png_size`/`_fraction` from
  `alerts.py`)
- `bajutsu/alerts.py:178` (`ClaudeAlertLocator._ensure_backend`), `:135` (`_png_size`), `:142`
  (`_fraction`)
- `bajutsu/record.py:75` (`_describe_step`), `:173` (`_screenshot_bytes`), `:251`
  (`_settle_step`), `:266` (`_execute`), `:291` (`_clear_blocking`)
- Builds on [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
  (vendor-neutral AI backend interface), whose `AiBackend` abstraction is why the seven
  classes' `_ensure_backend` boilerplate exists in its current form, and generalizes the
  client-init deduplication of
  [BE-0140](../BE-0140-dedupe-claude-client-init/BE-0140-dedupe-claude-client-init.md) to the
  new `_ensure_backend` seam BE-0104 introduced
- A companion proposal removes the dead `ensure_client` wrapper directly; another packages the
  `bajutsu/ai/` layer boundary more broadly — both referenced here by description, pending
  their own BE ids
