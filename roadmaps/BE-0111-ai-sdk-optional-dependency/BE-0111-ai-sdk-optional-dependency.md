**English** · [日本語](BE-0111-ai-sdk-optional-dependency-ja.md)

# BE-0111 — Make the AI SDK an optional extra so the deterministic gate installs AI-free

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0111](BE-0111-ai-sdk-optional-dependency.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0111") |
| Implementing PR | [#623](https://github.com/bajutsu-e2e/bajutsu/pull/623) |
| Topic | AI provider configuration |
| Related | [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md), [BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md), [BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md), [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) |
<!-- /BE-METADATA -->

## Introduction

The `anthropic` SDK moves out of the base `dependencies` and into an `ai` extra. The default
install, which an operator uses to run the deterministic `run` / CI gate, then carries no AI SDK.

The deterministic path never calls a model. Yet `anthropic` currently heads the five base
dependencies, so every install pulls the SDK and its transitive dependencies, whether or not any AI
feature is ever used.

This item is the **packaging** counterpart to
[BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md). BE-0101 already split the
*runtime* into Claude-using and Claude-free paths.

## Motivation

`pyproject.toml` declares five base dependencies, and `anthropic` is the first of them:

```toml
dependencies = [
    "anthropic>=0.50",
    "jinja2>=3.1",
    "pydantic>=2.7",
    "pyyaml>=6.0",
    "typer>=0.12",
]
```

Every other optional subsystem already lives behind an extra: `idb`, `web`, `visual`, `mcp`,
`bedrock`, `worker`, `server`, `db`, `oauth`, `schema`, `docs`. The AI SDK is the lone exception
that rides in the base set. This discipline gap has two concrete costs.

The first cost is supply chain. A deterministic install carries an SDK and its transitive
dependencies that the `run` / CI gate never exercises. That widens the attack and audit surface for
no functional gain.

The second cost is a contradiction of the promise the runtime already keeps. BE-0101 made the
default path Claude-free at runtime. With the SDK still in the base install, the *package* says "AI
is mandatory" even though the *code* no longer does.

The change is narrow and separable from any interface redesign. BE-0104 (vendor-neutral AI backend)
abstracts *which model family* the AI paths speak to. This item only moves *where the SDK is
declared*. The base package becomes AI-free, and the AI authoring and investigation features
declare their SDK dependency explicitly through the `ai` extra.

The two items compose: once BE-0104 lands, `anthropic` is one adapter's dependency rather than the
whole package's. Neither blocks the other, and the base-install cleanup is worth doing on its own.

## Detailed design

The work divides into the five pieces below, with no overlap and no gap.

### 1. Introduce the `ai` extra; drop `anthropic` from the base set

Remove `anthropic>=0.50` from `[project].dependencies`. Add `ai = ["anthropic>=0.50"]` under
`[project.optional-dependencies]`. The base package then depends only on `jinja2`, `pydantic`,
`pyyaml`, and `typer`, none of which reach a model.

### 2. Recompose the `bedrock` extra on top of `ai`

The `bedrock` extra is today `["anthropic[bedrock]>=0.50"]`. Keep a single version source. Express
`bedrock` so that it layers the Bedrock variant onto the same `anthropic` pin that the `ai` extra
declares. Two forms work: `bedrock = ["bajutsu[ai]", "anthropic[bedrock]>=0.50"]`, or having
`bedrock` extend `ai`. The version is then declared once, and `ai` and `bedrock` never disagree.

### 3. Lock "no `anthropic` on the default path" with an import guard

The default path must import cleanly on a base (AI-free) install. Extend the existing import-guard
tests (the pattern in `tests/serve/test_import_guard.py`) with a check. The check imports `bajutsu`
and walks the deterministic `run` path, and it asserts that `anthropic` is never imported. The
AI-free base then becomes a tested guarantee, not an accident of the current import order.

### 4. Keep the gate's AI-path test coverage via the dev group

The AI modules remain part of the codebase and need regression coverage. The `dev` dependency group
therefore keeps installing the AI extra: add `ai` to the
`bajutsu[bedrock,server,worker,db,oauth,mcp,visual,schema]` list. `make check` still imports and
tests the AI paths.

The new guarantee concerns the *base* install being AI-free. It does not drop AI test coverage.
Add an explicit check, in CI or in a test. The check confirms that a base install with no `ai` or
`bedrock` extra can import `bajutsu` and run the deterministic subset with `anthropic` absent from
the environment.

### 5. Document the install split

Update the install instructions (README, plus `docs/` and the `docs/ja/` mirror) so that the two
audiences are explicit:

- `pip install bajutsu` serves deterministic authoring and running.
- `pip install bajutsu[ai]` (or `[bedrock]`) serves the AI-assisted `record`, `triage`,
  crawl-guide, and alert-dismissal paths.

### Machine-checkable outcome

A virtual environment built from the base package (no `ai` or `bedrock` extra) can import `bajutsu`
and run the deterministic tests with `anthropic` not installed. The import-guard test fails if any
default-path module imports `anthropic`. All checks are static and deterministic. No LLM is involved
in verifying any of this.

### Prime-directive compliance

This item reinforces directive #1. The deterministic gate is model-free at runtime (BE-0101), and
it now becomes SDK-free at the base install too. The item adds no LLM call anywhere. AI stays on
the Tier-1 authoring and investigation paths, now gated behind an explicit extra. Determinism and
app-agnostic behavior are untouched, because the change is purely a packaging change.

## Alternatives considered

- **Keep `anthropic` in the base set (status quo).** Rejected. It carries an unused SDK and its
  transitive dependencies into every deterministic install. It is also the only optional subsystem
  not behind an extra, which contradicts the project's own extras discipline.
- **Fold this into BE-0104.** The review that surfaced this item suggested augmenting BE-0104. The
  item stays separate by choice. BE-0104 redesigns the *provider interface*, a behavior-preserving
  refactor of every AI call site. This item is a self-contained packaging move. It can land
  independently and deliver the AI-free base install even before the neutral seam exists. The two
  items are linked (`Related`) and compose cleanly.
- **Make the gate install no AI at all and drop the AI-path tests.** Rejected. The AI modules are
  part of the codebase and must keep their regression net. The `dev` group keeps the `ai` extra, so
  `make check` still tests them. The guarantee covers the *base* install, not test coverage.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Introduce the `ai` extra and drop `anthropic` from the base `dependencies`
- [x] Recompose the `bedrock` extra on top of `ai` (single version source)
- [x] Import guard: lock "no `anthropic` on the default path"
- [x] Keep AI-path test coverage via the `dev` group; assert a base install runs the deterministic subset AI-free
- [x] Document the `pip install bajutsu` vs `bajutsu[ai]` split (both languages)

- [#623](https://github.com/bajutsu-e2e/bajutsu/pull/623): move `anthropic` into an `ai` extra
  (base install AI-free), recompose `bedrock` on top of it, add `ai` to the `dev` group, extend the
  import guard with an anthropic-off-the-default-path check plus a base-install (anthropic-absent)
  simulation, and document the `bajutsu` / `bajutsu[ai]` install split in both languages.

## References

`pyproject.toml` (`[project].dependencies` / `[project.optional-dependencies]` /
`[dependency-groups].dev` — the base set, the extras, and the gate's install list this item
edits), `tests/serve/test_import_guard.py` (the import-guard pattern this item extends to
`anthropic`), `bajutsu/anthropic_client.py` and the AI call sites (`bajutsu/claude_agent.py` ·
`bajutsu/claude_triage.py` · `bajutsu/alerts.py` · `bajutsu/claude_enrich_agent.py` ·
`bajutsu/crawl_guide.py` · `bajutsu/crawl_tabs.py` — the code that needs the `ai` extra),
[BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md) (the
runtime Claude-using / Claude-free split this completes at the packaging layer),
[BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) (the
vendor-neutral interface this composes with),
[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) and
[BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md) (the
provider / Bedrock configuration whose `bedrock` extra this recomposes).
