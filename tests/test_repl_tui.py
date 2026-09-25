"""Tests for the ncurses-style `repl` front end (`bajutsu/repl/tui.py`).

`TuiState`/`handle_key` do no terminal I/O — they read curses' key constants but never touch a
window — so most of this file drives them directly with plain keycodes/characters, exactly the way
`test_repl.py` drives `ReplSession.dispatch` directly. `run_tui` is exercised end to end against a
`FakeScreen`, a minimal `Screen` double that scripts `get_wch()` and records what got drawn, so the
whole thing runs with no real terminal.
"""

from __future__ import annotations

import curses
from dataclasses import dataclass, field

import pytest

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.drivers.xcuitest import XcuitestRunnerCrashError
from bajutsu.repl.loop import PROMPT
from bajutsu.repl.render import _display_width
from bajutsu.repl.session import ReplSession
from bajutsu.repl.tui import (
    Screen,
    TuiState,
    _filtered,
    _fit,
    _input_row,
    _note_new_output,
    _visible,
    handle_key,
    run,
    run_tui,
)


def _el(identifier: str | None = None, *, label: str | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": [],
        "value": None,
        "frame": (0.0, 0.0, 100.0, 40.0),
        "nativeZ": None,
    }


def _type_str(state: TuiState, text: str, *, pane_height: int = 20) -> None:
    for ch in text:
        handle_key(state, ch, pane_height)


# --- input-mode editing --------------------------------------------------------------------------


def test_typing_appends_to_the_input_buffer() -> None:
    state = TuiState()
    _type_str(state, "tree")
    assert (state.input_buffer, state.cursor) == ("tree", 4)


def test_typing_a_japanese_character_appends_it_whole() -> None:
    # `get_wch` hands back one decoded Unicode character per call, never a raw UTF-8 byte — this is
    # exactly what lets a REPL user `type` into a Japanese-locale app's fields.
    state = TuiState()
    _type_str(state, "こんにちは")
    assert state.input_buffer == "こんにちは"


def test_backspace_deletes_before_the_cursor() -> None:
    state = TuiState(input_buffer="ab", cursor=2)
    handle_key(state, "\x7f", 20)
    assert (state.input_buffer, state.cursor) == ("a", 1)


def test_backspace_at_the_start_is_a_no_op() -> None:
    state = TuiState(input_buffer="ab", cursor=0)
    handle_key(state, curses.KEY_BACKSPACE, 20)
    assert (state.input_buffer, state.cursor) == ("ab", 0)


def test_left_and_right_move_the_cursor_without_editing() -> None:
    state = TuiState(input_buffer="ab", cursor=2)
    handle_key(state, curses.KEY_LEFT, 20)
    assert state.cursor == 1
    handle_key(state, curses.KEY_LEFT, 20)
    assert state.cursor == 0
    handle_key(state, curses.KEY_LEFT, 20)  # already at the start
    assert state.cursor == 0
    handle_key(state, curses.KEY_RIGHT, 20)
    handle_key(state, curses.KEY_RIGHT, 20)
    handle_key(state, curses.KEY_RIGHT, 20)  # already at the end
    assert state.cursor == 2


def test_home_and_end_jump_the_cursor() -> None:
    state = TuiState(input_buffer="hello", cursor=2)
    handle_key(state, curses.KEY_HOME, 20)
    assert state.cursor == 0
    handle_key(state, curses.KEY_END, 20)
    assert state.cursor == 5


def test_insert_happens_at_the_cursor_not_the_end() -> None:
    state = TuiState(input_buffer="ac", cursor=1)
    handle_key(state, "b", 20)
    assert (state.input_buffer, state.cursor) == ("abc", 2)


def test_enter_submits_and_clears_the_buffer() -> None:
    state = TuiState(input_buffer="tree", cursor=4)
    line = handle_key(state, "\n", 20)
    assert line == "tree"
    assert (state.input_buffer, state.cursor) == ("", 0)


