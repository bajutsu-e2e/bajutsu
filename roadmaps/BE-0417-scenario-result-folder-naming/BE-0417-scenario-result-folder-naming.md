**English** · [日本語](BE-0417-scenario-result-folder-naming-ja.md)

# BE-0417 — Name a scenario's evidence directory after its source file

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0417](BE-0417-scenario-result-folder-naming.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0417") |
| Implementing PR | [#1977](https://github.com/bajutsu-e2e/bajutsu/pull/1977) |
| Topic | Codebase quality & technical debt |
| Related | [BE-0200](../BE-0200-run-id-contract/BE-0200-run-id-contract.md) |
<!-- /BE-METADATA -->

## Introduction

Each [scenario](../../docs/glossary.md#scenario-authoring) in a run keeps its evidence under its own
`sid` directory inside `runs/<runId>/`. Today `sid` is `f"{i:02d}-{scenario_slug(s.name)}"`: a
run-order index plus a slug of the scenario's internal `name:` field
([`bajutsu/common/runner/pipeline.py:269`](../../bajutsu/common/runner/pipeline.py)).

This item changes where the slug half comes from. The slug will come from the stem of the file the
scenario was actually loaded from, not from the `name:` field. The stem is the file name without its
`.yaml` extension. For example, `sid` becomes `00-login_flow` instead of
`00-login-succeeds-with-a-valid-password`. The `{i:02d}-` index prefix is unchanged.

### Not doing

- **The top-level run directory (`runs/<runId>/`, a UTC timestamp) is unchanged.** Three readers
  rely on its contract that lexicographic order equals chronological order
  ([BE-0200](../BE-0200-run-id-contract/BE-0200-run-id-contract.md)):
  - `latest_run()` in `bajutsu/analysis/trace.py`
  - the run and crawl listings of `serve` (`bajutsu/serve/helpers.py`)
  - the prior-verdict lookup of `run/notify`

  None of them changes here. Renaming the run directory after a scenario file also has no coherent
  answer for a multi-scenario or multi-file run. A `runs/<runId>/` holds `manifest.json`,
  `junit.xml`, `ctrf.json`, and `report.html` for every scenario the run executed, not for one.
- **No new duplicate-suffix counter is introduced.** The existing `{i:02d}-` index prefix already
  keeps `sid` unique within one run. Two scenarios that land on the same file-derived name never
  collide. That covers two files sharing a stem and one file whose `scenarios:` list holds more than
  one scenario. See *Detailed design* and *Alternatives considered*.
- **A `Scenario` with no known source file keeps today's behavior.** `scenario_slug(s.name)` stays
  as the fallback for a scenario built directly in memory, outside the file loaders. Test fixtures
  are an example. This item does not affect them.
- **The `--browsers` cross-browser matrix's `<engine>/<sid>/` layer is unchanged.** Only what `sid`
  itself is built from changes.
- **The earlier name-keyed collision in `_matrix()` is out of scope.** `bajutsu/common/report/manifest.py`
  built the `--browsers` matrix summary keyed by scenario name. Two same-named scenarios in one
  engine therefore overwrote each other's matrix cell. That was a separate issue this item did not
  touch. [#1970](https://github.com/bajutsu-e2e/bajutsu/pull/1970) has since fixed it independently,
  before this item's implementation began: `_matrix()` now disambiguates same-named scenarios with a
  `(N)` suffix. The collision no longer applies.

## Motivation

Today the slug in `sid` comes from the scenario's internal `name:` field, not from the file the
scenario was authored in. The `name:` field is free-form prose and need not resemble the file name.
A scenario authored in `login_flow.yaml` can carry `name: "Login succeeds with a valid password"`.
That name slugs to `00-login-succeeds-with-a-valid-password`.

To find one scenario's results in `runs/<runId>/`, an operator must open `manifest.json`. The
operator then matches its `scenario` field against the file they have in mind. No path leads from
`login_flow.yaml` straight to the directory that holds its evidence.

That friction is the request behind this item. Locating one scenario's own results among several is
harder than it needs to be. It is hardest when an author re-runs the same scenario file repeatedly
while writing it or while chasing a continuous integration (CI) failure.

Naming the slug of `sid` after the scenario's own file removes the detour. An operator goes from the
file they already know to the directory that holds its evidence, without opening `manifest.json`.

## Detailed design

1. **`Scenario.source_stem` (new).** This is a private attribute, set only at load time, on
   [`Scenario`](../../bajutsu/common/scenario/models/scenario/scenario.py)
   (`_source_stem: str | None = PrivateAttr(default=None)`). A read-only `source_stem` property
   exposes it. It is a `PrivateAttr`, not an ordinary field, because it is provenance that the
   loader fills in after parsing. A scenario's own YAML never declares it. It must never appear in
   `model_dump()`. If it leaked into a re-serialized scenario file, a load-time detail would become
   part of the scenario's authored schema. `record`, `audit`, or a future scenario-editing feature
   might produce such a file.
2. **The two device-free loaders that read a scenario from a file set it.** Each loader sets
   `source_stem` on the *final* expanded `list[Scenario]`, right before returning. By then
   `expand_data` has already made its per-CSV-row copies. Every row that a data-driven scenario
   expands into therefore carries the same source stem. The two loaders are:
   - `_expand_file()` in [`bajutsu/run/cli.py:161-200`](../../bajutsu/run/cli.py), the
     setup-prefixing loader of `run` itself.
   - `load_expanded_scenarios()` in
     [`bajutsu/common/scenario/load_expanded.py:63-89`](../../bajutsu/common/scenario/load_expanded.py).
     `audit`, `trace --explain`, `coverage`, and the coverage view of the serve Web UI share it.
     Through `load_scenarios_dir()`, any device-free reader of a whole suite shares it too.
3. **`sid` construction reads the stem through a narrow sanitizer, with a fallback.** Two sites
   change: [`bajutsu/common/runner/pipeline.py:269`](../../bajutsu/common/runner/pipeline.py) in
   `_ScenarioRunner.run_one`, and `:1173` in `_cancelled_pass` of the cross-browser matrix. Both
   change from `sid = f"{i:02d}-{scenario_slug(s.name)}"` to
   `sid = f"{i:02d}-{sanitize_source_stem(s.source_stem) if s.source_stem else scenario_slug(s.name)}"`.

   `sid` is not only a filesystem path segment. Two consumers interpolate it unescaped:
   - the `src=` and `href=` asset links of `report.html`
     ([`bajutsu/templates/report.html.j2:32,43,47`](../../bajutsu/templates/report.html.j2))
   - the `/runs/<runId>/<sid>/…` fetches of serve

   A raw stem such as `login#1` or `a?b` would silently truncate every relative link at the `#` or
   `?`. The `[0-9a-z-]+` output of `scenario_slug()` makes that breakage impossible today (see
   *Alternatives considered*).

   **`sanitize_source_stem()`** is new. It sits beside `scenario_slug()` in
   [`bajutsu/common/orchestrator/types/_functions.py`](../../bajutsu/common/orchestrator/types/_functions.py).
   It replaces with `_` only the characters that are unsafe in an unescaped HTML attribute or URL
   path segment, meaning anything outside `[A-Za-z0-9_.-]`. A plain stem such as `login_flow` stays
   unchanged. Unlike `scenario_slug()`, it leaves `_` and `.` alone. In the common case, `sid` still
   reads identically to the source file's name. Only a stem that carries an unsafe character
   changes. The `{i:02d}-` prefix stays exactly as it is today, so `sid` stays unique within one run
   without any new counter.
4. **Docs.** The "Output layout" section of [`docs/reporting.md`](../../docs/reporting.md) and
   [`docs/ja/reporting.md`](../../docs/ja/reporting.md) gains two changes.
   - A line that names how `sid` is derived: the source file's stem, or the scenario's `name:` field
     when no source file is known. It sits beside the `runId` format that the section names today
     (`docs/reporting.md:30-32`).
   - The `<sid>/` level in the layout tree. The tree omits it today and hangs `<stepId>/` directly
     off `runs/<runId>/`.
5. **Tests.**
   - `tests/runner/test_pipeline.py`: a file-loaded scenario's `sid` uses the source file's stem. A
     `Scenario` built directly, with no `source_stem` set, keeps today's `scenario_slug(s.name)`
     `sid`.
   - A loader-level test for each of the two loaders in unit 2. It asserts that `source_stem`
     matches the loaded file's stem. It includes the data-driven-expansion case, where every
     expanded row carries the same stem.
   - A test that `source_stem` never appears in `Scenario.model_dump()` output.
   - A unit test for `sanitize_source_stem()`. A plain stem such as `login_flow` passes through
     unchanged. A stem with an HTML- or URL-unsafe character (`login#1`, `a?b`) comes back with only
     that character replaced.

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Rename the top-level run directory (`runs/<runId>/`) itself after the scenario file | It breaks the contract that [BE-0200](../BE-0200-run-id-contract/BE-0200-run-id-contract.md) established: lexicographic order equals chronological order. `latest_run()`, the run and crawl listings of `serve`, and the prior-verdict lookup of `run/notify` all rely on it, so a replacement for all of them would be needed. It also has no coherent answer for a run that executes more than one scenario or scenario file, because one `runs/<runId>/` directory would need one name for every scenario it holds. |
| Drop the `{i:02d}-` index prefix and rely on a duplicate-suffix counter for uniqueness | The prefix already makes `sid` unique within a run at no extra cost. Keeping it also lets a directory listing show run order at a glance, which a bare counter loses. The prefix does not make same-file scenarios any more identifiable than a counter would, though. Under this item's own design, two scenarios in one `login_flow.yaml` become `00-login_flow` and `01-login_flow`. Only the run-order index tells them apart, and a `login_flow` / `login_flow_2` counter would carry that just as well. What actually tells same-file scenarios apart by name is `scenario_slug(s.name)`. That is today's behavior, and it is this item's fallback when no source file is known. Neither the index prefix nor a duplicate counter provides that property on its own. |
| Normalize the file stem through the existing `scenario_slug()` sanitizer | It would turn `login_flow.yaml` into `login-flow`. In the common case the result would no longer match the file name, which defeats the point of naming `sid` after the file. `sanitize_source_stem()` (see *Detailed design*) gets the same filesystem, HTML, and URL safety at a narrower cost, because it leaves `_` and `.` untouched. |
| Use the source stem completely raw, with no sanitization | A scenario file's name is already a valid filesystem path segment. But `sid` also travels unescaped into the asset links of `report.html` and the `/runs/<runId>/<sid>/…` routes of serve (see *Detailed design*). An unsanitized stem such as `login#1` or `a?b.yaml` would silently truncate those links at the `#` or `?`, and nothing in `make check` would catch it. `sanitize_source_stem()` closes that gap and keeps the common case (`login_flow`) unchanged. |
| Thread a parallel `list[str]` of source stems alongside `list[Scenario]` through `run_all`, `run_and_report`, `run_matrix_and_report`, and their callers, instead of attaching the stem to `Scenario` | It touches every call site that carries a `list[Scenario]` today: `bajutsu/run/cli.py`, `bajutsu/analysis/cli/audit.py`, and the pipeline test suite. The data only ever travels with its scenario. A private, load-time-only field on `Scenario` keeps the change local to the two loaders and the one read site. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `Scenario.source_stem`: a private `PrivateAttr`, a read-only `source_stem` property,
  and a `set_source_stem()` setter (a public setter rather than a direct `_source_stem` assignment
  from the loaders, since `ruff`'s `SLF001` forbids a private-attribute write from outside the
  class — the property stays read-only as designed).
- [x] Unit 2 — `_expand_file()` (`bajutsu/run/cli.py`) and `load_expanded_scenarios()`
  (`bajutsu/common/scenario/load_expanded.py`) both call `set_source_stem()` on every scenario in
  their final expanded list, right before returning.
- [x] Unit 3 — `sanitize_source_stem()` added beside `scenario_slug()`; `run_one` and
  `_cancelled_pass` in `bajutsu/common/runner/pipeline.py` build `sid` from `s.source_stem` through
  it (via one shared `_evidence_sid()` helper, so the two sites can't drift), falling back to
  `scenario_slug(s.name)` when no source file is known. Deviation from the literal design: the
  replaced-character class is `[^\w.-]` (Unicode word characters), not the letter-of-the-spec
  `[^A-Za-z0-9_.-]` — the ASCII-only class silently collapsed a Japanese-named scenario file's stem
  to a run of underscores, which this codebase's own bilingual convention (CLAUDE.md) makes a
  realistic case, and defeats this item's own motivation for exactly that file. `\w` still replaces
  every character the design's own rationale (unescaped HTML attribute / URL path segment safety)
  cites — `#`, `?`, `/`, whitespace — while keeping a non-ASCII stem identifiable.
- [x] Unit 4 — `docs/reporting.md` / `docs/ja/reporting.md`'s Output layout gained the `<sid>/`
  level (it previously hung `<stepId>/` directly off `runs/<runId>/`, though the runtime already
  nests evidence that way) and a line naming `sid`'s derivation.
- [x] Unit 5 — Tests for all of the above, including the `_cancelled_pass` matrix path, the
  data-driven-expansion case for both loaders, `sanitize_source_stem()`'s character replacement,
  and that `source_stem` never appears in `model_dump()`.

Log:

- [#1977](https://github.com/bajutsu-e2e/bajutsu/pull/1977) — All 5 units, completing the item.
  Added `Scenario.source_stem` (a load-time `PrivateAttr` never leaked by `model_dump()`), the two
  device-free loaders setting it on their final expanded scenario list, and `sanitize_source_stem()`
  beside `scenario_slug()`. `pipeline.py`'s `run_one` and `_cancelled_pass` now build `sid` from the
  source file's stem through a shared `_evidence_sid()` helper, falling back to `scenario_slug(name)`
  when no source file is known. One deviation from the literal design: the sanitizer's replaced
  character class is `[^\w.-]` (Unicode word characters), not the spec's ASCII-only
  `[^A-Za-z0-9_.-]` — found in a self-review pass, since the ASCII-only class silently collapsed a
  Japanese-named scenario file's stem to a run of underscores, defeating this item's own motivation
  for exactly that file. Also corrected a "Not doing" bullet that named a `_matrix()` collision
  already fixed independently by #1970 before this PR began.

## References

- [BE-0200 — Make the run-id format a single named contract](../BE-0200-run-id-contract/BE-0200-run-id-contract.md)
- [`bajutsu/common/scenario/models/scenario/scenario.py`](../../bajutsu/common/scenario/models/scenario/scenario.py)
- [`bajutsu/common/scenario/load_expanded.py`](../../bajutsu/common/scenario/load_expanded.py)
- [`bajutsu/run/cli.py`](../../bajutsu/run/cli.py)
- [`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py)
- [`bajutsu/common/orchestrator/types/_functions.py`](../../bajutsu/common/orchestrator/types/_functions.py)
- [`bajutsu/templates/report.html.j2`](../../bajutsu/templates/report.html.j2)
- [`docs/reporting.md`](../../docs/reporting.md)
