**English** · [日本語](BE-0423-cli-repl-inspect-actuate-ja.md)

# BE-0423 — Interactive REPL for element-tree inspection and id-based actuation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0423](BE-0423-cli-repl-inspect-actuate.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0423") |
| Implementing PR | [#2009](https://github.com/bajutsu-e2e/bajutsu/pull/2009) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

`bajutsu repl` opens a read-eval-print loop (REPL): a manual command shell against a running target. An operator launches the app once, then types one command at a time. `tree` reads the current screen's element tree. `tap <id>` acts on one of its elements. The operator reads each result before choosing the next command.

The shell sits beside `record` (goal-directed AI authoring) and `crawl` (autonomous exploration). It is a third way to reach a target through the same [`Driver`](../../docs/glossary.md#driver-backend-actuator-platform) interface that those two commands already use. Unlike `record` and `crawl`, `repl` asks no large language model (LLM) anything and writes no scenario.

`repl` is a thin loop over `query()` and the actuation methods that every [backend](../../docs/glossary.md#driver-backend-actuator-platform) already implements. XCUITest, adb, and Playwright therefore gain the shell for free, with no per-backend read or actuation code. Only the launch and exit paths carry branches beyond that shared surface (see *Detailed design*).

## Motivation

Finding out which [selector](../../docs/glossary.md#scenario-authoring) will resolve to which element costs more than the question deserves today. An operator can read the tree by eye in Xcode's Accessibility Inspector or in a browser's devtools. That reading is backend-specific. It also skips the normalized `id` / `label` / `traits` fields that a scenario step actually matches against.

The existing tools each fall short:

- `bajutsu doctor` already reads the live screen from the command line. It scores convention (id coverage, off-namespace ids) against a driver that it probes once and tears down. It never lists the tree for reading, and it cannot act on an id.
- `record` and `crawl` both drive the app through an AI call. Both produce output shaped for their own artifact, a scenario or a screen map. Neither answers one query right away.
- The `serve` web UI's Author view comes closer. Its `/api/capture/start` and `/api/capture/resolve` endpoints boot a live driver. They turn a click on its live screenshot into a selector with no AI call ([BE-0262](../../roadmaps/BE-0262-serve-author-live-step-picker/BE-0262-serve-author-live-step-picker.md)). That picker still runs inside a browser tab, built for choosing one step to add to a scenario. It never answers whether a typed id resolves at all.

None of these answers one question directly from a command line, on any backend. What does the current screen look like, and what happens when one of its ids is tapped?

`repl` answers that question directly. `tree` prints the current element tree. `tap <id>` acts on one of its elements. A second `tree` shows what changed. The loop needs no AI round trip, no scenario file, and no per-backend inspector to learn.

A selector that fails to match during authoring is a common outcome. The id carries a typo, another element covers it, or it appears after a wait. Diagnosing that today means writing a candidate scenario step and running it. The operator then reads the manifest's captured tree. `repl` shortens that loop to typing `tree` and `tap` against a running app.

Once this ships, an operator launches `bajutsu repl --target <name>` and runs `tree`. The operator reads the visible elements' ids, taps one of them, and watches the screen change. That loop takes seconds, in place of a run-and-read-the-report cycle.

## Detailed design

`bajutsu repl --target <name> [--udid <id>] [--backend <list>] [--erase/--no-erase] [--headed/--no-headed] [--browser <engine>] [--config <path>]` launches the app. The launch path reuses existing helpers:

- Target resolution and backend selection reuse the shared CLI helpers (`bajutsu/cli/_shared.py`) that `record` already calls. They are `_load_effective_with_source` and `_select_actuator_or_exit`.
- Device bring-up reuses `launch_driver` (`bajutsu/common/runner/launch.py`). `record` and `crawl` use the same combination. It resolves a `udid` (skipped for the `playwright` actuator) and boots the device before handing off a driver.
- Ahead of that launch, `repl` brings the target's own server up where the config declares `launchServer`. It uses the `_start_launch_server_or_exit` helper. `run`, `record`, `crawl`, and `audit` already share it (`bajutsu/cli/_shared.py`). On exit, `repl` stops the server through `atexit`, the way `record` and `crawl` do. Without that step, a web target can serve its `baseUrl` from `launchServer`. The browser then opens on a host that is not listening, and every `tree` reads the error page.
- `--headed`/`--no-headed` and `--browser` are web-only. Both reuse a shared helper from `bajutsu/cli/_shared.py`. `_with_headed` is called by `record`, `crawl`, and `run`. `_resolve_browser` is called by `record` and `run`, because `crawl` exposes no `--browser`. The two flags matter for this shell in particular. A headless browser leaves an operator with no screen to watch change.

On launch, `repl` prints the resolved backend and target, then a `bajutsu>` prompt.

The v1 command set stays small and id-first:

| Command | Behavior |
|---|---|
| `tree [--json]` | `driver.settled_query()` when the driver implements `SettledReadProvider` (adb), else `driver.query()` — rendered as a table of `id` / `label` / `traits` / `value` / `frame` (or as JSON) |
| `find <substring>` | the same tree, filtered to rows whose `id` or `label` contains `<substring>` |
| `tap <id>` | `driver.tap({"id": "<id>"})` |
| `type <id> <text>` | tap `<id>` to focus it, then `driver.type_text("<text>")` |
| `back` | `driver.back()` |
| `screenshot [path]` | `driver.screenshot(path)`, auto-named when `path` is omitted |
| `help` | list the commands above |
| `exit` / `quit` | leave the shell; a Simulator- or device-backed app (local `xcuitest`, `adb`) keeps running, while any session this process itself owns is closed through the same call its `Environment.teardown` makes — the web backend's browser (`cast(base.BackendLifecycle, driver).close()`, as `WebEnvironment.teardown` does; `Driver` itself declares no `close()`) and, on the `--udid https://…` live route, the WebDriver session `XcuitestLiveEnvironment.teardown` deletes, which would otherwise stay reserved on the grid until it expires |

Every command that reads or resolves against the tree asks for an actuation-grade read, the same way `run`'s own handlers do. A driver that implements `SettledReadProvider` answers through `settled_query()`. The protocol lives in `bajutsu/common/drivers/base/settled_read_provider.py`. A driver whose own reads are already good enough to actuate from answers through a plain `query()`.

The read-lag barrier ([BE-0332](../../roadmaps/BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md)) makes `settled_query()` safe on adb. It keeps a `tree` issued right after a `tap` from reading a stale, pre-actuation snapshot.

`resolve_unique`'s existing zero-or-many-matches contract fails a command right away. The failure carries the same `ElementNotFound` / `AmbiguousSelector` message that `run` would raise. Neither path guesses which element the operator meant (prime directive 2 — determinism first).

`tap` calls `driver.tap()` directly. It does not call `run`'s own `_tap_with_recovery` (`bajutsu/common/orchestrator/actions/handlers/gestures.py`). A target that another element covers therefore raises `ElementNotTappable` in `repl`. `run` would first retry a bounded scroll and succeed. This is a deliberate v1 gap, not a bug to route around silently (see *Alternatives considered*).

`tap` and `type` address an element by `id` alone. That is narrower than the full [selector](../../docs/glossary.md#scenario-authoring) syntax that `run` accepts (`id`, `idMatches`, `label`, `labelMatches`, `traits`, `value`, `within`, `index`). The narrowing is on purpose, for two reasons. It keeps this first version small enough to review. It also matches how the shell gets used. `tree` already shows every element's `label` and `traits`. An operator reads the row and types its id.

A full selector can address an element with no `id` by `label` or `traits`. `repl`'s id-only v1 cannot reach it. A control genuinely absent from the tree is a separate problem. The most common case is an individual tab in a no-id app's tab bar. `record`'s vision fallback exists to close that problem (`docs/recording.md`). `repl` does not attempt it, because adding vision would put an AI call back into a tool built to avoid one.

A natural follow-up extends `tap` and `type` to the rest of `Selector`'s fields (`idMatches`, `label`, `labelMatches`, `traits`, `value`, `within`, `index`). It is scoped separately, once the shell itself has shipped.

Gestures (`swipe`, `scroll`, `pinch`, `rotate`) and platform-specific actions (`set_picker_value`, `select_option`) stay out of v1. `tap`, `type_text`, `back`, and `screenshot` cover what an operator reaches for first. That covers confirming a selector and walking a flow by hand. The remaining `Driver` methods are plain additions once the command-parsing shape is settled. Adding every one of them at once would widen this item past a single reviewable change.

`repl` lives in its own top-level package, `bajutsu/repl/`, beside `record/` and `crawl/`. A row for it joins `docs/architecture.md`'s module table, so `make lint-module-map` keeps passing. The fast suite gets `FakeDriver`-backed tests for the following:

- command parsing
- `tree` / `find` rendering
- the `ElementNotFound` / `AmbiguousSelector` / `ElementNotTappable` surfaces

`run`'s own handlers are tested the same way. The new module therefore clears the per-file coverage floor (`coverage-floors.json`) from its first PR. It needs no follow-up.

## Alternatives considered

- **Read the tree with a platform-native tool** (Xcode's Accessibility Inspector, browser devtools). Rejected. Each tool is backend-specific. Its answer does not come through the one backend-agnostic [`Driver`](../../docs/glossary.md#driver-backend-actuator-platform) seam. The rest of Bajutsu reads the tree through that seam. None of the tools shows the normalized `id` / `label` / `traits` fields. A Bajutsu selector matches against those fields. An id read there is not guaranteed to be the id that `run` would resolve.
- **Use `serve`'s Author live step picker instead of a new command.** Rejected for this item. The endpoints `/api/capture/start` and `/api/capture/resolve` ([BE-0262](../../roadmaps/BE-0262-serve-author-live-step-picker/BE-0262-serve-author-live-step-picker.md)) already boot a live, AI-free driver. They turn a click on its screen into a selector, but through a browser picker. The picker is built for choosing one step to add to a scenario. It never answers whether a typed id resolves. It also leaves no path for acting on an id from a terminal, with no browser tab and no editor open. That is the narrower need this item exists for.
- **Route `repl`'s `tap` through `run`'s `_tap_with_recovery`.** Rejected for v1. Surfacing the driver's own `ElementNotTappable` is the more useful answer while diagnosing a selector. What the error names varies by backend:
  - adb, the live XCUITest route, and `FakeDriver` name the covering element through `base.raise_if_covered`.
  - The web backend names it through its own hit result.
  - The XCUITest Simulator driver reports just `element resolved but not hittable`.

  An operator who wants the recovery writes the explicit `scroll` step in the scenario and taps there. The cost is that `repl` reports a failure where `run` would recover. The two can disagree on a covered target, which is the gap *Detailed design* calls out.
- **Add a "manual mode" flag to `record` instead of a new command.** Rejected. `record`'s loop is built around `ClaudeAgent` proposing actions from a screenshot, and it always ends by writing a scenario. Bolting a human-typed command path onto that loop would tangle an AI-driven path with a non-AI one. `repl` never writes a scenario, on purpose. A separate command keeps both simpler to read.
- **Accept a full selector (`label`, `labelMatches`, `index`) for `tap`/`type` from the start.** Rejected for v1 in favor of id-only addressing (see *Detailed design*). The smaller surface already answers the question behind this item. It lands as one reviewable change. That change does not have to settle selector-syntax parsing on a command line too.
- **A non-interactive mode that reads commands from stdin or a file.** Rejected for this item. The interactive shell alone proves the command set before any scripting surface gets built on top of it. A batch mode is a follow-up, scoped separately, once that command set is settled.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `bajutsu repl` command scaffold under the new `bajutsu/repl/` package:
  `_load_effective_with_source` / `_select_actuator_or_exit` / `_start_launch_server_or_exit` /
  `launch_driver` reuse (with the launch server stopped on exit via `atexit`),
  `--headed`/`--no-headed`/`--browser`, the `bajutsu>` prompt loop, `help` / `exit` / `quit`
  (including `cast(base.BackendLifecycle, driver).close()` on the web backend's exit and
  `XcuitestLiveEnvironment.teardown`'s WebDriver-session close on the `--udid https://…` live
  route).
- [x] `tree` / `tree --json` / `find <substring>`, reusing `settled_query()` / `query()` and the
  read-lag-barrier path.
- [x] `tap <id>` / `type <id> <text>`, surfacing `ElementNotFound` / `AmbiguousSelector` /
  `ElementNotTappable` the same way `run` does.
- [x] `back` / `screenshot [path]`.
- [x] `FakeDriver`-backed tests in the fast suite: command parsing, `tree` / `find` rendering, and
  the `ElementNotFound` / `AmbiguousSelector` / `ElementNotTappable` surfaces.
- [x] `docs/cli.md` and `docs/ja/cli.md` reference sections, plus the CLI inventories `repl` makes
  stale — the CLI-verbs table in `docs/glossary.md` (and `docs/ja/glossary.md`), the command
  lists in `docs/architecture.md` ([BE-0113](../../roadmaps/BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)),
  and `bajutsu/repl/`'s own row in `docs/architecture.md`'s module table (`make lint-module-map`).

Log:

- [#2009](https://github.com/bajutsu-e2e/bajutsu/pull/2009) — Units 1-6, the whole item. Added the `bajutsu/repl/` package: `render.py` (the
  `id` / `label` / `traits` / `value` / `frame` table and the verbatim JSON), `session.py` (the command
  set — `tree` / `find` / `tap` / `type` / `back` / `screenshot` / `help` / `exit`), `loop.py` (the
  `bajutsu>` prompt loop), and `cli.py` (the Typer command, reusing `record`'s launch helpers and
  `crawl`'s one-environment launch-and-exit pattern so the exit closes only the browser or the live
  WebDriver session this process owns). `find` matches case-sensitively, like every selector match.
  Registered the command in `_FEATURE_MODULE_NAMES`, classified it Claude-free in `capabilities.py`,
  and added `bajutsu.repl` to the deterministic-core import contract. `tests/test_repl.py` covers the
  rendering, each verb's driver call, the `ElementNotFound` / `AmbiguousSelector` /
  `ElementNotTappable` surfaces, and the loop's three ways out (`exit`/`quit`, end of input, an interrupt). Docs updated in both languages:
  the `cli` reference section, the glossary CLI-verbs table, the AI-boundary split, the
  `getting-started` `--help` list, the `architecture` command lists and module table, the
  `developer-guide` package table and map, and both READMEs.

## References

- [`Driver`](../../docs/glossary.md#driver-backend-actuator-platform) protocol —
  `bajutsu/common/drivers/base/driver.py`
- [`Selector`](../../docs/glossary.md#scenario-authoring) — `bajutsu/common/scenario/models/selector.py`
- `SettledReadProvider` — `bajutsu/common/drivers/base/settled_read_provider.py`
- `BackendLifecycle` — `bajutsu/common/drivers/base/backend_lifecycle.py`
- `_start_launch_server_or_exit` — `bajutsu/cli/_shared.py`
- `XcuitestLiveEnvironment` — `bajutsu/common/platform_lifecycle/environments/xcuitest_live.py`
- `record` and `crawl` — the two existing Tier 1 authoring paths `repl` sits beside (`docs/cli.md`)
- [BE-0332 — read-lag barrier](../../roadmaps/BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md)
- [BE-0262 — live step-picking and target-scoped runs in the Author editor](../../roadmaps/BE-0262-serve-author-live-step-picker/BE-0262-serve-author-live-step-picker.md)
