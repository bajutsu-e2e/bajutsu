**English** · [日本語](BE-XXXX-repl-tui-and-target-selectors-ja.md)

# BE-XXXX — An ncurses-style `repl` screen, and `label`/`index`/coordinate targets for `tap`/`type`

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-repl-tui-and-target-selectors.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#2026](https://github.com/bajutsu-e2e/bajutsu/pull/2026) |
| Topic | Authoring experience |
| Related | [BE-0423](../BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0423](../BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate.md) shipped
`bajutsu repl` as a plain, line-at-a-time `bajutsu>` prompt whose `tap`/`type` addressed an element
by `id` alone — a deliberate v1 scope cut, named in that item's own *Detailed design* and
*Alternatives considered* as a natural follow-up once the shell itself had landed. This item is
that follow-up, on three fronts: `tap`/`type` reach the rest of the `Selector` vocabulary the shell
already resolves against under the hood, through both a terse shortcut grammar (`label`, `index`)
and a `--sel <yaml>` escape hatch reusing the scenario `Selector` model directly for the fields the
shortcuts cannot reach (`idMatches`, `labelMatches`, `traits`, `value`, `within`); a raw coordinate
`tap` bypasses selector resolution entirely; and the shell itself renders as an ncurses-style
screen — the command line pinned at the top, every answer accumulating in a scrollable, greppable
pane below it — when both ends of the terminal support it.

## Motivation

Two gaps surfaced from actually using the v1 shell:

An element with no `id` — only a `label` — was simply unreachable from `repl`, even though `tree`
already prints its `label` in full. The operator could read the row and had no command that would
act on it. Two elements sharing one `id` or `label` (a duplicated row, a repeated list item) were
equally unreachable: `tap` would report `AmbiguousSelector` and stop, with no way to say which one.
Both fields — `label` and `index` — already exist end to end in `Selector`
(`bajutsu/common/drivers/base/selector.py`) and are already honored by `resolve_unique`
(`bajutsu/common/drivers/base/_functions.py`); only the REPL's own command-line grammar for naming
a target was missing. A raw coordinate tap closes a third, narrower gap: confirming exactly where a
covering element's edge sits, or reaching a control the tree misreports, without writing a scenario
step to do it. `Driver.tap_point` already exists on every backend for this; `repl` had no command
that called it.

A shortcut grammar covering `id`, `label`, and `index` still leaves a real gap: `idMatches`,
`labelMatches`, `traits`, `value`, and `within` have no shortcut spelling at all, and inventing one
for each — a glob flag, a regex flag, a trait list, a nested-selector syntax — would grow the
command-line grammar well past what a shortcut should carry. A scenario author already has a
vocabulary for exactly this: the `Selector` YAML a step authors against. Reusing it as a REPL escape
hatch closes the gap with no new vocabulary to design or learn.

Separately, the plain prompt loop makes reading back a long `tree` or hunting for one earlier
`tap`'s result mean scrolling the terminal's own history — the shell has no memory of what it
printed, and no way to search it. An operator debugging a flaky selector often wants exactly that:
type a few commands, then scroll back and grep the transcript for the one line that explains what
changed. A classic ncurses-style split — input pinned at the top, output scrollable below, one key
to move between them — is a well-understood answer to that need, and Python's own standard library
already ships `curses` (BE-0423 targets macOS/Linux only, matching this project's existing
Simulator-only platform reach — no new dependency).

## Detailed design

### Target syntax for `tap`/`type`

`bajutsu/repl/session.py` gains `_parse_target(token) -> Selector | Point | None`, reusing the
existing `Selector` fields and `Driver.tap_point` rather than inventing new resolution semantics —
only the token grammar itself is new:

| Form | Resolves to |
|---|---|
| `<id>` | `{"id": token}` — unchanged default; `tap`'s whole remainder is still the id verbatim, including an embedded space |
| `<id>#<index>` | `{"id": id, "index": index}` — `index` is 0-based, negative-from-end, exactly `resolve_unique`'s existing semantics |
| `label:<text>` | `{"label": text}` — the only way to reach a label-only element; confirmed there is no id→label fallback anywhere in `matches()` |
| `label:<text>#<index>` | `{"label": text, "index": index}` |
| `@<x>,<y>` | a raw `Point`, `tap`-only — `driver.tap_point((x, y))` directly, bypassing `resolve_unique` entirely |