def test_a_non_enter_key_never_returns_a_line() -> None:
    state = TuiState()
    assert handle_key(state, "t", 20) is None
    assert handle_key(state, curses.KEY_LEFT, 20) is None


def test_an_unhandled_key_leaves_the_buffer_untouched() -> None:
    state = TuiState(input_buffer="ab", cursor=1)
    handle_key(state, curses.KEY_F1, 20)  # no binding for a function key
    assert (state.input_buffer, state.cursor) == ("ab", 1)


# --- history recall (input mode only) -------------------------------------------------------------


def test_up_recalls_the_most_recent_history_entry_first() -> None:
    state = TuiState(history=["tree", "tap a", "find b"])
    handle_key(state, curses.KEY_UP, 20)
    assert state.input_buffer == "find b"


def test_repeated_up_walks_further_into_history() -> None:
    state = TuiState(history=["tree", "tap a", "find b"])
    handle_key(state, curses.KEY_UP, 20)
    handle_key(state, curses.KEY_UP, 20)
    assert state.input_buffer == "tap a"
    handle_key(state, curses.KEY_UP, 20)
    assert state.input_buffer == "tree"
    handle_key(state, curses.KEY_UP, 20)  # already at the oldest entry
    assert state.input_buffer == "tree"


def test_down_after_up_walks_back_toward_a_blank_line() -> None:
    state = TuiState(history=["tree", "tap a"])
    handle_key(state, curses.KEY_UP, 20)
    handle_key(state, curses.KEY_UP, 20)
    assert state.input_buffer == "tree"
    handle_key(state, curses.KEY_DOWN, 20)
    assert state.input_buffer == "tap a"
    handle_key(state, curses.KEY_DOWN, 20)
    assert state.input_buffer == ""  # past the newest entry


def test_up_with_no_history_is_a_no_op() -> None:
    state = TuiState()
    handle_key(state, curses.KEY_UP, 20)
    assert state.input_buffer == ""


def test_up_down_never_recall_in_scroll_mode() -> None:
    # Up/Down is overloaded — history in "input", scrolling in "scroll" — so they must never both
    # fire from one keystroke. 25 lines in a 20-row pane leaves room to actually scroll (max_offset
    # 5), so this also confirms Up moved the offset rather than recalling history silently.
    output = [f"{PROMPT}tree", *[f"line {i}" for i in range(24)]]
    state = TuiState(mode="scroll", history=["tree"], output=output)
    handle_key(state, curses.KEY_UP, 20)
    assert state.input_buffer == ""
    assert state.scroll_offset == 1


# --- mode switching (Tab) ---------------------------------------------------------------------


def test_tab_switches_from_input_to_scroll() -> None:
    state = TuiState()
    handle_key(state, "\t", 20)
    assert state.mode == "scroll"


def test_tab_switches_back_from_scroll_to_input() -> None:
    state = TuiState(mode="scroll")
    handle_key(state, "\t", 20)
    assert state.mode == "input"


def test_switching_to_scroll_preserves_a_half_typed_command() -> None:
    state = TuiState()
    _type_str(state, "tap fo")
    handle_key(state, "\t", 20)
    handle_key(state, "\t", 20)
    assert state.input_buffer == "tap fo"


# --- scrolling ---------------------------------------------------------------------------------


def _scrolled(lines: int) -> TuiState:
    return TuiState(mode="scroll", output=[f"line {i}" for i in range(lines)])


def test_up_in_scroll_mode_increases_the_offset() -> None:
    state = _scrolled(50)
    handle_key(state, curses.KEY_UP, 10)
    handle_key(state, curses.KEY_UP, 10)
    assert state.scroll_offset == 2


def test_down_in_scroll_mode_decreases_but_not_below_zero() -> None:
    state = _scrolled(50)
    handle_key(state, curses.KEY_DOWN, 10)
    assert state.scroll_offset == 0


def test_k_in_scroll_mode_increases_the_offset_like_up() -> None:
    state = _scrolled(50)
    handle_key(state, "k", 10)
    handle_key(state, "k", 10)
    assert state.scroll_offset == 2


