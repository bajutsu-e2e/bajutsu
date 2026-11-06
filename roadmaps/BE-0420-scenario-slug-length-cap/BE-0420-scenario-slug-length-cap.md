**English** · [日本語](BE-0420-scenario-slug-length-cap-ja.md)

# BE-0420 — Cap scenario evidence-directory slug length

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0420](BE-0420-scenario-slug-length-cap.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0420") |
| Implementing PR | [#1995](https://github.com/bajutsu-e2e/bajutsu/pull/1995) |
| Topic | Codebase quality & technical debt |
| Related | [BE-0417](../BE-0417-scenario-result-folder-naming/BE-0417-scenario-result-folder-naming.md), [BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu writes the evidence of each scenario under `runs/<runId>/<sid>/`. `_evidence_sid()`
([`bajutsu/common/runner/pipeline.py:82`](../../bajutsu/common/runner/pipeline.py)) builds `sid` from
a two-digit run-order index plus one of two slugs.

- A scenario loaded from a file gets `sanitize_source_stem(s.source_stem)`. That is the stem of the
  file itself, with unsafe characters replaced
  ([BE-0417](../BE-0417-scenario-result-folder-naming/BE-0417-scenario-result-folder-naming.md),
  **Implemented**).
- A scenario with no known source file falls back to `scenario_slug(s.name)`.

Neither function caps the length of its output. A long source-file stem or a long scenario `name`
produces an arbitrarily long `sid`. The evidence directory then fails to write.

This item adds a fixed length cap to both functions. The cap counts characters, not bytes.

`sanitize_source_stem()` preserves Unicode letters. A byte-oriented cap would cut a fullwidth or
Japanese name shorter than an ASCII name of the same length. Counting characters keeps the two the
same length. `scenario_slug()` reduces its input to ASCII first, where the two counts already
coincide.

Either way, the function now truncates an oversized slug instead of growing it without bound. `sid`
then stays under a safe length, whatever produced it.

### Not doing

- **The top-level run directory is unchanged.** `runs/<runId>/` is a timestamp in Coordinated
  Universal Time (UTC). This item touches the two slugs nested under it, and nothing else.
- **`Scenario.name` and `Scenario.source_stem` stay untruncated.**
  - `name` also feeds the `scenario` field of `manifest.json` and `report.html`. It feeds the
    row-suffix matching of `declared_name()` too. Serve uses that matching for its run pickers
    ([`bajutsu/common/scenario/expand.py:117`](../../bajutsu/common/scenario/expand.py)).
  - `source_stem` has one reader today, `_evidence_sid()` itself. This item still caps the slug it
    derives, not the field. A future second reader of `source_stem` then never receives a silently
    shortened value.
- **No new duplicate-suffix counter.** Every `sid` that `_evidence_sid()` builds carries the
  `{i:02d}-` index prefix. The prefix keeps `sid` unique within a run, truncated slug or not. Two fallbacks build a bare slug with no such prefix. They can collide after truncation
  where they do not today:
  - `scenario_slug(scenario.name)`, when a direct `run_scenario` caller passes no `scenario_id`
    ([`bajutsu/common/orchestrator/loop/_functions.py:645`](../../bajutsu/common/orchestrator/loop/_functions.py))
  - `scenario_slug(r.scenario)`, for a result without a `sid` in the report matrix
    ([`bajutsu/common/report/manifest.py:127`](../../bajutsu/common/report/manifest.py))

  Neither sits on the path of the `run` CLI itself. `pipeline.py:796` always passes
  `scenario_id=sid`, and `pipeline.py:1253` builds `sid` through `_evidence_sid()` itself. This item
  leaves both call sites unchanged. Capping `scenario_slug()` still shortens what they produce.
- **The file naming of `record` is unchanged.** An authored scenario with no explicit save name falls
  back to the natural-language goal of the recording. `scenario_out_name()` does that
  ([`bajutsu/serve/helpers.py:454`](../../bajutsu/serve/helpers.py)). It has no length cap either.

  This item caps the evidence-directory slug that a long file name can produce. It does not cap the
  file name itself. A long `*.yaml` file name is legible on disk and blocks nothing on its own. The evidence
  directory it produces competes with a real filesystem limit on every run of that file.

## Motivation

[BE-0417](../BE-0417-scenario-result-folder-naming/BE-0417-scenario-result-folder-naming.md)
already closes the failure this item was first reported against. Before BE-0417 landed, every
CSV-expanded row of a [data-driven scenario](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md)
carried its own `key=value` parameter text in `name`. A long or many-columned row could then turn
the output of `scenario_slug(s.name)` into an oversized `sid`.

Both file loaders now stamp `source_stem` on every row after expansion:

- [`bajutsu/run/cli.py:201`](../../bajutsu/run/cli.py)
- [`bajutsu/common/scenario/load_expanded.py:91`](../../bajutsu/common/scenario/load_expanded.py)

Every row of one file now shares the short stem of that file. For a data-driven scenario,
`_evidence_sid()` no longer reaches `scenario_slug()` at all.

The same class of failure still reaches two paths that neither slug function caps.

**A long source file.** Auto-naming in `record` falls back to the natural-language goal of the
recording. That happens when an operator gives no `--out` and no explicit name (see *Not doing*).

A verbose goal produces a `*.yaml` file exactly that long. Take the goal "log in with a saved card,
confirm the checkout total matches the cart, and check the confirmation email arrives". Every later
run of the resulting file tries to create an evidence directory of the same length.

**A scenario built directly, outside the file loaders.** This path covers the two bare-fallback call
sites in *Not doing*. It also covers any caller of `run_scenario()` that constructs a `Scenario`
itself, rather than loading one from a file.
[`demos/showcase/record/generate_from_nl.py`](../../demos/showcase/record/generate_from_nl.py) is an
example. Each of these still falls back to an uncapped `scenario_slug(s.name)`.

Capping both functions closes both paths at their single definition. `_evidence_sid()` and the two
bare fallbacks already share that choke point.

Once this item lands, a scenario with a long or verbose source file still runs to completion.
It writes its evidence directory. So does a scenario with no source file and a long or verbose `name`.

## Detailed design

1. **A shared character-count truncation helper.** Add `_MAX_SLUG_CHARS = 60` and a private
   `_cap_chars(slug: str) -> str` beside `scenario_slug()` and `sanitize_source_stem()` in
   [`bajutsu/common/orchestrator/types/_functions.py`](../../bajutsu/common/orchestrator/types/_functions.py).
   `_cap_chars` slices `slug` to `_MAX_SLUG_CHARS` characters with ordinary Python string slicing.
   Python string indexing always falls on a codepoint boundary. The helper needs no
   encode/decode step and cannot split a character. One helper and one budget serve both functions.
   The existing `.strip("-").lower()` ordering in `scenario_slug()` is unaffected.
2. **`scenario_slug()` calls it last.** The existing regex first collapses each run of
   non-alphanumeric characters to `-`, strips the result, and lowercases it. Pass that string
   through `_cap_chars` next, then through `.rstrip("-")` to drop a hyphen that the cut may leave
   dangling. Fall back to `"scenario"` when nothing is left. The function already returns that same
   fallback for an all-symbol name.
3. **`sanitize_source_stem()` calls it last.** The existing `re.sub()` call replaces unsafe
   characters. It matches `r"[^\w.-]"` and replaces each match with `"_"`. Pass the result through
   `_cap_chars` next. No further fallback is needed, for two reasons. Slicing a non-empty string to a
   positive length always keeps its first character. And `re.sub` cannot turn a non-empty `stem` into
   an empty string.
4. **No call site changes.** The two branches of `_evidence_sid()` and the two bare-fallback call
   sites in *Not doing* all call `scenario_slug()` or `sanitize_source_stem()` directly. The cap
   reaches every one of them without touching a single call site.
5. **Docs.** [`docs/reporting.md`](../../docs/reporting.md) and
   [`docs/ja/reporting.md`](../../docs/ja/reporting.md) gain a short paragraph beside the existing
   `runId` / `stepId` line. The paragraph names the two possible sources of `sid` and the shared cap.
6. **Tests.**
   - A unit test for `scenario_slug()`: an overlong name comes back capped, with no trailing hyphen.
   - A unit test for `sanitize_source_stem()`: an ASCII stem over 60 characters comes back capped.
   - A unit test for `sanitize_source_stem()`: `決済フロー` repeated comes back capped at 60
     characters. Its UTF-8 encoding runs well past 60 bytes, because the cap counts characters, not
     bytes.
   - A unit test that reproduces the `record`-with-no-`--out` case. A long natural-language goal, run
     through `scenario_out_name()` and then `sanitize_source_stem()`, produces a `sid` within the
     cap.
   - A test that colliding truncated slugs still land in distinct directories, through the existing
     `{i:02d}-` index prefix.

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Refuse a scenario whose slug would exceed the cap, mirroring the "refuse, don't truncate" rule of [BE-0404](../BE-0404-collapse-project-layer/BE-0404-collapse-project-layer.md) for run-history labels (`MAX_LABEL_LENGTH`, `bajutsu/common/report/manifest.py:13`) | A run-history label is text that an operator typed and expects preserved exactly. Refusing it hands control back to the operator. The slug of `sid` is a derived filesystem id that nobody authors directly. An operator who records a long natural-language goal is not choosing a file name. They are describing a flow. Refusing to run over that description blocks the whole scenario, where a legible, truncated slug does not. |
| Hash the slug to a fixed-length digest instead of truncating it | A digest carries nothing of the original name or file stem. That defeats the reason both functions derive the directory name from the scenario in the first place: letting an operator recognize a result by eye. Truncation keeps the recognizable lead. |
| Truncate `Scenario.name` or `Scenario.source_stem` themselves, before the slug functions run | `name` also feeds the `scenario` field of `manifest.json`, `report.html`, and the row-suffix matching of `declared_name()`. `source_stem` has one reader today, but truncating the field itself would silently hand a shortened value to any future second reader. `sid` is the only consumer that is purely a filesystem identifier, so it is the only thing this item caps. |
| Make the cap configurable per target | The cap protects a filesystem write, not app behavior. The app-agnostic boundary (prime directive 3) puts per-app differences in config. It does not put there a filesystem constant that holds the same regardless of the target under test. |
| Keep the row-distinguishing suffix instead of the lead of the scenario name: truncate from the front, or elide the middle (a head fragment plus a tail fragment) | This option weighs a suffix that no longer reaches `scenario_slug()` for the common data-driven case (*Motivation*). Every such row now shares one source-file stem, told apart by the `{i:02d}-` index alone, cap or no cap. For the two paths this item does address, a long file name and a long in-memory `name`, the lead is what an operator recognizes on sight. A head-and-tail elision would preserve slightly more of either. It would cost a second slice, a wider test matrix, and a slug that no longer reads as one continuous name at a glance. |
| Cap `scenario_out_name()` (the file naming of `record`) instead of, or in addition to, the two slug functions | It fixes the case that a `record` session with no `--out` produces, and nothing more. A hand-authored file with an equally long name reaches the same failure through `sanitize_source_stem()`, untouched. Capping at the two slug functions protects every path that reaches `sid`, from one definition: recorded, hand-authored, or built in memory. It does so regardless of how the source file (if any) got its name. |
| Cap `sanitize_source_stem()` by UTF-8 byte count instead of character count | The first shipped revision did this. `scenario_slug()` reduces to ASCII first, where a byte and a character are the same thing. `sanitize_source_stem()` preserves Unicode letters instead, where the two counts differ. This codebase treats fullwidth and Japanese scenario names as ordinary. A byte-oriented cap cuts such a name to about a third of the characters of an ASCII name that length. The cut lands at an arbitrary point in a name that the author chose to describe a flow. Counting characters treats every scenario name the same, regardless of script. The cost is a wider `sid` worst case: 60 of the longest UTF-8 codepoints run to 240 bytes, not 60. That is still far short of typical filesystem name-length limits. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `_MAX_SLUG_CHARS = 60` and a private `_cap_chars()` helper. Both sit beside the two
  slug functions in `bajutsu/common/orchestrator/types/_functions.py`. The helper slices `slug` to
  `_MAX_SLUG_CHARS` characters with ordinary Python string slicing. That is always at a codepoint
  boundary, so it needs no encode/decode step. It cannot split a character.
- [x] Unit 2 — `scenario_slug()` passes its lowercased result through `_cap_chars()` last. A
  `.rstrip("-")` follows, dropping a separator the cut can leave dangling. The existing
  `"scenario"` fallback still covers an all-symbol name.
- [x] Unit 3 — `sanitize_source_stem()` passes its `re.sub()` result through `_cap_chars()` last.
  The change needs no further fallback. Slicing a non-empty string to a positive length always
  keeps its first character.
- [x] Unit 4 — No call site changed, as designed. Both `_evidence_sid()` branches call one of the
  two capped functions directly. The two bare fallbacks do the same, at `loop/_functions.py:645`
  and `report/manifest.py:127`. The cap reaches every one of them from its single definition.
- [x] Unit 5 — `docs/reporting.md` and `docs/ja/reporting.md` each gained a paragraph. It sits
  beside the existing `runId` / `sid` / `stepId` line. The paragraph names the shared cap and why
  it counts characters rather than bytes. It also records that `Scenario.name` itself stays
  untruncated.
- [x] Unit 6 — Seven unit tests in `tests/runner/test_pipeline.py`, beside the BE-0417 slug
  tests. The count is two more than the design's five. The dangling-separator case became its
  own test, not a second assertion on the overlong-name one. A regression then names the broken
  property. A self-review pass found unit 3's one unpinned claim, and the seventh test covers it.
  The seven cases:
  - an ASCII slug capped with no trailing hyphen;
  - a cut landing on a separator;
  - a capped ASCII stem;
  - a multi-byte stem capped by character count, well past the cap in UTF-8 bytes;
  - a stem never coming back empty for a non-empty input, unit 3's missing-fallback invariant;
  - the `record`-with-no-`--out` path through `scenario_out_name()`;
  - colliding truncated slugs still drawing distinct `sid`s from the `{i:02d}-` prefix.

Log:

- [#1995](https://github.com/bajutsu-e2e/bajutsu/pull/1995) — All 6 units, completing the item.
  Added `_MAX_SLUG_BYTES = 60` and a private `_cap_bytes()` beside the two slug functions.
  `scenario_slug()` and `sanitize_source_stem()` each call it last, so a long source-file name or a
  long in-memory `name` no longer produces an `sid` the filesystem refuses. No call site changed.
  A self-review pass corrected two claims this change first got wrong. The `scenario_slug`
  docstring and both `reporting.md` pages had claimed every `sid` carries a run-order prefix. The
  item's own *Not doing* denies that for the two bare-fallback callers. The rationale for omitting
  `sanitize_source_stem`'s empty-result fallback had rested on the smallest UTF-8 character being
  one byte, where the load-bearing fact is that the longest is four. A seventh test now pins that
  invariant, which nothing had covered.
- [#1995](https://github.com/bajutsu-e2e/bajutsu/pull/1995) — Switched the cap to a character
  count. The item's author asked for that, in place of the UTF-8 byte count it shipped with.
  `_MAX_SLUG_BYTES` / `_cap_bytes()` became `_MAX_SLUG_CHARS` / `_cap_chars()`. The new helper
  slices with ordinary Python string
  indexing instead of encoding and decoding UTF-8. That drops the encode/decode step. It also
  drops the reasoning that step needed about the smallest and longest UTF-8 character. A character
  slice cannot split a character. The multi-byte test now pins character-count truncation instead
  of valid-UTF-8 output. The empty-result test lost its byte-length argument, since the invariant
  is now immediate. This revision also updated the docs and the *Detailed design* and
  *Alternatives considered* sections above. See the new *Alternatives considered* row for the
  trade-off this accepts.

## References

- [BE-0417 — Name a scenario's evidence directory after its source file](../BE-0417-scenario-result-folder-naming/BE-0417-scenario-result-folder-naming.md) (Implemented, [#1977](https://github.com/bajutsu-e2e/bajutsu/pull/1977))
- [BE-0031 — Data-driven scenarios](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md)
- [BE-0404 — Collapse the project layer](../BE-0404-collapse-project-layer/BE-0404-collapse-project-layer.md)
- [`bajutsu/common/orchestrator/types/_functions.py`](../../bajutsu/common/orchestrator/types/_functions.py)
- [`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py)
- [`bajutsu/common/report/manifest.py`](../../bajutsu/common/report/manifest.py)
- [`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py)
- [`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py)
- [`docs/reporting.md`](../../docs/reporting.md)
