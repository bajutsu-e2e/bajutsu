"""An ncurses-style front end for `repl`: input pinned at top, output scrollable/greppable below.

Used instead of the plain `repl_loop` (`loop.py`) only when both stdin and stdout are a real
terminal (`cli.py`) — a piped/non-interactive `repl` keeps the plain line-at-a-time shell, which is
what most terminal tools do and keeps this front end optional rather than a hard dependency of every
caller. Split the same way `loop.py` splits `read_line`/`say` from `ReplSession`: `TuiState` and
`handle_key` do no terminal I/O — they read curses' key constants but never touch a window — so they
run under a `FakeScreen` in tests with no real terminal; only `run` opens a real one.
"""

from __future__ import annotations

import contextlib
import curses
import locale
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from bajutsu.repl.loop import PROMPT
from bajutsu.repl.render import _display_width
from bajutsu.repl.session import COMMAND_ERRORS, FATAL_ERRORS, ReplExit, ReplSession

Mode = Literal["input", "scroll", "filter"]

_BANNERS: dict[Mode, str] = {
    "input": "-- INPUT  (Tab: switch to scroll pane) --",
    "scroll": (
        "-- SCROLL  (Tab: back to input · ↑↓/jk scroll · PgUp/PgDn: page"
        " · ←→: top/bottom · /: filter) --"
    ),
    "filter": "-- FILTER  (Enter: apply · Esc: cancel) --",
}

# `curses.getmouse()`'s own return shape: (id, x, y, z, bstate) — only `bstate` matters here.
GetMouseEvent = Callable[[], tuple[int, int, int, int, int]]

# Appended after every command's output — a scrollback full of answers otherwise runs together
# with no visual seam between one command's last line and the next command's prompt.
_EOL_MARKER = "-- EOL --"


class Screen(Protocol):
    """The slice of a curses window this module draws through — real or fake, for testing.

    A real `curses.wrapper` `stdscr` already implements every one of these methods with this exact
    signature, so `run` passes it straight through with no adapter.
    """

    def getmaxyx(self) -> tuple[int, int]: ...  # (height, width)
    def get_wch(self) -> int | str: ...  # a decoded keycode (int) or one Unicode character (str)
    def erase(self) -> None: ...
    def addnstr(self, y: int, x: int, text: str, n: int) -> None: ...
    def move(self, y: int, x: int) -> None: ...
    def refresh(self) -> None: ...


@dataclass
class TuiState:
    """Everything the TUI needs to decide what a keystroke does and what to draw. No curses here."""

    mode: Mode = "input"
    input_buffer: str = ""
    cursor: int = 0
    history: list[str] = field(default_factory=list)
    history_index: int | None = None  # None = not recalling; else the entry currently shown
    output: list[str] = field(default_factory=list)  # the full transcript, prompt lines included
    scroll_offset: int = 0  # lines scrolled up from the bottom of the current (filtered) view
    filter_query: str | None = None
    stashed_input: tuple[str, int] = ("", 0)  # a command parked while the filter prompt borrows
    # `input_buffer`/`cursor` — restored, not discarded, when the filter prompt closes


def handle_key(state: TuiState, key: int | str, pane_height: int) -> str | None:
    """Apply one keystroke to *state*, mutating it in place.

    Returns the line to run through `session.dispatch`, only when Enter was pressed in `"input"`
    mode — the one case with a side effect this pure function cannot itself perform. Every other
    keystroke (editing, history recall, a mode switch, a filter query) is fully handled here.
    """
    if state.mode == "scroll":
        _handle_scroll_key(state, key, pane_height)
        return None
    if state.mode == "filter":
        _handle_filter_key(state, key)
        return None
    return _handle_input_key(state, key)


def _handle_input_key(state: TuiState, key: int | str) -> str | None:
    if _is_enter(key):
        line = state.input_buffer
        state.input_buffer = ""
        state.cursor = 0
        state.history_index = None
        return line
    if key == "\t":
        state.mode = "scroll"
        return None
    if key == curses.KEY_UP:
        _recall_history(state, -1)
        return None
    if key == curses.KEY_DOWN:
        _recall_history(state, 1)
        return None
    _edit_buffer(state, key)
    return None