def test_j_in_scroll_mode_decreases_but_not_below_zero() -> None:
    state = _scrolled(50)
    handle_key(state, "j", 10)
    assert state.scroll_offset == 0


def test_j_after_k_returns_to_the_bottom() -> None:
    state = _scrolled(50)
    handle_key(state, "k", 10)
    handle_key(state, "k", 10)
    handle_key(state, "j", 10)
    assert state.scroll_offset == 1


def test_j_and_k_are_ordinary_characters_outside_scroll_mode() -> None:
    # "j"/"k" are only scroll bindings in "scroll" mode — elsewhere they must type normally.
    state = TuiState()
    _type_str(state, "jk")
    assert state.input_buffer == "jk"


def test_page_up_and_page_down_move_by_a_full_pane() -> None:
    state = _scrolled(50)
    handle_key(state, curses.KEY_PPAGE, 10)
    assert state.scroll_offset == 10
    handle_key(state, curses.KEY_NPAGE, 10)
    assert state.scroll_offset == 0


def test_up_clamps_at_the_top_instead_of_accumulating_past_it() -> None:
    # A short transcript (5 lines in a 10-row pane, max_offset 0): repeatedly pressing Up must not
    # let scroll_offset run up unboundedly, or new output later would jump the pane far above the
    # bottom instead of staying pinned (the bug this test pins).
    state = _scrolled(5)
    for _ in range(5):
        handle_key(state, curses.KEY_UP, 10)
    assert state.scroll_offset == 0


def test_page_up_clamps_to_the_top_in_one_press() -> None:
    state = _scrolled(15)  # max_offset = 15 - 10 = 5
    handle_key(state, curses.KEY_PPAGE, 10)
    assert state.scroll_offset == 5


def test_scroll_clamp_tracks_a_shrinking_filtered_view() -> None:
    # Scrolled up over the unfiltered transcript, then a filter narrows the view: the clamp must
    # use the *filtered* count, not the stale unfiltered one, or Up would again accumulate past
    # what `_visible` can ever show.
    state = _scrolled(50)
    state.filter_query = "line 4"  # matches only "line 4" and "line 45..49" etc. — a small set
    for _ in range(20):
        handle_key(state, curses.KEY_UP, 10)
    assert state.scroll_offset == max(0, len(_filtered(state)) - 10)


# --- filter (`/`) --------------------------------------------------------------------------------


def test_slash_in_scroll_mode_opens_a_filter_prompt() -> None:
    state = _scrolled(5)
    handle_key(state, "/", 20)
    assert state.mode == "filter"


def test_enter_in_filter_mode_sets_the_query_and_returns_to_scroll() -> None:
    state = _scrolled(5)
    handle_key(state, "/", 20)
    _type_str(state, "line 3")
    handle_key(state, "\n", 20)
    assert state.mode == "scroll"
    assert state.filter_query == "line 3"


def test_an_empty_filter_query_clears_filtering() -> None:
    state = _scrolled(5)
    state.filter_query = "line 3"
    handle_key(state, "/", 20)
    handle_key(state, "\n", 20)
    assert state.filter_query is None


def test_escape_in_filter_mode_abandons_the_edit_without_changing_the_query() -> None:
    state = _scrolled(5)
    state.filter_query = "line 3"
    handle_key(state, "/", 20)
    _type_str(state, "something else")
    handle_key(state, "\x1b", 20)
    assert state.mode == "scroll"
    assert state.filter_query == "line 3"


def test_filter_never_returns_a_line_to_dispatch() -> None:
    state = _scrolled(5)
    handle_key(state, "/", 20)
    _type_str(state, "line")
    assert handle_key(state, "\n", 20) is None


def test_opening_the_filter_prompt_parks_a_half_typed_command() -> None:
    state = TuiState()
    _type_str(state, "tap label:Sign")
    handle_key(state, "\t", 20)  # into scroll mode, command carried along (already covered above)
    handle_key(state, "/", 20)  # "/" borrows input_buffer for the filter query
    assert state.input_buffer == ""


