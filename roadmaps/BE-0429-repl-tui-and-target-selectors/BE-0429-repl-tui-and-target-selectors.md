**English** · [日本語](BE-0429-repl-tui-and-target-selectors-ja.md)

# BE-0429 — An ncurses-style `repl` screen, and `label`/`index`/coordinate targets for `tap`/`type`

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0429](BE-0429-repl-tui-and-target-selectors.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0429") |
| Implementing PR | [#2026](https://github.com/bajutsu-e2e/bajutsu/pull/2026) |
| Topic | Authoring experience |
| Related | [BE-0423](../BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0423](../BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate.md) shipped
`bajutsu repl` as a plain, line-at-a-time `bajutsu>` prompt. Its `tap`/`type` addressed an element by
`id` alone. That was a deliberate v1 scope cut, which BE-0423's own *Detailed design* and
*Alternatives considered* named as a natural follow-up once the shell had landed. This item is that
follow-up, on three fronts.

- **Richer targets.** `tap`/`type` reach the rest of the `Selector` vocabulary the shell already
  resolves against under the hood. A terse shortcut grammar covers `label` and `index`. A
  `--sel <yaml>` escape hatch reuses the scenario `Selector` model directly for the fields the
  shortcuts cannot reach (`idMatches`, `labelMatches`, `traits`, `value`, `within`).
- **Raw coordinates.** A coordinate `tap` bypasses selector resolution entirely.
- **A screen.** When both ends of the terminal support it, the shell renders as an ncurses-style
  screen. The command line is pinned at the top, and every answer accumulates in a scrollable,
  greppable pane below it.

## Motivation

Using the v1 shell surfaced two gaps.

An element with no `id`, only a `label`, was unreachable from `repl`,
even though `tree` already prints its `label` in full. The operator could read the row but had no
command that would act on it. Two elements sharing one `id` or `label` (a duplicated row, a repeated
list item) were equally unreachable. `tap` would report `AmbiguousSelector` and stop, with no way to
say which one was meant.

Both fields, `label` and `index`, already exist end to end in `Selector`
(`bajutsu/common/drivers/base/selector.py`). `resolve_unique`
(`bajutsu/common/drivers/base/_functions.py`) already honors them. Only the REPL's own command-line
grammar for naming a target was missing.

A raw coordinate tap closes a third, narrower gap. It lets an operator confirm precisely where a
covering element's edge sits, or reach a control the tree misreports, without writing a scenario
step. `Driver.tap_point` already exists on every backend for this purpose. `repl` had no command
that called it.

A shortcut grammar covering `id`, `label`, and `index` still leaves a real gap. `idMatches`,
`labelMatches`, `traits`, `value`, and `within` have no shortcut spelling at all. Inventing one for
each (a glob flag, a regex flag, a trait list, a nested-selector syntax) would grow the command-line
grammar well past what a shortcut should carry. A scenario author already has a vocabulary for
precisely this: the `Selector` YAML a step authors against. Reusing it as a REPL escape hatch closes
the gap with no new vocabulary to design or learn.

Separately, the plain prompt loop forces an operator to scroll the terminal's own
history to read back a long `tree` or to find an earlier `tap`'s result. The shell has no memory of
what it printed and no way to search it. An operator debugging a flaky selector often wants precisely
that: type a few commands, then scroll back and grep the transcript for the one line that explains
what changed.

A classic ncurses-style split answers that need and is well understood. Input is pinned at the top,
output scrolls below, and one key moves between them. Python's own standard library already ships
`curses`, so the split adds no dependency. BE-0423 targets macOS and Linux only, matching this
project's Simulator-only platform reach.

## Detailed design

### Target syntax for `tap`/`type`

`bajutsu/repl/session.py` gains `_parse_target(token) -> Selector | Point | None`. It reuses the
existing `Selector` fields and `Driver.tap_point` rather than inventing new resolution semantics.
Only the token grammar is new.

| Form | Resolves to |
|---|---|
| `<id>` | `{"id": token}` — unchanged default; `tap`'s whole remainder is still the id verbatim, including an embedded space |
| `<id>#<index>` | `{"id": id, "index": index}` — `index` is 0-based, negative-from-end, precisely `resolve_unique`'s existing semantics |
| `label:<text>` | `{"label": text}` — the sole way to reach a label-only element; confirmed there is no id→label fallback anywhere in `matches()` |
| `label:<text>#<index>` | `{"label": text, "index": index}` |
| `@<x>,<y>` | a raw `Point`, `tap`-only — `driver.tap_point((x, y))` directly, bypassing `resolve_unique` entirely |

A trailing `#<index>` is stripped before the `label:` prefix check, so it composes with both forms.
An id or label that itself ends in a literal `#<digits>`, or starts with `@`/`label:`, cannot be
reached this way. This gap is narrow and documented (`help`), and the shell never misreads such a
target silently. `tree`/`find` remain the way to check.

`_tap(rest)` parses `rest` as a whole. The framing is unchanged: `tap` takes one argument, the
target, and nothing follows it, so a label with an embedded space needs no quoting.

`_type(rest)` must split the target from the text after it, so it gains a small quote-aware
splitter. The splitter is not `shlex`, because free-typed text must never require quoting or choke on
a stray quote character. The rule has two cases:

- A line starting with `"` reads up to the matching `"` as the target. This lets `type "label:Sign
  in" hello` name a spaced label.
- Otherwise the first whitespace-delimited token is the target, precisely today's behavior.

`type` rejects an `@<x>,<y>` target outright. There is a "tap at a point" concept, but no "type at a
point" one.

### Full-selector escape hatch (`--sel`)

`tap --sel <yaml>` and `type --sel <yaml> <text>` accept one flow-style `{...}` YAML mapping. Block
style needs newlines that a single typed line cannot hold, so flow style is the only shape this input
can take. Its closing `}` also marks `type`'s target/text boundary, the role a quote plays for
`"<target with a space>"`.

The mapping is parsed with `yaml.safe_load`. It is then validated through the *same*
`bajutsu.common.scenario.Selector` pydantic model that a scenario step authors against
(`Selector.model_validate(data).as_selector()`), not a hand-rolled schema. Every field a scenario can
use is thus available here: `id`, `idMatches`, `label`, `labelMatches`, `traits`, `value`,
`within`, and `index`, under both the snake_case field names and their camelCase aliases. The two
surfaces share one vocabulary with no drift, including `within`'s nested-selector shape, which no
shortcut spelling could express.

A YAML syntax error or a `Selector` validation failure reports the underlying error as the
answer. Examples of such a failure are an unknown field, an empty selector, and a malformed `id`
OR-candidate list. The error is a `yaml.YAMLError` or a `pydantic.ValidationError`. It replaces a
generic usage line because those messages are already specific enough to act on.

The shortcut forms above stay the default rather than being replaced by `--sel`. `tap <id>` is
shorter to type than `--sel {id: ...}` for the common single-id case (see *Alternatives considered*).

### An ncurses-style screen

A new `bajutsu/repl/tui.py` sits alongside the existing plain loop (`loop.py`) rather than replacing
it. `bajutsu/repl/cli.py` picks between them by `sys.stdin.isatty() and sys.stdout.isatty()`. This
choice has two effects:

- A piped `repl` (a script, a test harness) keeps the plain line-at-a-time shell, matching how most
  terminal tools degrade.
- The existing CLI-wiring tests drive `repl` through `CliRunner`, which never provides a real tty.
  They keep exercising the plain loop unmodified.

`tui.py` follows the dependency-injection shape that `loop.py` already uses for testability. `loop.py`
injects `read_line` and `say`. `tui.py` injects a small `Screen` Protocol with `getmaxyx`, `get_wch`,
`erase`, `addnstr`, `move`, and `refresh`. These are the exact subset of curses' own window object that
a real `curses.wrapper` `stdscr` already implements, so production needs no adapter class.

All behavior lives in two pure pieces:

- A `TuiState` dataclass holds the mode, the input buffer and cursor, the history, the output
  transcript, the scroll offset, and the filter query.
- A `handle_key(state, key, pane_height)` function applies one key to that state.

`run_tui` draws, reads one key, and runs a submitted line through `session.dispatch` precisely the way
`repl_loop` does, with the same `COMMAND_ERRORS`/`FATAL_ERRORS`/`ReplExit` handling. Tests drive
`handle_key` directly. They drive `run_tui` against a `FakeScreen` double that scripts `get_wch()` and
records draws. No real terminal is needed, mirroring how `test_repl.py` already tests the plain loop.

The keys follow the one-shortcut-key request this item answers.

- **`"input"` mode** (the default):
  - Printable characters insert at the cursor. `get_wch` decodes one full Unicode character per call,
    so a Japanese `type`/`tap label:` argument round-trips correctly. A raw `getch` would instead hand
    back one UTF-8 byte at a time.
  - Backspace, Left, Right, Home, and End edit the line, and Enter submits.
  - Up and Down recall history, classic-shell style.
  - **Tab** switches to `"scroll"`.
- **`"scroll"` mode**:
  - Up and Down scroll the output pane one line. PageUp and PageDown scroll a full pane. Scrolling is
    clamped to the current, possibly filtered, content.
  - New output arriving while the pane is pinned to the bottom (`scroll_offset == 0`) stays pinned.
    While the pane is scrolled up, it keeps the same content in view instead of yanking the operator
    back down mid-read.
  - `/` opens a filter prompt that reuses the same line editing.
  - **Tab** switches back to `"input"`.
- **`"filter"` mode** (entered via `/`):
  - Enter sets a case-insensitive substring filter over the whole transcript. An empty pattern clears
    it.
  - Esc abandons the edit without changing whatever filter was active.

Ctrl-C abandons the half-typed line (or filter query), the same intent as the plain loop's. Unlike
`input()`, curses gives no Ctrl-D/EOF signal in cbreak mode. Typing and submitting `exit`/`quit` is
thus the TUI's only way out. This is the one documented behavioral difference from the plain
fallback.

### Coverage

`bajutsu/repl/session.py` and `bajutsu/repl/tui.py` both clear their per-file coverage floor
(`coverage-floors.json`) from this item's own PR. BE-0423 set the same standard for the package.

## Alternatives considered

- **A third-party TUI library** (`prompt_toolkit`, `textual`). Rejected. `curses` is already in the
  standard library on every platform this project targets (macOS and Linux; there is no Windows CI),
  so it adds no dependency. This item needs a fixed input line, a scrollable pane, and a handful of
  keybindings, which do not call for a framework's widget system.
- **Always render the TUI, with no plain-loop fallback.** Rejected. A piped `repl` (a script, a test
  harness) has no real terminal for curses to draw into. Keeping `repl_loop` as the non-tty fallback
  costs nothing extra, because it already existed. It also keeps every existing CLI-wiring test, which
  drives `repl` through `CliRunner` and never a real tty, exercising real, unmodified code instead of
  needing a curses stand-in.
- **Normalized (0..1) coordinates for `tap @<x>,<y>`, matching the YAML `tapPoint` action.**
  Rejected. The REPL's own `tree`/`find` already report frames in raw device points, so a raw-pixel
  `tap_point` call is the more REPL-native fit. The YAML action normalizes for resolution-portability
  across runs. That concern does not apply to one interactive session against one already-booted
  device.
- **`Esc` as a second shortcut, toggling `"scroll"`/`"input"` like `Tab` does.** Rejected for this
  item. A short curses-internal timeout is the sole reliable way to tell a lone `Esc` keypress from
  the start of an escape sequence (an arrow key, a function key). That timeout would add latency to
  the one shortcut key this item's request asked for, and `Tab` already is a single key. `Esc` is used
  narrowly instead, only to abandon an in-progress filter edit, where a small delay is unnoticeable.
- **Replace the shortcut target grammar with YAML entirely, rather than adding `--sel` beside it.**
  Rejected. A bare `tap <id>` is the overwhelmingly common case. `tap {id: stable.save}` is strictly
  more to type for it, with no benefit. The shortcuts exist because they are faster than the full
  selector syntax for what an operator reaches for first. An explicit `--sel` flag, rather than
  sniffing a leading `{`, keeps the two grammars unambiguous to both the parser and the reader. It
  also matches this shell's existing flag style (`tree --json`).

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `_parse_target`/`_split_target_and_text` in `bajutsu/repl/session.py`: `<id>`, `<id>#<index>`,
  `label:<text>[#<index>]`, and `@<x>,<y>` (tap-only) target forms for `tap`/`type`, with `_HELP`
  updated to document them.
- [x] `--sel <yaml>` in `bajutsu/repl/session.py`: `_strip_sel_flag`/`_split_yaml_selector`/
  `_parse_yaml_selector`, validating through `bajutsu.common.scenario.Selector` for the full
  `id`/`idMatches`/`label`/`labelMatches`/`traits`/`value`/`within`/`index` vocabulary.
- [x] `bajutsu/repl/tui.py`: `Screen` Protocol, `TuiState`, `handle_key`, `run_tui`, and the real
  `run` entry point (`curses.wrapper` + `locale.setlocale`); `bajutsu/repl/cli.py` routes to it
  when both stdin and stdout are a tty, the plain `repl_loop` otherwise.
- [x] `FakeDriver`-backed tests for the new target forms, including `--sel` (`tests/test_repl.py`),
  and a `FakeScreen`-backed test suite for the TUI engine (`tests/test_repl_tui.py`), both at 100%
  coverage.
- [x] `docs/cli.md` and `docs/ja/cli.md` `## repl` sections updated: the new target-syntax table
  (including `--sel`) and the TUI keybindings table, replacing the closed "addressed by `id` alone"
  v1 limitation.

Log:

- [#2026](https://github.com/bajutsu-e2e/bajutsu/pull/2026) — Units 1-5, the whole item.

## References

- [BE-0423 — Interactive REPL for element-tree inspection and id-based actuation](../BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate.md) —
  the item this one extends. Its *Detailed design* and *Alternatives considered* describe the
  original id-only scope cut that this item closes.
- [`Selector`](../../docs/glossary.md#scenario-authoring) — `bajutsu/common/drivers/base/selector.py`,
  `bajutsu/common/drivers/base/_functions.py` (`resolve_unique`, `matches`)
- The scenario `Selector` pydantic model — `bajutsu/common/scenario/models/selector.py`, the
  validator `--sel <yaml>` reuses directly
- `Driver.tap_point` — `bajutsu/common/drivers/base/driver.py`
- [BE-0332 — read-lag barrier](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md) — unaffected
  by this item; still governs every `tree` read the same way it did under BE-0423