def _handle_scroll_key(state: TuiState, key: int | str, pane_height: int) -> None:
    # Clamped here, not only inside `_visible`: an offset the view cannot honor would still
    # accumulate unboundedly (every no-op ↑ press while already at the top), so later output
    # would jump the pane up by however far past the end it had run, instead of staying pinned.
    max_offset = max(0, len(_filtered(state)) - pane_height)
    if key == "\t":
        state.mode = "input"
    elif key in (curses.KEY_UP, "k"):  # "k"/"j": vim-style scroll, alongside the arrow keys
        state.scroll_offset = min(max_offset, state.scroll_offset + 1)
    elif key in (curses.KEY_DOWN, "j"):
        state.scroll_offset = max(0, state.scroll_offset - 1)
    elif key == curses.KEY_PPAGE:
        state.scroll_offset = min(max_offset, state.scroll_offset + max(1, pane_height))
    elif key == curses.KEY_NPAGE:
        state.scroll_offset = max(0, state.scroll_offset - max(1, pane_height))
    elif key == curses.KEY_LEFT:
        state.scroll_offset = max_offset
    elif key == curses.KEY_RIGHT:
        state.scroll_offset = 0
    elif key == "/":
        state.stashed_input = (state.input_buffer, state.cursor)
        state.mode = "filter"
        state.input_buffer = ""
        state.cursor = 0


def _handle_wheel_scroll(state: TuiState, bstate: int, pane_height: int) -> None:
    """Scroll the output pane on a mouse-wheel tick, whatever the current mode is.

    A terminal's wheel arrives as `curses.KEY_MOUSE`/`getmouse()`, not a key constant `handle_key`
    already dispatches on, and a wheel tick should move the view no matter what the operator is
    doing — typing a command, filtering, already in the scroll pane — so this runs before
    `handle_key`'s mode dispatch rather than folding into `_handle_scroll_key`. `BUTTON5_PRESSED`
    (wheel down) is missing from some platforms' ncurses builds; `getattr` with a `0` default keeps
    the check a no-op there instead of raising, at the cost of wheel-down doing nothing on those.
    """
    max_offset = max(0, len(_filtered(state)) - pane_height)
    if bstate & curses.BUTTON4_PRESSED:
        state.scroll_offset = min(max_offset, state.scroll_offset + 1)
    elif bstate & getattr(curses, "BUTTON5_PRESSED", 0):  # pragma: no cover - no BUTTON5 here
        state.scroll_offset = max(0, state.scroll_offset - 1)


def _handle_filter_key(state: TuiState, key: int | str) -> None:
    if _is_enter(key):
        state.filter_query = state.input_buffer.strip() or None
        state.input_buffer, state.cursor = state.stashed_input
        state.scroll_offset = 0
        state.mode = "scroll"
        return
    if key == "\x1b":  # Esc: abandon the edit; whatever filter was active stays active
        state.input_buffer, state.cursor = state.stashed_input
        state.mode = "scroll"
        return
    _edit_buffer(state, key)


def _edit_buffer(state: TuiState, key: int | str) -> None:
    """Line-editing shared by `"input"` and `"filter"` — both just edit `input_buffer`/`cursor`."""
    if _is_backspace(key):
        if state.cursor > 0:
            state.input_buffer = (
                state.input_buffer[: state.cursor - 1] + state.input_buffer[state.cursor :]
            )
            state.cursor -= 1
    elif key == curses.KEY_LEFT:
        state.cursor = max(0, state.cursor - 1)
    elif key == curses.KEY_RIGHT:
        state.cursor = min(len(state.input_buffer), state.cursor + 1)
    elif key == curses.KEY_HOME:
        state.cursor = 0
    elif key == curses.KEY_END:
        state.cursor = len(state.input_buffer)
    elif isinstance(key, str) and len(key) == 1 and key.isprintable():
        # `get_wch` decodes a full multi-byte character in one call (BE-0423's original `input()`
        # loop got this for free from the line-buffered terminal; a raw `getch` would instead hand
        # back one raw byte of a Japanese character's UTF-8 encoding at a time).
        state.input_buffer = (
            state.input_buffer[: state.cursor] + key + state.input_buffer[state.cursor :]
        )
        state.cursor += 1