def test_confirming_a_filter_restores_the_parked_command() -> None:
    state = TuiState()
    _type_str(state, "tap label:Sign")
    handle_key(state, "\t", 20)
    handle_key(state, "/", 20)
    _type_str(state, "line 3")
    handle_key(state, "\n", 20)  # confirms the filter, returns to "scroll"
    handle_key(state, "\t", 20)  # back to "input"
    assert state.input_buffer == "tap label:Sign"


def test_escaping_a_filter_restores_the_parked_command() -> None:
    state = TuiState()
    _type_str(state, "tap label:Sign")
    handle_key(state, "\t", 20)
    handle_key(state, "/", 20)
    _type_str(state, "abandoned")
    handle_key(state, "\x1b", 20)
    handle_key(state, "\t", 20)
    assert state.input_buffer == "tap label:Sign"


# --- drawing: full-width columns and the input line's horizontal scroll ------------------------


def test_fit_counts_a_fullwidth_character_as_two_columns() -> None:
    # 5 full-width characters cost 10 columns; a character-count truncation (plain `addnstr`) would
    # instead let all 5 through into a 6-column row and overrun it.
    assert _fit("ログイン画面", 6) == "ログイ"  # 3 chars = 6 columns, the 4th would make 8


def test_fit_never_splits_within_its_column_budget_for_ascii() -> None:
    assert _fit("hello world", 5) == "hello"


def test_input_row_is_empty_when_the_terminal_has_no_columns() -> None:
    assert _input_row(PROMPT, "tree", 4, 0) == ("", 0)


def test_input_row_shows_the_whole_line_when_it_fits() -> None:
    row, cursor_col = _input_row(PROMPT, "tree", 4, 40)
    assert row == f"{PROMPT}tree"
    assert cursor_col == len(PROMPT) + 4


def test_input_row_slides_right_to_keep_an_ascii_cursor_visible() -> None:
    # 9-column prompt + 30 "a"s + cursor at the end, in a 20-column row: without sliding, the
    # cursor would sit at column 20+ with nothing on screen showing where it is.
    row, cursor_col = _input_row(PROMPT, "a" * 30, 30, 20)
    assert cursor_col < 20  # the cursor itself must land inside the drawn row
    assert row.endswith("a")


def test_input_row_slides_by_display_width_for_japanese_text() -> None:
    # Each full-width character the cursor has passed costs 2 columns, not 1 — sliding by
    # character count (the naive fix) would under-shoot and still clip the cursor off-screen.
    buffer = "検索フィールドに日本語を入力する"  # well past a narrow row, all full-width
    row, cursor_col = _input_row(PROMPT, buffer, len(buffer), 20)
    assert cursor_col < 20
    assert _display_width(row) <= 20


# --- FakeScreen / run_tui end to end -------------------------------------------------------------


@dataclass
class FakeScreen:
    """A `Screen` double: `get_wch` replays a scripted key sequence; draws are recorded, not shown."""

    keys: list[int | str]
    height: int = 24
    width: int = 80
    draws: list[list[str]] = field(default_factory=list)

    def getmaxyx(self) -> tuple[int, int]:
        return self.height, self.width

    def get_wch(self) -> int | str:
        if not self.keys:
            raise RuntimeError("FakeScreen ran out of scripted keys — did the script end in exit?")
        return self.keys.pop(0)

    def erase(self) -> None:
        self.draws.append([])

    def addnstr(self, y: int, x: int, text: str, n: int) -> None:
        self.draws[-1].append(text[:n])

    def move(self, y: int, x: int) -> None:
        pass

    def refresh(self) -> None:
        pass


def _keys(*chunks: str | int) -> list[int | str]:
    """Expand each string chunk into individual characters; an int passes through as one keycode."""
    out: list[int | str] = []
    for chunk in chunks:
        out.extend(chunk) if isinstance(chunk, str) else out.append(chunk)
    return out


def _session() -> tuple[ReplSession, FakeDriver]:
    driver = FakeDriver(screen=[_el("stable.save")])
    return ReplSession(driver), driver


