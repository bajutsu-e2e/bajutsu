**English** · [日本語](BE-0140-dedupe-claude-client-init-ja.md)

# BE-0140 — Deduplicate Claude client initialization

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0140](BE-0140-dedupe-claude-client-init.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0140") |
| Implementing PR | [#613](https://github.com/bajutsu-e2e/bajutsu/pull/613) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

Six `Claude*` classes across the AI authoring and investigation paths each carry a private
`_ensure_client` method with the same three lines. The method returns the injected client if one
exists, and otherwise builds a client and caches it. This item hoists that shared logic into the
module these classes already import from. A behavior change to client construction or caching then
needs one edit instead of six.

## Motivation

`_ensure_client` is byte-identical in six places:

- `bajutsu/claude_agent.py:331` (`ClaudeAgent`)
- `bajutsu/claude_triage.py:216` (`ClaudeTriageAgent`)
- `bajutsu/claude_enrich_agent.py:187` (`ClaudeEnrichmentAgent`)
- `bajutsu/alerts.py:182` (`ClaudeAlertLocator`)
- `bajutsu/crawl_guide.py:352` (`ClaudeActionProposer`)
- `bajutsu/crawl_tabs.py:183` (`ClaudeTabLocator`)

That is twice the three call sites the initial codebase-analysis report counted.

```python
def _ensure_client(self) -> Any:
    if self._client is None:
        self._client = make_client(ai=self._ai)
    return self._client
```

All six classes already import `make_client` from `bajutsu/anthropic_client.py:72`. `make_client`
itself short-circuits on an injected `client` (`if client is not None: return client`). So
`_ensure_client` adds one thing that `make_client` does not already do: it memoizes the
constructed client on `self._client`. Without the memo, a class would reopen an SDK client, and
reread the API key environment variable, on every call.

Each of the six `__init__` methods also repeats the same `self._client = client; self._ai = ai`
assignment that `_ensure_client` closes over.

Nothing ties the six copies together. A future change to client construction or caching (a new
provider, a different memoization key, added logging) forces a contributor to find and edit all
six by hand. A seventh AI class added later will most likely copy the pattern again, because
nothing points to a shared home for it.

The fix stays entirely within the AI authoring and investigation paths (`record`, `enrich`,
`triage`, alert dismissal, crawl). None of the six classes is reachable from the deterministic
`run` / CI gate.

## Detailed design

The refactor preserves behavior. Each of the six classes keeps its current constructor signature
(`client`, `model`, `ai`, `redactor`, and per-class extras such as `max_tokens`). Each keeps the same
lazy-build-then-cache semantics for `_client`. The duplication goes away because the six classes
call one shared implementation instead of six private copies:

- **Hoist the caching wrapper next to `make_client`.** Add one function or small mixin in
  `bajutsu/anthropic_client.py`, beside `make_client`. That module is already the single place these
  classes import their client-construction logic from. The new code takes `(client, ai)` and returns
  the same three-line memoized-build result that `_ensure_client` computes today. A plain function
  (for example, one taking `self` or a `(client, ai)` pair and returning the value to assign)
  avoids adding a base class to six otherwise unrelated `Protocol` implementations. A mixin is the
  alternative if the constructors of the six classes turn out easier to share too (see
  *Alternatives considered*).
- **Replace each of the six `_ensure_client` methods with a call to the shared implementation.**
  Each class keeps its own `self._client` attribute, so the memoization stays per-instance. The
  class delegates the "build if absent" decision to the shared function. The change is mechanical:
  one line of body per class. The six call sites are unaffected:
  - `next_action` and `plan` in `claude_agent.py`
  - `triage` in `claude_triage.py`
  - `propose_assertions` in `claude_enrich_agent.py`
  - `locate` in `alerts.py`
  - `propose` in `crawl_guide.py`
  - `locate` in `crawl_tabs.py`

  They still call `self._ensure_client()`, or the method stays as a thin one-line wrapper,
  whichever reads better at each site.
- **Cover the shared implementation with its own unit test.** The test checks the client-injection
  short-circuit and the build-once-then-reuse caching. Each class's existing tests then confirm that
  no call site's behavior changed: `tests/test_claude_agent.py`, `tests/test_claude_triage.py`,
  `tests/test_alerts.py`, `tests/test_crawl_tabs.py`, and the `claude_enrich_agent.py` and
  `crawl_guide.py` coverage.

## Alternatives considered

- **Leave the six copies as they are.** Rejected, because it keeps exactly the duplication this item
  exists to remove. The report's initial count (three) already undercounted the real total (six),
  which shows that the copies are easy to lose track of as new AI classes are added.
- **Introduce a shared base class that all six `Claude*` classes inherit from.** The base class
  would hoist not just `_ensure_client` but the repeated `__init__` assignments (`self._client`,
  `self._ai`, `self._redactor`, and `self._model` via `resolve_model`) too. This is a larger version
  of the fix. It is deferred for this item's scope. The six classes implement different `Protocol`s
  (`TriageAgent`, `EnrichmentAgent`, `AlertLocator`, `ActionProposer`, `TabLocator`, and the
  action-proposing agent behind `ClaudeAgent`). Their constructor extras differ too (`max_tokens`,
  `max_actions`, and the presence or absence of `redactor`). A base class would either grow optional
  parameters to cover every subclass or deduplicate only part of the code. A plain shared function
  composes more cleanly with structurally unrelated `Protocol` implementations, and it forces no
  inheritance relationship between them.
- **Leave `_ensure_client` duplicated, and add a lint rule that fails when a new byte-identical copy
  appears.** The rule could be a custom `ruff` check or a grep-based test. Rejected as the primary
  fix, for the same reason as the analogous alternative on the flag-mirror item. A check catches new
  duplication after the fact. It neither removes the six existing copies nor ends the need to keep
  them in sync by hand.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add the shared client-init-and-cache implementation to `bajutsu/anthropic_client.py`
- [x] Replace the six `_ensure_client` copies (`claude_agent.py`, `claude_triage.py`,
      `claude_enrich_agent.py`, `alerts.py`, `crawl_guide.py`, `crawl_tabs.py`) with calls to
      the shared implementation
- [x] Add a unit test for the shared implementation (injection short-circuit, build-once
      caching) covering the behavior the six copies relied on

- 2026-07-03 ([#613](https://github.com/bajutsu-e2e/bajutsu/pull/613)): Added `ensure_client` +
  the `CachesClient` structural Protocol next to `make_client`, memoizing the built client on the
  instance; replaced the six byte-identical `_ensure_client` bodies with `return ensure_client(self)`;
  covered the shared helper with injection-short-circuit and build-once-and-reuse tests.

## References

- `bajutsu/claude_agent.py:331` (`ClaudeAgent._ensure_client`)
- `bajutsu/claude_triage.py:216` (`ClaudeTriageAgent._ensure_client`)
- `bajutsu/claude_enrich_agent.py:187` (`ClaudeEnrichmentAgent._ensure_client`)
- `bajutsu/alerts.py:182` (`ClaudeAlertLocator._ensure_client`)
- `bajutsu/crawl_guide.py:352` (`ClaudeActionProposer._ensure_client`)
- `bajutsu/crawl_tabs.py:183` (`ClaudeTabLocator._ensure_client`)
- `bajutsu/anthropic_client.py:72` (`make_client`) — the factory all six already call and
  where the shared implementation would live
- Related: [BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage.md) (AI triage),
  [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
  (AI data sovereignty)
- Originates from the 2026-07-02 codebase-analysis report (technical debt).