def _recall_history(state: TuiState, direction: int) -> None:
    if not state.history:
        return
    if state.history_index is None:
        state.history_index = len(state.history)
    new_index = state.history_index + direction
    if not 0 <= new_index <= len(state.history):
        return  # already at the oldest/newest end; nothing to move to
    state.history_index = new_index
    state.input_buffer = "" if new_index == len(state.history) else state.history[new_index]
    state.cursor = len(state.input_buffer)


def _is_enter(key: int | str) -> bool:
    return key in ("\n", "\r") or key == curses.KEY_ENTER


def _is_backspace(key: int | str) -> bool:
    return key in ("\x7f", "\x08") or key == curses.KEY_BACKSPACE


def _filtered(state: TuiState) -> list[str]:
    if state.filter_query is None:
        return state.output
    needle = state.filter_query.lower()
    return [line for line in state.output if needle in line.lower()]


def _visible(state: TuiState, height: int) -> list[str]:
    """The output-pane slice to draw for a `height`-row pane, honoring scroll and filter."""
    lines = _filtered(state)
    max_offset = max(0, len(lines) - height)
    offset = min(state.scroll_offset, max_offset)
    start = max(0, len(lines) - height - offset)
    return lines[start : start + height]


def _note_new_output(state: TuiState, added: list[str]) -> None:
    """Keep a scrolled-up view showing the same content when new output arrives (no yank-to-bottom).

    Pinned (`scroll_offset == 0`) is left alone — the view already tracks the bottom on its own.
    Scrolled up, the offset grows by however many of the new lines would actually appear in the
    current (possibly filtered) view, which is what keeps `_visible`'s computed window unchanged.
    """
    if state.scroll_offset == 0:
        return
    if state.filter_query is None:
        state.scroll_offset += len(added)
    else:
        needle = state.filter_query.lower()
        state.scroll_offset += sum(1 for line in added if needle in line.lower())


def _run_line(session: ReplSession, line: str) -> tuple[list[str], bool]:
    """Run one submitted line the same way `repl_loop` does.

    Returns the lines to append to the transcript and whether the shell should now leave — the
    curses analog of `repl_loop`'s `say` calls and early `return`.
    """
    try:
        return list(session.dispatch(line)), False
    except ReplExit:
        return [], True
    except FATAL_ERRORS as e:
        return [
            f"{type(e).__name__}: {e}",
            "the XCUITest runner is gone; this shell cannot recover it — leaving",
        ], True
    except COMMAND_ERRORS as e:
        return [f"{type(e).__name__}: {e}"], False


def run_tui(
    session: ReplSession,
    screen: Screen,
    get_mouse_event: GetMouseEvent = curses.getmouse,
) -> None:
    """Drive the ncurses-style shell against *screen* until the operator leaves.

    Ctrl-C abandons the half-typed line (or a half-typed filter query), same intent as the plain
    loop's — but, unlike `input()`, curses gives no Ctrl-D/EOF signal in cbreak mode, so `exit` /
    `quit`, typed and submitted, is the only way out. `get_mouse_event` defaults to the real
    `curses.getmouse` and is overridden only in tests, which have no mouse queue to read.
    """
    state = TuiState()
    while True:
        _draw(screen, state)
        try:
            key = screen.get_wch()
        except KeyboardInterrupt:
            # A no-op while just browsing the scroll pane (nothing is being typed there to
            # abandon) — matches the docs table's "Ctrl-C: —" row for that mode.
            if state.mode == "input":
                state.input_buffer = ""
                state.cursor = 0
            elif state.mode == "filter":
                state.input_buffer, state.cursor = state.stashed_input
                state.mode = "scroll"
            continue
        if key == curses.KEY_RESIZE:
            continue  # the next redraw picks up the new size
        height, _width = screen.getmaxyx()
        pane_height = max(1, height - 2)
        if key == curses.KEY_MOUSE:
            # Handled here, not through `handle_key`'s mode dispatch — same reasoning as
            # `KEY_RESIZE` above: a terminal-level event, not a keystroke any mode interprets.
            _handle_wheel_scroll(state, get_mouse_event()[4], pane_height)
            continue
        line = handle_key(state, key, pane_height)
        if line is None:
            continue
        if line.strip():
            state.history.append(line)
        state.history_index = None
        if line.strip() == "clear":
            # A shell-local convenience, not a driver command: `ReplSession` knows nothing of a
            # transcript to wipe, so this never reaches `_run_line`/`session.dispatch`.
            state.output = []
            state.scroll_offset = 0
            continue
        new_output, leave = _run_line(session, line)
        new_output.insert(0, f"{PROMPT}{line}")
        new_output.append(_EOL_MARKER)
        _note_new_output(state, new_output)
        state.output.extend(new_output)
        if leave:
            # One last redraw so a fatal error's explanation is on screen when the shell exits —
            # the loop would otherwise leave before the frame showing it is ever drawn.
            _draw(screen, state)
            return


