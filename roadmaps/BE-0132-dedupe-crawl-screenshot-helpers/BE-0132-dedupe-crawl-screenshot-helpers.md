**English** · [日本語](BE-0132-dedupe-crawl-screenshot-helpers-ja.md)

# BE-0132 — Deduplicate crawl screenshot helpers

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0132](BE-0132-dedupe-crawl-screenshot-helpers.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0132") |
| Implementing PR | [#629](https://github.com/bajutsu-e2e/bajutsu/pull/629) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

Crawl-adjacent code captures a best-effort screenshot of the current screen in more than one place.
Two of those capture helpers have drifted into exact duplicates, and both hide their own failures.

This item unifies the two helpers into one and makes a failed capture surface instead of vanishing.
That follows the determinism-first principle of failing loudly rather than silently swallowing
errors.

## Motivation

`bajutsu/record.py:52` defines `_screenshot_bytes`, and `bajutsu/alerts.py:48` defines
`_screenshot_png`. Both have the identical body:

1. Write to a temp PNG path.
2. Read the file back as bytes.
3. Delete the temp file.
4. Return `None` on any exception.

`bajutsu/crawl_guide.py:82` and `bajutsu/enrich.py:92` both import and call `_screenshot_bytes` from
`record.py` for their own best-effort captures. The same logic is therefore already relied on from
four call sites through two separately maintained copies.

Beyond the duplication, both copies wrap the whole capture in a bare `except Exception: return
None`. That discards the actual failure (a stale simulator, a permissions error, a full disk) and
reports it identically to "there was genuinely nothing to capture."

Some callers skip the screenshot on `None`: an alert-locator prompt, a crawl-guide annotation, and
an enrichment record. These callers cannot tell a real failure from an expected absence. The
determinism-first principle rules out exactly this kind of ambiguity for anything the tool treats as
a signal.

The effort is size M/S: consolidating a self-contained ~10-line helper and threading the change
through its four call sites.

## Detailed design

The fix has two independent parts, deduplication and error handling. We apply them together, because
unifying the helper is the natural place to also fix how it fails:

- **Unify the two helpers into one.** Keep a single implementation, `_screenshot_bytes` in
  `bajutsu/record.py`, since it already has the wider call surface. `bajutsu/alerts.py` imports and
  uses it instead of defining `_screenshot_png`, and the duplicate is deleted. `screenshot_bytes` is
  a more accurate name than `_screenshot_png` for a function that returns bytes rather than a PNG
  object, so no call site needs a semantic adjustment.
- **Stop swallowing errors silently.** Replace the bare `except Exception: return None` with one of
  two behaviors:
  - Let the exception propagate, if every caller can tolerate a capture failure aborting the
    enclosing operation.
  - Log the exception before returning `None`, if callers genuinely need to treat a failed capture
    as best-effort and continue.

  Which of the two applies depends on what the call sites at `bajutsu/record.py:249`,
  `bajutsu/alerts.py:86`, `bajutsu/crawl_guide.py:82`, and `bajutsu/enrich.py:92` turn out to need
  once inspected. The point is that a real failure stays distinguishable from an empty capture
  instead of being merged into it.
- **Update the four call sites and their imports.** `bajutsu/alerts.py` switches from its local
  `_screenshot_png` to importing the unified helper. `bajutsu/crawl_guide.py` and
  `bajutsu/enrich.py` already import from `record.py`. They need no import change, only to keep
  working against the (possibly renamed) unified function.
- **Add or extend unit tests for the unified helper.** The tests cover the success path and the
  failure path (a driver whose `screenshot()` raises). They pin down the new error-surfacing
  behavior, so it does not regress back to silent swallowing.

## Alternatives considered

- **Keep both helpers but delegate one to the other.** Rejected. Delegating still leaves two names for one
  behavior and one extra layer of indirection, with no benefit over deleting the duplicate outright.
- **Fix the silent-swallow in place, in both copies, without deduplicating.** Rejected. It addresses
  the determinism concern but leaves the actual duplication. The next behavior change would still
  need to land in two files, which is the more durable risk (BE-0064, BE-0066, and BE-0092 all
  touched the surrounding crawl code without anyone noticing the duplicate).
- **Deduplicate but leave the bare `except Exception: return None`.** Rejected. It removes the
  maintenance burden but keeps the silent-failure problem the finding specifically calls out. A
  screenshot failure is exactly the kind of thing that should fail loudly rather than disappear into
  a `None`.
- **Move the unified helper to a new shared module** (for example a `screenshots.py`) rather than
  keeping it in `record.py`. Considered, but left as an implementation detail for whoever picks this
  up. `record.py` already owns the function, and three of the four call sites tolerate the existing
  import path. A new module is worth it only if it turns out to reduce coupling in practice.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unify `_screenshot_bytes` and `_screenshot_png` into one helper and delete the duplicate.
- [x] Replace the bare `except Exception: return None` with a failure path that surfaces the error.
- [x] Update the four call sites (`record.py`, `alerts.py`, `crawl_guide.py`, `enrich.py`) and their imports.
- [x] Add unit tests covering both the success and the failure path of the unified helper.

- 2026-07-04: Deleted `_screenshot_png` from `alerts.py` and pointed its call site at the single
  `_screenshot_bytes` in `record.py`; the helper now logs a warning on a capture failure (instead
  of a bare `except: return None`) so a real failure stays distinguishable from an empty capture.
  All four call sites remain best-effort, so no caller changes were needed beyond the import. As a
  reuse follow-through in the same theme, the byte-identical test `ShotDriver` fakes (duplicated
  across `test_alerts.py` and `test_crawl_guide.py`) were consolidated into `tests/conftest.py`.
- 2026-07-04: Review follow-up (#629) — moved the temp-file cleanup into a `finally` so a capture
  failure no longer leaks the `delete=False` PNG into the temp dir, and logged the failure with
  `exc_info=True` for a traceback; added a test asserting the temp file is removed on the failure
  path.

## References

- [`bajutsu/record.py:52`](../../bajutsu/record.py) — `_screenshot_bytes`, the helper this item
  keeps as the unified implementation.
- [`bajutsu/alerts.py:48`](../../bajutsu/alerts.py) — `_screenshot_png`, the byte-identical
  duplicate this item removes.
- [`bajutsu/crawl_guide.py:82`](../../bajutsu/crawl_guide.py),
  [`bajutsu/enrich.py:92`](../../bajutsu/enrich.py) — the other two call sites already using
  `_screenshot_bytes`.
- [BE-0064 — Parallel crawl across multiple simulators](../BE-0064-parallel-crawl/BE-0064-parallel-crawl.md)
  — crawl code the unified helper's callers run under.
- [BE-0066 — Web crawl (Playwright backend)](../BE-0066-web-crawl/BE-0066-web-crawl.md)
  — the second backend this crawl code runs against.
- [BE-0092 — Extract the crawl coordinator into a class](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction.md)
  — the most recent structural change to the surrounding crawl code.
- Originates from the 2026-07-02 codebase-analysis report (technical debt).
