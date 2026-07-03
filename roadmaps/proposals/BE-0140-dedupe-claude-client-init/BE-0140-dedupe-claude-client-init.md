**English** · [日本語](BE-0140-dedupe-claude-client-init-ja.md)

# BE-0140 — Deduplicate Claude client initialization

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0140](BE-0140-dedupe-claude-client-init.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

Six `Claude*` classes across the AI authoring/investigation paths each carry a private
`_ensure_client` method with the same three lines: return the injected client if one exists,
otherwise build one and cache it. This item hoists that shared logic into the one place these
classes already import from, so a behavior change to client construction or caching needs one
edit instead of six.

## Motivation

`_ensure_client` is byte-identical in six places: `bajutsu/claude_agent.py:331`
(`ClaudeAgent`), `bajutsu/claude_triage.py:216` (`ClaudeTriageAgent`),
`bajutsu/claude_enrich_agent.py:187` (`ClaudeEnrichmentAgent`), `bajutsu/alerts.py:182`
(`ClaudeAlertLocator`), `bajutsu/crawl_guide.py:352` (`ClaudeActionProposer`), and
`bajutsu/crawl_tabs.py:183` (`ClaudeTabLocator`) — twice the three call sites the initial
codebase-analysis report counted:

```python
def _ensure_client(self) -> Any:
    if self._client is None:
        self._client = make_client(ai=self._ai)
    return self._client
```

All six already import `make_client` from `bajutsu/anthropic_client.py:72`, which itself
short-circuits on an injected `client` (`if client is not None: return client`). So
`_ensure_client` only adds one thing `make_client` doesn't already do: memoizing the
constructed client on `self._client` so a class doesn't reopen an SDK client (and reread the
API key env var) on every call. Each of the six `__init__` methods also repeats the same
`self._client = client; self._ai = ai` assignment that `_ensure_client` closes over. Because
nothing ties these six copies together, a future change to client construction or caching
(a new provider, a different memoization key, added logging) requires a contributor to find
and edit all six by hand — and a seventh AI class added later will most likely copy the
pattern by hand again rather than notice a shared home for it. The fix stays entirely within
the AI authoring/investigation paths (`record`, `enrich`, `triage`, alert dismissal, crawl):
none of the six classes is reachable from the deterministic `run`/CI gate.

## Detailed design

The refactor is behavior-preserving: every one of the six classes keeps its current
constructor signature (`client`, `model`, `ai`, `redactor`, and per-class extras like
`max_tokens`) and the same lazy-build-then-cache semantics for `_client`. The duplication is
removed by giving the six classes a shared implementation to call instead of six private
copies:

- **Hoist the caching wrapper next to `make_client`.** Add one function or small mixin —
  living in `bajutsu/anthropic_client.py` beside `make_client`, since that is already the
  single place these classes import their client-construction logic from — that takes
  `(client, ai)` and returns the same three-line memoized-build result `_ensure_client`
  currently computes. A plain function (e.g. taking `self` or a `(client, ai)` pair and
  returning the value to assign) avoids introducing a new base class into six otherwise
  unrelated `Protocol` implementations; a mixin is the alternative if the six classes'
  constructors turn out easier to share too (see *Alternatives considered*).
- **Replace each of the six `_ensure_client` methods with a call to the shared
  implementation.** Each class keeps its own `self._client` attribute (so the memoization
  stays per-instance) but delegates the "build if absent" decision to the shared function.
  This is a mechanical, one-line-body change per class — the six call sites
  (`next_action`/`plan` in `claude_agent.py`, `triage` in `claude_triage.py`,
  `propose_assertions` in `claude_enrich_agent.py`, `locate` in `alerts.py`, `propose` in
  `crawl_guide.py`, `locate` in `crawl_tabs.py`) are unaffected since they still call
  `self._ensure_client()` (or keep that name as a thin one-line wrapper, whichever reads
  better at each site).
- **Cover the shared implementation with its own unit test** (client-injection short-circuit,
  and build-once-then-reuse caching), then rely on each class's existing tests
  (`tests/test_claude_agent.py`, `tests/test_claude_triage.py`, `tests/test_alerts.py`,
  `tests/test_crawl_tabs.py`, and the `claude_enrich_agent.py` / `crawl_guide.py` coverage)
  to confirm no call site's behavior changed.

## Alternatives considered

- **Leave the six copies as they are.** Rejected: it is exactly the duplication this item
  exists to remove, and the fact that the report's initial count (three) already undercounted
  the real total (six) shows the copies are easy to lose track of as new AI classes are
  added.
- **Introduce a shared base class that all six `Claude*` classes inherit from**, hoisting not
  just `_ensure_client` but the repeated `__init__` assignments (`self._client`, `self._ai`,
  `self._redactor`, `self._model` via `resolve_model`) too. Considered as a larger version of
  this fix. Deferred for this item's scope: the six classes implement different `Protocol`s
  (`TriageAgent`, `EnrichmentAgent`, `AlertLocator`, `ActionProposer`, `TabLocator`, and the
  action-proposing agent behind `ClaudeAgent`) with different constructor extras (`max_tokens`,
  `max_actions`, presence/absence of `redactor`), so a base class either grows optional
  parameters to cover every subclass's needs or only partially deduplicates — a plain shared
  function composes more cleanly with structurally unrelated `Protocol` implementations
  without forcing an inheritance relationship between them.
- **Leave `_ensure_client` duplicated but add a lint rule (e.g. a custom `ruff` check or a
  grep-based test) that fails when a new byte-identical copy appears.** Rejected as the
  primary fix, for the same reason as the analogous alternative on the flag-mirror item: a
  check catches new duplication after the fact but does not remove the six existing copies or
  the need to keep them in sync by hand.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add the shared client-init-and-cache implementation to `bajutsu/anthropic_client.py`
- [ ] Replace the six `_ensure_client` copies (`claude_agent.py`, `claude_triage.py`,
      `claude_enrich_agent.py`, `alerts.py`, `crawl_guide.py`, `crawl_tabs.py`) with calls to
      the shared implementation
- [ ] Add a unit test for the shared implementation (injection short-circuit, build-once
      caching) covering the behavior the six copies relied on

No PR has landed yet.

## References

- `bajutsu/claude_agent.py:331` (`ClaudeAgent._ensure_client`)
- `bajutsu/claude_triage.py:216` (`ClaudeTriageAgent._ensure_client`)
- `bajutsu/claude_enrich_agent.py:187` (`ClaudeEnrichmentAgent._ensure_client`)
- `bajutsu/alerts.py:182` (`ClaudeAlertLocator._ensure_client`)
- `bajutsu/crawl_guide.py:352` (`ClaudeActionProposer._ensure_client`)
- `bajutsu/crawl_tabs.py:183` (`ClaudeTabLocator._ensure_client`)
- `bajutsu/anthropic_client.py:72` (`make_client`) — the factory all six already call and
  where the shared implementation would live
- Related: [BE-0021](../../implemented/BE-0021-ai-triage/BE-0021-ai-triage.md) (AI triage),
  [BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
  (AI data sovereignty)
- Originates from the 2026-07-02 codebase-analysis report (technical debt).