def _fit(text: str, columns: int) -> str:
    """*text* cut to at most *columns* terminal columns — `_display_width`, not `len`, per char.

    `addnstr`'s own `n` argument counts characters, not columns, so a line of full-width text (a
    Japanese element label, a typed Japanese argument) can pass it and still overrun the row: two
    display columns per character, not one, wrapping onto the next row or hitting `curses.error` at
    the bottom-right cell.
    """
    out: list[str] = []
    used = 0
    for ch in text:
        cost = _display_width(ch)
        if used + cost > columns:
            break
        out.append(ch)
        used += cost
    return "".join(out)


def _input_row(prompt: str, buffer: str, cursor: int, columns: int) -> tuple[str, int]:
    """The `prompt`+`buffer` slice to draw in a `columns`-wide row, plus the column to place the
    cursor at within it.

    Slides right, in display columns (not characters — see `_fit`), just far enough to keep the
    cursor (a character index into `buffer`) on screen once typing runs past the row's width;
    `addnstr`'s own truncation only ever hides the *end* of a long line, which would otherwise
    leave every keystroke past the edge invisible with the cursor pinned in place.
    """
    if columns <= 0:
        return "", 0
    text = prompt + buffer
    cursor_index = len(prompt) + cursor
    cursor_col = _display_width(text[:cursor_index])
    if cursor_col < columns:
        return _fit(text, columns), cursor_col
    target_col = cursor_col - columns + 1
    start = 0
    col = 0
    while start < len(text) and col < target_col:
        col += _display_width(text[start])
        start += 1
    return _fit(text[start:], columns), cursor_col - col


def _draw(screen: Screen, state: TuiState) -> None:
    height, width = screen.getmaxyx()
    screen.erase()
    prompt = "/" if state.mode == "filter" else PROMPT
    # Leaves the last column blank: writing a full-width string into a window's bottom-right cell
    # is a well-known ncurses quirk (the cursor tries to wrap) that can raise `curses.error`.
    n = max(0, width - 1)
    row, cursor_col = _input_row(prompt, state.input_buffer, state.cursor, n)
    screen.addnstr(0, 0, row, n)
    screen.addnstr(1, 0, _fit(_BANNERS[state.mode], n), n)
    pane_height = max(0, height - 2)
    for i, line in enumerate(_visible(state, pane_height)):
        screen.addnstr(2 + i, 0, _fit(line, n), n)
    if state.mode != "scroll":
        screen.move(0, min(cursor_col, n))
    screen.refresh()


def _enable_wheel_reporting() -> None:
    """Ask the terminal to report the mouse wheel as `curses.KEY_MOUSE`, best-effort.

    `curses.error` covers both a terminal that cannot report mouse events at all and — relevant
    only to this function's own tests — a `curses.wrapper` double that skips real `initscr()`;
    either way the shell still works from the keyboard alone.
    """
    with contextlib.suppress(curses.error):
        curses.mousemask(curses.BUTTON4_PRESSED | getattr(curses, "BUTTON5_PRESSED", 0))


def run(session: ReplSession) -> None:
    """The real entry point: an alternate-screen ncurses session against the actual terminal.

    `locale.setlocale` puts ncurses in the terminal's own locale (normally a UTF-8 one) before
    `get_wch` is asked to assemble multi-byte characters from it — without it, a Japanese
    `type`/`tap label:` argument decodes as mojibake instead of the characters actually typed.
    """
    locale.setlocale(locale.LC_ALL, "")

    def _run(stdscr: Screen) -> None:
        _enable_wheel_reporting()
        run_tui(session, stdscr)

    curses.wrapper(_run)
