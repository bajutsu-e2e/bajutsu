**English** · [日本語](BE-XXXX-test-suite-mypy-typing-ja.md)

# BE-XXXX — Type-check the test suite under mypy

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-test-suite-mypy-typing.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

`make check`'s `typecheck` step runs mypy in strict mode over `bajutsu`, `demos`, and `scripts`
([BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)), but
`tests/` — 361 files, the largest source tree in the repository — carries none of it. This item
brings `tests/` under mypy through a phased, per-directory rollout with settings relaxed for the
patterns a pytest suite conventionally uses, closing the gap BE-0067 left open when it deferred
`tests/` as needing "a focused follow-up with relaxed per-module settings."

## Motivation

A baseline run of `mypy tests` (informational only; not yet part of the gate) reports 1,361 errors
across 159 of 361 files. Four error codes account for most of that count: 522 `arg-type` (a value's
type does not match the parameter it is passed to), 269 `attr-defined` (an attribute mypy cannot
see — most from a test reaching into an already-imported module to patch or read an internal, for
example `bajutsu.drivers.base.time.sleep`, which strict mode's implicit-reexport check treats as
private to that module), 139 `no-untyped-def` (a `def test_...():` with no return annotation, the
convention this repo's own tests already follow throughout), and 60 `unused-ignore` (a `# type:
ignore` comment that no longer suppresses anything). None of these four codes reports a defect in
the code under test; each reports a mismatch between strict mode's defaults and how a pytest suite
is conventionally written — mocks, monkeypatching, and untyped test functions.

Enabling mypy's pydantic plugin (`plugins = ["pydantic.mypy"]`, not configured today) shows both the
promise and the risk of a single global setting change. It resolves 46 of the 1,361 `tests/` errors:
calls such as `XcuitestConfig(test_runner=..., device_type=...)` construct a config by its Python
field name rather than its `testRunner` / `deviceType` alias, which the model's
`populate_by_name=True` accepts at runtime but which mypy, without the plugin, rejects. The same
change also introduces 9 new errors in `bajutsu/` itself, where production code constructs the same
kind of model by its alias instead (`OrgConfig(editorTeams=...)`,
`RequestMatch(urlMatches=..., pathMatches=...)`, `SystemAlertHandling(pollInterval=...)`), and the
plugin's synthesized constructor no longer accepts that form. A single configuration change trades
one class of false positive for another; landing it needs an audit of every affected call site, not
a drive-by edit to `pyproject.toml`.

## Detailed design

Roll `tests/` into `typecheck` in three steps, each its own PR so the gate never turns red
mid-migration:

1. **Add the relaxed override scoped to `tests.*` (config only — `tests` joins the `typecheck`
   target in the final step).** `disallow_untyped_defs = false` keeps a bare `def test_x():`
   valid — annotating every test's return type as `-> None` adds no safety, since pytest never
   inspects it. `warn_unused_ignores =
   false` stays in place only until the existing `# type: ignore` comments are swept; each one is
   removed or replaced with a narrowed `# type: ignore[<code>]` naming the reason, in the same pass
   that finds it, not left for later.
2. **Clear the `attr-defined` findings that come from patching a module's own import**, module by
   module. Each count below is that directory's *total* mypy error count, not its `attr-defined`
   subset (269 of the baseline's 1,361), because a directory is cleared outright before the next one
   starts. Start with the smallest directories — `tests/scenario/` at 7 errors and `tests/report/`
   at 17 — before the largest: `tests/serve/` at 228, and the flat files directly under `tests/` at
   902, led by `test_crawl.py` at 200 and `test_record.py` at 75.

   Each fix patches the call the test actually cares about instead of reaching into the target
   module's private import — `patch.object(module, "sleep")` on a name the module already exposes,
   or patching the higher-level method that calls `time.sleep` rather than the standard-library call
   underneath it.
3. **Triage the remaining `arg-type` / `call-arg` findings as real signal, not noise.** A test that
   passes a `dict[str, str]` where the call site's signature expects `dict[str, str | None] | None`
   is either exercising a case the current signature no longer allows, or the signature is stricter
   than the code actually needs — each instance gets a test fix or a production-code type widening,
   never a blanket `# type: ignore`.

Once every directory in `tests/` is clean, add `tests` to the `typecheck` Makefile target and the CI
step, so it runs alongside `bajutsu demos scripts` from then on.

The pydantic-plugin question stays out of this item's scope; see *Alternatives considered*.

## Alternatives considered

- **Turn on strict mode over all of `tests/` in one PR** — rejected: 1,361 errors landing at once is
  not reviewable, and fixes made under that pressure would be rushed rather than triaged.
- **Enable the pydantic mypy plugin as part of this item** — rejected for now: measured, it nets
  negative outside `tests/` (9 new `bajutsu/` errors for 46 `tests/` fixes) without an audit of every
  aliased-model call site across the codebase. Worth a follow-up item once that audit exists; this
  item does not depend on it.
- **Exclude the noisiest files from mypy permanently** — rejected: a per-file exclusion decays
  silently, since a new test added to an excluded file inherits the exclusion without anyone
  deciding that. A per-directory override with the *Progress* checklist below stays visible instead.
- **Leave `tests/` untyped and rely on code review** — the status quo; rejected because it was already
  the status quo BE-0067 flagged as a gap, and review alone did not catch the `attr-defined` /
  `arg-type` classes of drift this baseline run surfaced.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add the relaxed `tests.*` mypy override — `disallow_untyped_defs = false` and
  `warn_unused_ignores = false` — without yet adding `tests` to the `typecheck` target.
- [ ] Clear `tests/ai/` (already clean, 0 errors) and `tests/scenario/` (7 errors) — confirm the
  override is sufficient before touching a larger directory.
- [ ] Clear `tests/report/` (17 errors) and `tests/orchestrator/` (83 errors).
- [ ] Clear `tests/runner/` (124 errors).
- [ ] Clear `tests/serve/` (228 errors, 123 files — the largest single directory).
- [ ] Clear the flat files directly under `tests/` (902 errors across roughly 180 files), starting with
  `test_crawl.py` (200), `test_record.py` (75), and `test_intervals.py` (49).
- [ ] Sweep every remaining `unused-ignore` finding; remove or justify each `# type: ignore`.
- [ ] Add `tests` to the `typecheck` Makefile target and the CI step; drop the relaxed override's
  `warn_unused_ignores = false` now that the sweep above is complete.

## References

- [BE-0067 — Code-quality gate hardening](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
  — added `scripts/` to `typecheck` and explicitly deferred `tests/` as needing this follow-up.
- [pyproject.toml](../../pyproject.toml), [Makefile](../../Makefile) — the `[tool.mypy]` config and
  the `typecheck` target this item extends.
- [Pydantic mypy plugin docs](https://docs.pydantic.dev/latest/integrations/mypy/) — the plugin
  discussed in *Motivation* and left out of this item's scope.
