**English** · [日本語](BE-XXXX-ai-sdk-optional-dependency-ja.md)

# BE-XXXX — Make the AI SDK an optional extra so the deterministic gate installs AI-free

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ai-sdk-optional-dependency.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | AI provider configuration |
| Related | [BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md), [BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md), [BE-0101](../../implemented/BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md), [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) |
<!-- /BE-METADATA -->

## Introduction

Move the `anthropic` SDK out of the base `dependencies` and into an `ai` extra, so that the
default install — the one an operator uses to run the deterministic `run` / CI gate — carries no AI
SDK. The deterministic path never calls a model, yet `anthropic` currently sits at the head of the
five base dependencies, so every install pulls the SDK and its transitive dependencies whether or
not any AI feature is ever used. This item is the **packaging** counterpart to
[BE-0101](../../implemented/BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md), which
already split the *runtime* into Claude-using and Claude-free paths.

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
that rides in the base set. That is a discipline gap, and it has two concrete costs. First,
supply chain: a deterministic install carries an SDK and its transitive dependencies that the
`run` / CI gate never exercises, widening the attack and audit surface for no functional gain.
Second, it contradicts the promise the runtime already keeps. BE-0101 made the default path
Claude-free at runtime; leaving the SDK in the base install means the *package* still says "AI is
mandatory" even though the *code* no longer does.

The point is narrow and separable from any interface redesign. BE-0104 (vendor-neutral AI backend)
abstracts *which model family* the AI paths speak to; this item only moves *where the SDK is
declared*. The base package becomes AI-free, and the AI authoring / investigation features declare
their SDK dependency explicitly through the `ai` extra. The two items compose — once BE-0104 lands,
`anthropic` is one adapter's dependency rather than the whole package's — but neither blocks the
other, and the base-install cleanup is worth doing on its own.

## Detailed design

The work is MECE along the five pieces below.

### 1. Introduce the `ai` extra; drop `anthropic` from the base set

Remove `anthropic>=0.50` from `[project].dependencies` and add `ai = ["anthropic>=0.50"]` under
`[project.optional-dependencies]`. The base package then depends only on `jinja2`, `pydantic`,
`pyyaml`, and `typer` — none of which reach a model.

### 2. Recompose the `bedrock` extra on top of `ai`

`bedrock` is today `["anthropic[bedrock]>=0.50"]`. Keep a single version source: express `bedrock`
so it layers the Bedrock variant onto the same `anthropic` pin the `ai` extra declares (e.g.
`bedrock = ["bajutsu[ai]", "anthropic[bedrock]>=0.50"]` or by having `bedrock` extend `ai`), so the
version is declared once and `ai` / `bedrock` never disagree.

### 3. Lock "no `anthropic` on the default path" with an import guard

The default path must import cleanly on a base (AI-free) install. Extend the existing import-guard
tests (the pattern in `tests/serve/test_import_guard.py`) with a check that importing `bajutsu` and
walking the deterministic `run` path never imports `anthropic`. This makes the AI-free base a
tested guarantee, not an accident of current import order.

### 4. Keep the gate's AI-path test coverage via the dev group

The AI modules remain part of the codebase and need regression coverage, so the `dev` dependency
group keeps installing the AI extra (add `ai` to the `bajutsu[bedrock,server,worker,db,oauth,mcp,
visual,schema]` list). `make check` therefore still imports and tests the AI paths. The new
guarantee is about the *base* install being AI-free, not about dropping AI test coverage — add an
explicit check (in CI or a test) that a base install with no `ai` / `bedrock` extra can import
`bajutsu` and run the deterministic subset with `anthropic` absent from the environment.

### 5. Document the install split

Update the install instructions (README and `docs/` + `docs/ja/` mirror) so the two audiences are
explicit: `pip install bajutsu` for deterministic authoring / running, `pip install bajutsu[ai]`
(or `[bedrock]`) for the AI-assisted `record` / `triage` / crawl-guide / alert-dismissal paths.

### Machine-checkable outcome

A virtual environment built from the base package (no `ai` / `bedrock` extra) can import `bajutsu`
and run the deterministic tests with `anthropic` not installed; the import-guard test fails if any
default-path module imports `anthropic`. All checks are static / deterministic — no LLM is involved
in verifying any of this.

### Prime-directive compliance

This reinforces directive #1: the deterministic gate is model-free at runtime (BE-0101) and now
SDK-free at the base install too. No LLM call is added anywhere; AI stays on the Tier-1 authoring /
investigation paths, now gated behind an explicit extra. Determinism and app-agnostic behavior are
untouched — this is a packaging change only.

## Alternatives considered

- **Keep `anthropic` in the base set (status quo).** Rejected: it carries an unused SDK and its
  transitive dependencies into every deterministic install, and it is the only optional subsystem
  not behind an extra — an inconsistency with the project's own extras discipline.
- **Fold this into BE-0104.** The review that surfaced this suggested augmenting BE-0104. Kept as
  its own item by choice: BE-0104 redesigns the *provider interface* (a behavior-preserving refactor
  of every AI call site), whereas this is a self-contained packaging move that can land
  independently and deliver the AI-free base install even before the neutral seam exists. The two
  are linked (`Related`) and compose cleanly.
- **Make the gate install no AI at all and drop the AI-path tests.** Rejected: the AI modules are
  part of the codebase and must keep their regression net. The `dev` group keeps the `ai` extra so
  `make check` still tests them; the guarantee is scoped to the *base* install, not to test
  coverage.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Introduce the `ai` extra and drop `anthropic` from the base `dependencies`
- [ ] Recompose the `bedrock` extra on top of `ai` (single version source)
- [ ] Import guard: lock "no `anthropic` on the default path"
- [ ] Keep AI-path test coverage via the `dev` group; assert a base install runs the deterministic subset AI-free
- [ ] Document the `pip install bajutsu` vs `bajutsu[ai]` split (both languages)

## References

`pyproject.toml` (`[project].dependencies` / `[project.optional-dependencies]` /
`[dependency-groups].dev` — the base set, the extras, and the gate's install list this item
edits), `tests/serve/test_import_guard.py` (the import-guard pattern this item extends to
`anthropic`), `bajutsu/anthropic_client.py` and the AI call sites (`bajutsu/claude_agent.py` ·
`bajutsu/claude_triage.py` · `bajutsu/alerts.py` · `bajutsu/claude_enrich_agent.py` ·
`bajutsu/crawl_guide.py` · `bajutsu/crawl_tabs.py` — the code that needs the `ai` extra),
[BE-0101](../../implemented/BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md) (the
runtime Claude-using / Claude-free split this completes at the packaging layer),
[BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) (the
vendor-neutral interface this composes with),
[BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) and
[BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md) (the
provider / Bedrock configuration whose `bedrock` extra this recomposes).