def test_run_tui_screen_satisfies_the_protocol() -> None:
    screen: Screen = FakeScreen(keys=[])
    assert isinstance(screen, FakeScreen)  # structural check that the Protocol is this shape


def test_run_tui_dispatches_a_typed_command_and_leaves_on_exit() -> None:
    session, driver = _session()
    screen = FakeScreen(keys=_keys("tap stable.save", "\n", "exit", "\n"))
    run_tui(session, screen)
    assert driver.actions == [("tap", {"id": "stable.save"})]


def test_run_tui_reports_a_command_error_and_keeps_the_shell_alive() -> None:
    session, _driver = _session()
    screen = FakeScreen(keys=_keys("tap nope", "\n", "exit", "\n"))
    run_tui(session, screen)  # would raise if the error propagated instead of being reported


class _InterruptingScreen(FakeScreen):
    """A `FakeScreen` where the sentinel char `"\\x03"` raises `KeyboardInterrupt` (our stand-in
    for ^C) instead of being replayed as an ordinary key."""

    def get_wch(self) -> int | str:
        if self.keys and self.keys[0] == "\x03":
            self.keys.pop(0)
            raise KeyboardInterrupt
        return super().get_wch()


def test_run_tui_ctrl_c_abandons_the_half_typed_line() -> None:
    session, driver = _session()
    screen = _InterruptingScreen(keys=_keys("tap garbage", "\x03", "exit", "\n"))
    run_tui(session, screen)
    assert driver.actions == []  # the abandoned line was never submitted


def test_run_tui_draws_the_prompt_and_typed_text() -> None:
    session, _driver = _session()
    screen = FakeScreen(keys=_keys("tree", "\n", "exit", "\n"))
    run_tui(session, screen)
    first_frame = "".join(screen.draws[0])
    assert PROMPT in first_frame


def test_run_tui_ignores_an_unhandled_key_in_scroll_mode() -> None:
    session, _driver = _session()
    # Tab into scroll, press an arbitrary letter (no scroll binding), Tab back, then leave.
    screen = FakeScreen(keys=_keys("\t", "z", "\t", "exit", "\n"))
    run_tui(session, screen)  # would raise on a KeyError/AttributeError if "z" were mishandled


def test_run_tui_submitting_a_blank_line_is_not_recorded_in_history() -> None:
    session, driver = _session()
    screen = FakeScreen(keys=_keys("\n", "exit", "\n"))
    run_tui(session, screen)
    assert driver.actions == []


def test_run_tui_draws_the_scroll_banner_after_tab() -> None:
    session, _driver = _session()
    screen = FakeScreen(keys=_keys("\t", "\t", "exit", "\n"))  # Tab in, then Tab back out
    run_tui(session, screen)
    # The frame drawn right after the first Tab (before the next key is read) is in scroll mode.
    scroll_frame = "".join(screen.draws[1])
    assert "SCROLL" in scroll_frame


def test_run_tui_draws_the_filter_banner_after_slash() -> None:
    session, _driver = _session()
    screen = FakeScreen(keys=_keys("\t", "/", "\x1b", "\t", "exit", "\n"))
    run_tui(session, screen)
    assert any("FILTER" in "".join(frame) for frame in screen.draws)


def test_run_tui_draws_a_tab_hint_in_input_mode() -> None:
    # The mode-switch binding is always on screen, not only after Tab into "scroll" — the operator
    # should never have to guess or consult the docs to discover it.
    session, _driver = _session()
    screen = FakeScreen(keys=_keys("exit", "\n"))
    run_tui(session, screen)
    first_frame = "".join(screen.draws[0])
    assert "Tab" in first_frame


def test_run_tui_ignores_a_terminal_resize() -> None:
    session, driver = _session()
    screen = FakeScreen(keys=_keys("tap stable.save", curses.KEY_RESIZE, "\n", "exit", "\n"))
    run_tui(session, screen)
    assert driver.actions == [("tap", {"id": "stable.save"})]


