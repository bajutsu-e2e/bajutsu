**English** · [日本語](BE-0388-test-suite-mypy-typing-ja.md)

# BE-0388 — Type-check the test suite under mypy

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0388](BE-0388-test-suite-mypy-typing.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0388") |
| Implementing PR | [#1760](https://github.com/bajutsu-e2e/bajutsu/pull/1760), [#1763](https://github.com/bajutsu-e2e/bajutsu/pull/1763), [#1766](https://github.com/bajutsu-e2e/bajutsu/pull/1766), [#1768](https://github.com/bajutsu-e2e/bajutsu/pull/1768) |
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

1. **Add a `typecheck-tests` Makefile target that runs the relaxed check, outside `make check`
   (`tests` joins the gate in the final step).** The target runs
   `mypy --allow-untyped-defs --no-warn-unused-ignores tests`. `--allow-untyped-defs` keeps a bare
   `def test_x():` valid — annotating every test's return type as `-> None` adds no safety, since
   pytest never inspects it. `--no-warn-unused-ignores` stays in place only until the existing
   `# type: ignore` comments are swept; each one is removed or replaced with a narrowed
   `# type: ignore[<code>]` naming the reason, in the same pass that finds it, not left for later.
2. **Clear the `attr-defined` findings that come from patching a module's own import**, module by
   module. Each count below is that directory's *total* mypy error count under the relaxed run,
   not its `attr-defined` subset, because a directory is cleared outright before the next one
   starts. Start with the smallest directories — `tests/scenario/` at 7 errors and `tests/report/`
   at 14 — before the largest: `tests/serve/` at 196, and the flat files directly under `tests/` at
   880, led by `test_crawl.py` at 200 and `test_record.py` at 75. Every count in this section and
   in *Progress* below was re-measured under the relaxed run when the work started, which is why
   each differs from the corresponding figure in *Motivation* — those count the unrelaxed run, over
   the suite as it stood when the author wrote the proposal.

   Each fix patches the call the test actually cares about instead of reaching into the target
   module's private import — `patch.object(module, "sleep")` on a name the module already exposes,
   or patching the higher-level method that calls `time.sleep` rather than the standard-library call
   underneath it.
3. **Triage the remaining `arg-type` / `call-arg` findings as real signal, not noise.** A test that
   passes a `dict[str, str]` where the call site's signature expects `dict[str, str | None] | None`
   is either exercising a case the current signature no longer allows, or the signature is stricter
   than the code actually needs — each instance gets a test fix or a production-code type widening,
   never a blanket `# type: ignore`.

Step 1 gives the two relaxations as command-line flags rather than as a `[[tool.mypy.overrides]]`
block scoped to `tests.*`, because no per-module pattern can select the test suite. `tests/` carries
no `__init__.py` — the suite imports its helper modules by bare name, the way pytest's prepend
import mode puts `tests/` on `sys.path` — so mypy names every module under `tests/` by basename
alone, and the pattern `tests.*` matches nothing. mypy refuses the pattern `test_*` outright,
because `*` must stand for a whole dotted component. Relaxing the two settings globally and
re-tightening them for the gate's existing targets fails for the same reason: mypy names the modules
under `demos/` and `scripts/` by basename too, so re-tightening would have to list every one.

Basename naming carries a second consequence, recorded here because it becomes a hard failure once
`tests` joins the gate: no two files under `tests/` may share a name. mypy aborts a run with
`Duplicate module named …` and reports no errors at all, so an ordinary new test file could turn
`make check` red with a message that points nowhere near its cause. No collision exists today, and
nothing but this note keeps it that way.

The second invocation gets its own `--cache-dir`. Both relaxed settings are per-module, and mypy
records the options in every module's cache metadata, so two runs sharing one directory each abandon
the other's metadata — "options differ" — and re-analyse from source. That covers all of `bajutsu/`
as well, which `tests` imports, so a shared cache would make the pair pay two cold analyses and the
next strict run a third. With separate directories both stay incremental. The two runs differ in
their flags and their cache alone; the strict `[tool.mypy]` configuration is the one the gate
already applies to `bajutsu demos scripts`.

Once every directory in `tests/` is clean, fold `typecheck-tests` into the `typecheck` target with
`--no-warn-unused-ignores` dropped, so the relaxed run joins the gate alongside
`mypy bajutsu demos scripts` from then on. The continuous integration (CI) workflow needs no edit of
its own: its type step already calls `make typecheck`.

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
  deciding that. The per-directory rollout in *Detailed design*, tracked by the *Progress* checklist
  below, stays visible instead.
- **Leave `tests/` untyped and rely on code review** — the status quo; rejected because it was already
  the status quo BE-0067 flagged as a gap, and review alone did not catch the `attr-defined` /
  `arg-type` classes of drift this baseline run surfaced.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add the `typecheck-tests` Makefile target — `mypy --allow-untyped-defs
  --no-warn-unused-ignores tests` — without yet putting it on the `make check` gate.
- [x] Clear `tests/ai/` (already clean, 0 errors) and `tests/scenario/` (7 errors) — confirm the
  relaxed run is sufficient before touching a larger directory.
- [x] Clear `tests/report/` (14 errors) and `tests/orchestrator/` (79 errors).
- [x] Clear `tests/runner/` (126 errors).
- [x] Clear `tests/serve/` (196 errors, 124 files — the largest single directory).
- [x] Clear the flat files directly under `tests/` (880 errors across 197 files), starting with
  `test_crawl.py` (200), `test_record.py` (75), and `test_intervals.py` (48).
- [x] Sweep every remaining `unused-ignore` finding; remove or justify each `# type: ignore`.
- [x] Fold `typecheck-tests` into the `typecheck` Makefile target and drop
  `--no-warn-unused-ignores` now that the sweep above is complete.

**Log**

- Landed the `typecheck-tests` target
  ([#1760](https://github.com/bajutsu-e2e/bajutsu/pull/1760)). The relaxed settings became command-line flags on a second mypy invocation rather than the
  `[[tool.mypy.overrides]]` block this item first proposed, because mypy names every module under
  `tests/` by basename and so no per-module pattern selects the suite; *Detailed design* above
  records the reasoning. Re-measuring at that point put the relaxed run at 1,302 errors across 158
  files, against the 1,361 across 159 that *Motivation* reports for the unrelaxed run at proposal
  time.
- Cleared `tests/scenario/`, `tests/report/`, `tests/orchestrator/`, and `tests/runner/` — 226
  findings, leaving 1,076 across `tests/serve/` and the flat files
  ([#1763](https://github.com/bajutsu-e2e/bajutsu/pull/1763)). Four shapes covered nearly every one,
  and none was a defect in the code under test:
  - a test helper annotated `object` where it returns a `Scenario`, an `Effective`, or a list of
    `Actuation`s, so every assertion downstream of it lost its type;
  - a deliberately partial driver or collector stub, which now subclasses `FakeDriver` /
    `FakeNetworkCollector` and so satisfies the protocol for real, rather than being cast at each
    call site;
  - a test reading a module's own `time` / `subprocess` / `urllib` import to patch it, which now
    patches that standard-library module directly — the same object, so the patch is unchanged, and
    the read no longer trips strict mode's implicit-reexport check;
  - a pydantic model constructed by Python field name, such as `test_runner=`, where mypy — without
    the plugin this item leaves out of scope — sees only the alias `testRunner=`.

  The 19 `# type: ignore` comments these fixes made stale went with them, rather than waiting for
  the final sweep: leaving one on a call whose siblings no longer carry it would suppress a genuine
  future error at that one site while the others reported it.

  Two assertions changed meaning rather than only their types. `ScreenTransition(name="detail")`
  passed a field the model does not declare, which `extra="ignore"` silently dropped; it now sets
  the `kind` field it meant. `test_visual_assertion_with_exclude_and_threshold` indexed a
  `list[ExcludeRegion | SelectorRegion]` and read `.w`, a field only `ExcludeRegion` carries, and
  now asserts that variant before reading it.
- Cleared `tests/serve/` — 196 findings, leaving 880 in the flat files under `tests/`
  ([#1766](https://github.com/bajutsu-e2e/bajutsu/pull/1766)). The directory's dominant shape was a
  partial fake of a seam protocol, redefined once per file: nine near-identical in-memory
  `ObjectStore` fakes, each implementing a different four or five of the protocol's nine methods.
  `tests/serve/_shared.py`'s `FakeObjectStore` now implements the whole protocol, and the nine
  become subclasses overriding only what they specialize; `StubArtifactStore` does the same for the
  eleven-method `ArtifactStore`. That removed the duplication as well as the findings.

  Fourteen `# type: ignore` comments went stale as the stubs came to satisfy their protocols, and
  were dropped with them for the reason the previous slice records.

  One fake had drifted rather than merely being partial. `test_http_auth.py`'s `_FakeOAuth` declared
  a `fetch_login` the `OAuthClient` protocol no longer has, and lacked the `fetch_identity` that
  replaced it. Nothing failed, because those tests exercise only the redirect leg. Review alone did
  not catch the drift; this item's baseline did.
- Cleared the 880 findings in the flat files under `tests/`, swept the 131 `# type: ignore` comments
  the clean-up left stale, and folded the run into `typecheck`, which now covers `tests` on every
  `make check` and every CI run ([#1768](https://github.com/bajutsu-e2e/bajutsu/pull/1768)). The item
  is complete: `mypy --allow-untyped-defs tests` reports zero findings across 372 source files, and
  the suite's 6,610 tests pass unchanged. The folded target keeps the separate `--cache-dir` the
  first slice introduced, so neither run abandons the other's cache metadata.

  Two settings were relaxed while the migration ran; only one survives it.
  `--no-warn-unused-ignores` is gone, because the sweep is what it was holding open.
  `--allow-untyped-defs` stays, for the reason *Detailed design* gives. `disallow_incomplete_defs`
  was deliberately **not** relaxed: a test that annotates some of its parameters and not the rest
  is not the conventional pattern this item set out to accommodate, and the 106 instances were
  annotated instead — mostly a fixture whose type the author had not yet written
  (`capsys: pytest.CaptureFixture[str]`).

  The flat files' dominant shapes matched the directories' and the item's prediction. Two more
  emerged at this scale: a callback declared `Callable[[FakeDriver], None]` where the seam takes
  `Callable[[Driver], None]`, which contravariance rules out — 41 in `test_crawl.py` alone,
  each now declaring the seam's own parameter type and narrowing inside; and a lambda carrying a
  default argument, which mypy cannot infer against a target signature, replaced by a typed closure
  or by dropping the default the seam never omits.

## References

- [BE-0067 — Code-quality gate hardening](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
  — added `scripts/` to `typecheck` and explicitly deferred `tests/` as needing this follow-up.
- [pyproject.toml](../../pyproject.toml), [Makefile](../../Makefile) — the `[tool.mypy]` config and
  the `typecheck` target this item extends.
- [Pydantic mypy plugin docs](https://docs.pydantic.dev/latest/integrations/mypy/) — the plugin
  discussed in *Motivation* and left out of this item's scope.
