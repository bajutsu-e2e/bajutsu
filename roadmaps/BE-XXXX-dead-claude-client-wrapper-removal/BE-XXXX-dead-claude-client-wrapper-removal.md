**English** · [日本語](BE-XXXX-dead-claude-client-wrapper-removal-ja.md)

# BE-XXXX — Remove the dead Claude-client wrapper orphaned by the backend seam

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-dead-claude-client-wrapper-removal.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/anthropic_client.py` still defines `ensure_client` and the `CachesClient` protocol
(around lines 216–233). Both are dead: no production code path calls them anymore. This item
removes them and their covering test, so the module's public surface matches what the AI
authoring/investigation classes actually use today.

## Motivation

`ensure_client` was added by BE-0140 as "the lazy-build-then-cache wrapper the AI classes
share" — at the time, six `Claude*` classes each called `self._ensure_client()`, which
delegated to this module-level `ensure_client(agent)` to build (via `make_client`) and cache
an SDK client on `agent._client`. BE-0104 then introduced the vendor-neutral `AiBackend` seam
(`bajutsu/ai/registry.create_backend`), and every one of those classes moved onto it: each now
carries its own `_ensure_backend` method that calls `create_backend(ai=self._ai)` and caches
the result on `self._backend` — for example `bajutsu/claude_agent.py:632`,
`bajutsu/claude_triage.py:253`, `bajutsu/claude_enrich_agent.py:190`, `bajutsu/alerts.py:178`,
`bajutsu/crawl_guide.py:360`, and `bajutsu/crawl_tabs.py:192`. `bajutsu/ai/anthropic.py`'s
`AnthropicBackend` — the adapter that now sits behind `create_backend` for the Anthropic
provider — builds its own client with a private `_ensure_client` method
(`bajutsu/ai/anthropic.py:64`) that calls `make_client` directly; it does not call the
module-level `ensure_client` from `anthropic_client.py` either.

So `ensure_client` and `CachesClient` have no production caller left. Worse, `ensure_client`'s
own docstring is now false: it still claims to be "the lazy-build-then-cache wrapper the AI
authoring/investigation classes share (BE-0140)", but none of them do — they all share
`create_backend` instead. A stale abstraction with a docstring that names a mechanism the
codebase no longer uses is actively misleading: a reader who greps for how the `Claude*`
classes reach a model finds this function, believes it, and starts from the wrong seam. The
only reason it still exists is that `tests/test_anthropic_client.py` (around lines 289–313)
still exercises it — the test outlived the code it was written to protect.

This is a behavior-preserving cleanup confined to the AI authoring/investigation paths
(`record`, `enrich`, `triage`, alert dismissal, crawl). It never touches the deterministic
`run`/CI verdict path, so prime directive 1 (AI is the author and the failure investigator,
never the judge) is unaffected either way.

## Detailed design

The work is a straight removal, with a verification step to make sure nothing outside the
files below still depends on the wrapper:

1. **Delete `ensure_client`** from `bajutsu/anthropic_client.py` (around lines 223–233).
2. **Delete the `CachesClient` protocol** from `bajutsu/anthropic_client.py` (around lines
   216–220), which exists solely to type `ensure_client`'s `agent` parameter.
3. **Delete the covering test** in `tests/test_anthropic_client.py` (around lines 289–313):
   the `_CacheHolder` stand-in class, the "ensure_client is the lazy-build-then-cache wrapper…"
   comment, `test_ensure_client_returns_injected_client_without_building`, and
   `test_ensure_client_builds_once_and_reuses`.
4. **Grep the repo to confirm no remaining caller before removal** —
   `rg -n "ensure_client|CachesClient"` should, after the three deletions above, return no
   matches outside this proposal itself (`bajutsu/ai/anthropic.py`'s unrelated
   `_ensure_client` method and the six classes' `_ensure_backend` methods are different names
   and are left untouched).

No other file imports `ensure_client` or `CachesClient`, so this needs no follow-on changes to
`bajutsu/claude_agent.py`, `bajutsu/claude_triage.py`, `bajutsu/claude_enrich_agent.py`,
`bajutsu/alerts.py`, `bajutsu/crawl_guide.py`, `bajutsu/crawl_tabs.py`, or `bajutsu/ai/anthropic.py`
— those already reach a model exclusively through `create_backend` / their own
`_ensure_backend`, or (for `AnthropicBackend`) through `make_client` directly.

## Alternatives considered

- **Keep it "just in case" a future AI class wants a raw SDK client instead of the
  `AiBackend` seam.** Rejected: dead code with a misleading docstring is worse than no code at
  all — a contributor who finds it has to independently discover it is unused before trusting
  or ignoring it. If a future need for a raw client-caching helper appears, it is cheaper to
  write a small, correctly-documented one against the seam that exists then than to keep
  carrying one whose docstring already lies about the current architecture.
- **Repurpose `ensure_client` into a new shared base class for the `Claude*` classes** (a
  hypothetical `ClaudeBackedAgent` base, referred to here by name only — this proposal does
  not number or scope that item). Rejected for this item: that would be a genuine new design
  exercise (what the base owns, which of the six classes' constructor differences it
  accommodates) and deserves its own proposal: bundling it here would turn a same-day deletion
  into a design discussion that blocks it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Delete `ensure_client` from `bajutsu/anthropic_client.py`
- [ ] Delete the `CachesClient` protocol from `bajutsu/anthropic_client.py`
- [ ] Delete the covering test in `tests/test_anthropic_client.py`
- [ ] Grep the repo to confirm no remaining caller before merging

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