A trailing `#<index>` is stripped before the `label:` prefix check, so it composes with both forms.
An id or label that itself ends in a literal `#<digits>`, or starts with `@`/`label:`, cannot be
reached this way — a narrow, documented gap (`help`), not a silent misread; `tree`/`find` remain
the way to check.

`_tap(rest)` parses `rest` as a whole (unchanged framing: `tap` takes one argument, the target, and
nothing follows it, so a label with an embedded space needs no quoting). `_type(rest)` needs to
split the target from the text that follows it, so it gains a small quote-aware splitter — not
`shlex`, since free-typed text must never require quoting or choke on a stray quote character: a
line starting with `"` reads up to the matching `"` as the target (letting `type "label:Sign in"
hello` name a spaced label), otherwise the first whitespace-delimited token is the target, exactly
today's behavior. `type` rejects an `@<x>,<y>` target outright — there is no "type at a point"
concept the way there is a "tap at a point" one.

### Full-selector escape hatch (`--sel`)

`tap --sel <yaml>` and `type --sel <yaml> <text>` accept one flow-style `{...}` YAML mapping —
block style needs newlines a single typed line cannot hold, so flow style is the only shape this
input can take, and its closing `}` doubles as `type`'s target/text boundary, the same role a quote
plays for `"<target with a space>"`. The mapping is parsed with `yaml.safe_load` and validated
through the *same* `bajutsu.common.scenario.Selector` pydantic model a scenario step authors
against (`Selector.model_validate(data).as_selector()`), not a hand-rolled schema — so every field
a scenario can use (`id`, `idMatches`, `label`, `labelMatches`, `traits`, `value`, `within`,
`index`, both the snake_case field names and their camelCase aliases) is available here with zero
vocabulary drift between the two surfaces, including `within`'s nested-selector shape, which no
shortcut spelling could express. A YAML syntax error or a `Selector` validation failure (an unknown
field, an empty selector, a malformed `id` OR-candidate list) reports the underlying error
(`yaml.YAMLError` or `pydantic.ValidationError`) as the command's answer rather than a generic usage
line, since those messages are already specific enough to act on. The shortcut forms above stay the
default rather than being replaced by `--sel`, because `tap <id>` is shorter to type than
`--sel {id: ...}` for the common single-id case — see *Alternatives considered*.

### An ncurses-style screen

New `bajutsu/repl/tui.py`, kept alongside the existing plain loop (`loop.py`) rather than replacing
it — `bajutsu/repl/cli.py` picks between them by `sys.stdin.isatty() and sys.stdout.isatty()`, so a
piped `repl` (a script, a test harness) keeps the plain line-at-a-time shell, matching how most
terminal tools degrade, and the existing CLI-wiring tests (driven through `CliRunner`, which never
provides a real tty) keep exercising the plain loop unmodified.

`tui.py` follows the same dependency-injection shape `loop.py` already uses for its own
testability (`read_line`/`say` there, a small `Screen` Protocol here — `getmaxyx`/`get_wch`/
`erase`/`addnstr`/`move`/`refresh`, the exact subset of curses' own window object a real
`curses.wrapper` `stdscr` already implements, so no adapter class is needed in production). A pure
`TuiState` dataclass (mode, input buffer and cursor, history, the output transcript, scroll offset,
filter query) and a pure `handle_key(state, key, pane_height)` hold all the behavior; `run_tui`
draws, reads one key, and runs a submitted line through `session.dispatch` exactly the way
`repl_loop` does (same `COMMAND_ERRORS`/`FATAL_ERRORS`/`ReplExit` handling). Tests drive `handle_key`
directly and `run_tui` against a `FakeScreen` double that scripts `get_wch()` and records draws — no
real terminal needed, mirroring how `test_repl.py` already tests the plain loop.

Keys, matching the one-shortcut-key request this item answers:

- **`"input"` mode** (the default): printable characters (`get_wch` decodes one full Unicode
  character per call, so a Japanese `type`/`tap label:` argument round-trips correctly — a raw
  `getch` would instead hand back one UTF-8 byte at a time) insert at the cursor;
  Backspace/Left/Right/Home/End edit the line; Enter submits; Up/Down recall history, classic-shell
  style; **Tab** switches to `"scroll"`.
- **`"scroll"` mode**: Up/Down scroll the output pane one line, PageUp/PageDown a full pane, clamped
  to the current (possibly filtered) content; new output arriving while pinned to the bottom
  (`scroll_offset == 0`) stays pinned, while scrolled up it keeps the same content in view instead
  of yanking the operator back down mid-read; `/` opens a filter prompt (reusing the same line
  editing); **Tab** switches back to `"input"`.
- **`"filter"` mode** (entered via `/`): Enter sets a case-insensitive substring filter over the
  whole transcript (an empty pattern clears it), Esc abandons the edit without changing whatever
  filter was active.

Ctrl-C abandons the half-typed line (or filter query), the same intent as the plain loop's. Unlike
`input()`, curses gives no Ctrl-D/EOF signal in cbreak mode, so `exit`/`quit`, typed and submitted,
is the TUI's only way out — documented as the one behavioral difference from the plain fallback.

### Coverage

`bajutsu/repl/session.py` and `bajutsu/repl/tui.py` both clear their per-file coverage floor
(`coverage-floors.json`) from this item's own PR, the same standard BE-0423 set for the package.

## Alternatives considered

- **A third-party TUI library** (`prompt_toolkit`, `textual`). Rejected: `curses` is already in the
  standard library on every platform this project targets (macOS/Linux; no Windows CI), so it adds
  no dependency, and this item's needs — a fixed input line, a scrollable pane, a handful of
  keybindings — do not need a framework's widget system.
- **Always render the TUI, with no plain-loop fallback.** Rejected: a piped `repl` (a script, a test
  harness) has no real terminal for curses to draw into. Keeping `repl_loop` as the non-tty fallback
  costs nothing extra (it already existed) and keeps every existing CLI-wiring test — which drives
  `repl` through `CliRunner`, never a real tty — exercising real, unmodified code instead of needing
  a curses stand-in.
- **Normalized (0..1) coordinates for `tap @<x>,<y>`, matching the YAML `tapPoint` action.**
  Rejected: the REPL's own `tree`/`find` already report frames in raw device points, so a raw-pixel
  `tap_point` call is the more REPL-native fit; the YAML action's normalization exists for
  resolution-portability across runs, a concern that does not apply to one interactive session
  against one already-booted device.
- **`Esc` as a second shortcut, toggling `"scroll"`/`"input"` like `Tab` does.** Rejected for this
  item: a lone `Esc` keypress is only reliably distinguished from the start of an escape sequence
  (an arrow key, a function key) by a short curses-internal timeout, which would add latency to the
  one shortcut key this item's request actually asked for (a single key, which `Tab` already is).
  `Esc` is used narrowly instead, only to abandon an in-progress filter edit, where a small delay is
  unnoticeable.
- **Replace the shortcut target grammar with YAML entirely, rather than adding `--sel` beside it.**
  Rejected: a bare `tap <id>` is the overwhelmingly common case, and `tap {id: stable.save}` is
  strictly more to type for it with no benefit — the shortcuts' whole reason to exist is that they
  are faster than the full selector syntax for what an operator reaches for first. An explicit
  `--sel` flag (over sniffing a leading `{`) keeps the two grammars unambiguous to both the parser
  and the reader, and matches this shell's existing flag style (`tree --json`).

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
  the item this one extends; see its *Detailed design* and *Alternatives considered* for the
  original id-only scope cut this item closes.
- [`Selector`](../../docs/glossary.md#scenario-authoring) — `bajutsu/common/drivers/base/selector.py`,
  `bajutsu/common/drivers/base/_functions.py` (`resolve_unique`, `matches`)
- The scenario `Selector` pydantic model — `bajutsu/common/scenario/models/selector.py`, the
  validator `--sel <yaml>` reuses directly
- `Driver.tap_point` — `bajutsu/common/drivers/base/driver.py`
- [BE-0332 — read-lag barrier](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md) — unaffected
  by this item; still governs every `tree` read the same way it did under BE-0423
