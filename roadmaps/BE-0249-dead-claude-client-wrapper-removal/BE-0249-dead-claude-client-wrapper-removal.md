**English** · [日本語](BE-0249-dead-claude-client-wrapper-removal-ja.md)

# BE-0249 — Remove the dead Claude-client wrapper orphaned by the backend seam

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0249](BE-0249-dead-claude-client-wrapper-removal.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0249") |
| Implementing PR | [#1091](https://github.com/bajutsu-e2e/bajutsu/pull/1091) |
| Topic | Codebase quality & technical debt |
| Related | [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md), [BE-0140](../BE-0140-dedupe-claude-client-init/BE-0140-dedupe-claude-client-init.md), [BE-0246](../BE-0246-claude-client-taxonomy/BE-0246-claude-client-taxonomy.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/anthropic_client.py` still defines `ensure_client` and the `CachesClient` protocol
(around lines 216–233). No production code path calls either one anymore. This item removes both
and the test that covers them. The module's public surface then matches what the AI
authoring and investigation classes use today.

## Motivation

BE-0140 added `ensure_client` as "the lazy-build-then-cache wrapper the AI classes share". At the
time, six `Claude*` classes each called `self._ensure_client()`. That method delegated to the
module-level `ensure_client(agent)`, which built an SDK client (via `make_client`) and cached it on
`agent._client`.

BE-0104 then introduced the vendor-neutral `AiBackend` seam (`bajutsu/ai/registry.create_backend`),
and every one of those classes moved onto it. Each class now carries its own `_ensure_backend`
method. That method calls `create_backend(ai=self._ai)` and caches the result on `self._backend`.
The call sites are:

- `bajutsu/claude_agent.py:632`
- `bajutsu/claude_triage.py:253` and `:473`
- `bajutsu/claude_enrich_agent.py:190`
- `bajutsu/alerts.py:178`
- `bajutsu/crawl_guide.py:360`
- `bajutsu/crawl_tabs.py:192`

That makes seven implementers in all, because `claude_triage.py` defines two: `ClaudeTriageAgent`
and `ClaudeCrossRunTriageAgent`.

`AnthropicBackend` in `bajutsu/ai/anthropic.py` is the adapter behind `create_backend` for the
Anthropic provider. It builds its own client with a private `_ensure_client` method
(`bajutsu/ai/anthropic.py:64`), which calls `make_client` directly. It does not call the
module-level `ensure_client` from `anthropic_client.py` either.

So `ensure_client` and `CachesClient` have no production caller left. The docstring of
`ensure_client` is also false now. It still calls the function "the lazy-build-then-cache wrapper
the AI authoring/investigation classes share (BE-0140)", but none of those classes share it. They
all share `create_backend` instead.

A stale abstraction whose docstring names a mechanism the codebase no longer uses is actively
misleading. A reader who greps for how the `Claude*` classes reach a model finds this function,
believes it, and starts from the wrong seam. The function survives for one reason:
`tests/test_anthropic_client.py` (around lines 289–313) still exercises it. The test outlived the
code it was written to protect.

The cleanup preserves behavior and stays within the AI authoring and investigation paths
(`record`, `enrich`, `triage`, alert dismissal, and crawl). It never touches the deterministic
`run` / CI verdict path, so prime directive 1 (AI is the author and the failure investigator, never
the judge) holds either way.

## Detailed design

The work is a straight removal. A verification step confirms that nothing outside the files below
still depends on the wrapper.

1. **Delete `ensure_client`** from `bajutsu/anthropic_client.py` (around lines 223–233).
2. **Delete the `CachesClient` protocol** from `bajutsu/anthropic_client.py` (around lines
   216–220). It exists solely to type the `agent` parameter of `ensure_client`.
3. **Delete the covering test** in `tests/test_anthropic_client.py` (around lines 289–313). The
   deleted items are:
   - the `_CacheHolder` stand-in class
   - the "ensure_client is the lazy-build-then-cache wrapper…" comment
   - `test_ensure_client_returns_injected_client_without_building`
   - `test_ensure_client_builds_once_and_reuses`
4. **Grep the repo to confirm no remaining caller before removal.** After the three deletions
   above, `rg -n "ensure_client|CachesClient"` should return no matches outside this proposal
   itself. Two groups of names look similar but are different and stay untouched: the unrelated
   `_ensure_client` method in `bajutsu/ai/anthropic.py`, and the `_ensure_backend` methods of the
   `Claude*` classes.

No other file imports `ensure_client` or `CachesClient`. The removal therefore needs no follow-on
change to `bajutsu/claude_agent.py`, `bajutsu/claude_triage.py`, `bajutsu/claude_enrich_agent.py`,
`bajutsu/alerts.py`, `bajutsu/crawl_guide.py`, `bajutsu/crawl_tabs.py`, or `bajutsu/ai/anthropic.py`.
The first six already reach a model exclusively through `create_backend` and their own
`_ensure_backend`. `AnthropicBackend` reaches one through `make_client` directly.

## Alternatives considered

- **Keep it "just in case" a future AI class wants a raw SDK client instead of the `AiBackend`
  seam.** Rejected. Dead code with a misleading docstring is worse than no code. A contributor who
  finds it must first discover on their own that it is unused, and only then can trust or ignore it.
  Suppose a raw client-caching helper becomes necessary later. Writing a small, correctly
  documented one against the seam that exists then costs less than carrying one whose docstring
  already lies about the current architecture.
- **Repurpose `ensure_client` into a new shared base class for the `Claude*` classes.** The
  hypothetical base would be a `ClaudeBackedAgent`, named here only; this proposal neither numbers
  nor scopes that item. Rejected for this item. The base class would be a genuine new design
  exercise: what the base owns, and which of the constructor differences among the `Claude*`
  classes it accommodates. That deserves its own proposal. Bundling it here would turn a same-day
  deletion into a design discussion that blocks the deletion.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Delete `ensure_client` from `bajutsu/anthropic_client.py`
- [x] Delete the `CachesClient` protocol from `bajutsu/anthropic_client.py`
- [x] Delete the covering test in `tests/test_anthropic_client.py`
- [x] Grep the repo to confirm no remaining caller before merging

_Log:_

- [#1091](https://github.com/bajutsu-e2e/bajutsu/pull/1091) — delete `ensure_client` /
  `CachesClient` and their covering test; drop the now-unused `Protocol` import; refresh the stale
  reference in `bajutsu/claude_backed_agent.py`'s docstring (the wrapper it named is now gone).

## References

- `bajutsu/anthropic_client.py:216` (`CachesClient`) and `bajutsu/anthropic_client.py:223`
  (`ensure_client`) — the dead code this item removes
- `tests/test_anthropic_client.py:289` — the covering test to remove alongside it
- [BE-0140](../BE-0140-dedupe-claude-client-init/BE-0140-dedupe-claude-client-init.md)
  (Deduplicate Claude client initialization) — introduced `ensure_client` /
  `CachesClient` as the shared wrapper this item now removes
- [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
  (Vendor-neutral AI backend interface) — the `create_backend` seam that superseded it
- `bajutsu/ai/anthropic.py:64` (`AnthropicBackend._ensure_client`) — the unrelated,
  still-live private method that calls `make_client` directly and is not affected by this item