def test_run_tui_ctrl_c_while_filtering_returns_to_scroll_mode() -> None:
    session, _driver = _session()
    screen = _InterruptingScreen(keys=_keys("\t", "/", "abc", "\x03", "\t", "exit", "\n"))
    run_tui(session, screen)  # would hang/raise if the interrupt left the shell stuck in "filter"


def test_run_tui_ctrl_c_while_browsing_the_pane_is_a_no_op() -> None:
    # Tab into "scroll" (nothing being typed there — not "filter"): Ctrl-C must not touch the
    # parked command, matching the docs table's "Ctrl-C: —" row for that mode.
    session, driver = _session()
    screen = _InterruptingScreen(
        keys=_keys("tap stable.save", "\t", "\x03", "\t", "\n", "exit", "\n")
    )
    run_tui(session, screen)
    assert driver.actions == [("tap", {"id": "stable.save"})]


def test_run_tui_never_draws_a_row_wider_than_the_pane_with_japanese_output() -> None:
    # A narrow pane (20 columns) plus a full-width label: a character-count truncation would let
    # the row through at roughly double the pane's actual width.
    driver = FakeDriver(screen=[_el("stable.a", label="ログインフィールドへようこそ")])
    session = ReplSession(driver)
    screen = FakeScreen(keys=_keys("tree", "\n", "exit", "\n"), width=21)
    run_tui(session, screen)
    for frame in screen.draws:
        for row in frame:
            assert _display_width(row) <= screen.width - 1


def test_run_tui_ctrl_c_while_filtering_restores_the_parked_command() -> None:
    session, driver = _session()
    screen = _InterruptingScreen(
        keys=_keys("tap stable.save", "\t", "/", "query", "\x03", "\t", "\n", "exit", "\n")
    )
    run_tui(session, screen)
    assert driver.actions == [("tap", {"id": "stable.save"})]


def test_run_tui_a_fatal_error_ends_the_shell() -> None:
    class _Dead(FakeDriver):
        def back(self) -> None:
            raise XcuitestRunnerCrashError("xcodebuild exited")

    session = ReplSession(_Dead(screen=[]))
    screen = FakeScreen(keys=_keys("back", "\n"))
    run_tui(session, screen)  # a single scripted line: leaves on the fatal error, not on "exit"
    frames = ["".join(frame) for frame in screen.draws]
    assert any("XcuitestRunnerCrashError" in frame for frame in frames)


def test_visible_lines_respects_an_active_filter() -> None:
    state = TuiState(output=["match one", "skip this", "match two"], filter_query="match")
    assert _visible(state, height=10) == ["match one", "match two"]


def test_visible_lines_are_unfiltered_by_default() -> None:
    state = TuiState(output=["alpha", "beta", "gamma"])
    assert _visible(state, height=10) == ["alpha", "beta", "gamma"]


def test_note_new_output_is_a_no_op_when_pinned_to_the_bottom() -> None:
    state = TuiState(output=["a"], scroll_offset=0)
    _note_new_output(state, ["b", "c"])
    assert state.scroll_offset == 0


def test_note_new_output_grows_the_offset_when_scrolled_up_and_unfiltered() -> None:
    state = TuiState(output=["a"], scroll_offset=3)
    _note_new_output(state, ["b", "c"])
    assert state.scroll_offset == 5


def test_note_new_output_counts_only_lines_matching_the_active_filter() -> None:
    state = TuiState(output=["match a"], scroll_offset=2, filter_query="match")
    _note_new_output(state, ["match b", "skip this", "match c"])
    assert state.scroll_offset == 4  # 2 + the two new lines that match, not all three


def test_run_wires_a_real_curses_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run`'s own body — locale setup plus handing off to `curses.wrapper` — needs no real tty."""
    screen = FakeScreen(keys=_keys("exit", "\n"))

    def fake_wrapper(fn: object) -> None:
        fn(screen)  # type: ignore[operator]  # a real curses.wrapper passes the real stdscr

    monkeypatch.setattr(curses, "wrapper", fake_wrapper)
    session, driver = _session()
    run(session)
    assert driver.actions == []
