"""`bajutsu repl` (BE-0423): the command set, the tree rendering, and the prompt loop.

Everything here runs against `FakeDriver`, with no device and no browser — the shell is a thin loop
over the shared `Driver`, so the part worth pinning is its own behavior: which driver call each
typed verb makes, what the table and the JSON say, that a zero- or many-match selector fails the
way `run` would, and that the loop leaves on exactly the three ways out (`exit`, `quit`, end of
input) while a command failure keeps it alive.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

import bajutsu.repl.cli as repl_cli
from bajutsu.cli import app
from bajutsu.common.config import load_config, resolve
from bajutsu.common.devices import errors as device_errors
from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.drivers.xcuitest import XcuitestChannelError
from bajutsu.common.drivers.xcuitest_live import WebDriverError
from bajutsu.common.platform_lifecycle import Environment, FakeEnvironment, WebEnvironment
from bajutsu.common.platform_lifecycle.environments.xcuitest_live import XcuitestLiveEnvironment
from bajutsu.common.scenario import Preconditions
from bajutsu.repl.loop import PROMPT, repl_loop
from bajutsu.repl.render import _display_width, render_json, render_table
from bajutsu.repl.session import COMMAND_ERRORS, ReplExit, ReplSession

runner = CliRunner()

_EFF = resolve(load_config("targets: { demo: { bundleId: com.example.demo } }"), "demo")


def _el(
    identifier: str | None = None,
    *,
    label: str | None = None,
    traits: list[str] | None = None,
    value: str | None = None,
    frame: base.Frame = (0.0, 0.0, 100.0, 40.0),
) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or [],
        "value": value,
        "frame": frame,
        "nativeZ": None,
    }


def _session(*elements: base.Element) -> tuple[ReplSession, FakeDriver]:
    driver = FakeDriver(screen=list(elements))
    return ReplSession(driver), driver


def _kinds(driver: FakeDriver) -> list[str]:
    return [kind for kind, _arg in driver.actions]


# --- rendering ---------------------------------------------------------------------------------


def test_table_shows_the_five_selector_fields_aligned() -> None:
    table = render_table(
        [
            _el("stable.save", label="Save", traits=["button"], frame=(1.0, 2.0, 80.0, 44.0)),
            _el("stable.cancel-with-a-long-id", label="Cancel", traits=["button", "other"]),
        ]
    )
    header, separator, save, cancel = table.splitlines()
    assert header.split() == ["id", "label", "traits", "value", "frame"]
    assert set(separator) == {"-", " "}
    assert save.split() == ["stable.save", "Save", "button", "-", "1,2,80,44"]
    assert cancel.split() == [
        "stable.cancel-with-a-long-id",
        "Cancel",
        "button,other",
        "-",
        "0,0,100,40",
    ]
    # Every column but the last is padded to the widest cell, so ids of different lengths line up.
    assert save.index("Save") == cancel.index("Cancel")


def test_table_never_truncates_a_long_label() -> None:
    label = "とても長いラベル " * 20
    assert label.strip() in render_table([_el("x", label=label)])


def test_table_renders_an_absent_field_as_a_dash() -> None:
    row = render_table([_el(None, label=None)]).splitlines()[-1]
    assert row.split() == ["-", "-", "-", "-", "0,0,100,40"]


def test_table_of_an_empty_list_is_just_the_header() -> None:
    header, separator = render_table([]).splitlines()
    assert header.split() == ["id", "label", "traits", "value", "frame"]
    assert set(separator) == {"-", " "}


def test_display_width_counts_a_fullwidth_character_as_two() -> None:
    assert _display_width("ab") == 2
    assert _display_width("保存") == 4
    assert _display_width("a保") == 3


def test_table_aligns_a_fullwidth_label_by_its_display_width_not_its_length() -> None:
    # "保存" is 2 Python characters but 4 terminal columns; a naive len()-based pad would misalign
    # every column after it against a same-visual-width ASCII row like "Cancel".
    table = render_table(
        [
            _el("stable.a", label="保存", traits=["button"]),
            _el("stable.b", label="Cancel", traits=["button"]),
        ]
    )
    row_a, row_b = table.splitlines()[2:]
    assert _display_width(row_a[: row_a.index("button")]) == _display_width(
        row_b[: row_b.index("button")]
    )


def test_table_escapes_a_newline_and_a_tab_in_a_field() -> None:
    # An accessible name carrying a raw newline or tab would otherwise split or misalign the table
    # once `_table` calls `.splitlines()` on the rendered result.
    row = render_table([_el("x", label="line1\nline2", value="a\tb")]).splitlines()[-1]
    assert row.split() == ["x", "line1\\nline2", "-", "a\\tb", "0,0,100,40"]


def test_json_is_the_tree_verbatim_with_unescaped_non_ascii() -> None:
    out = render_json([_el("x", label="保存")])
    assert '"identifier": "x"' in out
    assert "保存" in out  # ensure_ascii=False: a Japanese label stays readable


# --- tree / find -------------------------------------------------------------------------------


def test_tree_renders_the_live_screen() -> None:
    session, _driver = _session(_el("stable.save", label="Save", traits=["button"]))
    out = session.dispatch("tree")
    assert out[0].split() == ["id", "label", "traits", "value", "frame"]
    assert any("stable.save" in line for line in out)


def test_tree_json_emits_parsable_lines() -> None:
    session, _driver = _session(_el("stable.save"))
    assert session.dispatch("tree --json") == render_json([_el("stable.save")]).splitlines()


def test_tree_says_so_when_the_screen_is_empty() -> None:
    session, _driver = _session()
    assert session.dispatch("tree") == ["the screen reports no elements"]


def test_tree_rejects_an_unknown_flag_without_reading_the_screen() -> None:
    session, _driver = _session(_el("stable.save"))
    assert session.dispatch("tree --xml") == ["usage: tree [--json]"]


def test_find_filters_on_id_and_on_label() -> None:
    session, _driver = _session(
        _el("stable.save", label="Save"),
        _el("stable.cancel", label="Cancel"),
        _el("other.thing", label="Save draft"),
    )
    rows = session.dispatch("find save")[2:]  # past the header and the separator
    assert [r.split()[0] for r in rows] == ["stable.save"]
    assert [r.split()[0] for r in session.dispatch("find Save")[2:]] == [
        "stable.save",
        "other.thing",
    ]


def test_find_is_case_sensitive_like_every_selector_match() -> None:
    session, _driver = _session(_el("stable.saveButton"))
    assert session.dispatch("find savebutton") == ["no id or label contains 'savebutton'"]


def test_find_needs_a_substring() -> None:
    session, _driver = _session(_el("stable.save"))
    assert session.dispatch("find") == ["usage: find <substring>"]


class _SettledFake(FakeDriver):
    """A backend whose reads must settle first (adb's shape), to pin which read `tree` asks for."""

    def __init__(self, screen: list[base.Element]) -> None:
        super().__init__(screen=screen)
        self.settled_reads = 0

    def settled_query(self) -> list[base.Element]:
        self.settled_reads += 1
        return self.query()


def test_tree_takes_the_actuation_grade_read_where_the_backend_offers_one() -> None:
    # The read-lag barrier (BE-0332) is what keeps a `tree` typed right after a `tap` from
    # describing the pre-tap screen on adb, so the shell must ask through `settled_query`.
    driver = _SettledFake([_el("stable.save")])
    assert isinstance(driver, base.SettledReadProvider)
    ReplSession(driver).dispatch("tree")
    assert driver.settled_reads == 1


def test_tree_takes_a_plain_query_on_a_backend_without_a_settled_read() -> None:
    session, driver = _session(_el("stable.save"))
    assert not isinstance(driver, base.SettledReadProvider)
    session.dispatch("tree")  # no settled read to assert on; the point is that it does not raise


# --- tap / type --------------------------------------------------------------------------------


def test_tap_addresses_the_element_by_id() -> None:
    session, driver = _session(_el("stable.save"))
    assert session.dispatch("tap stable.save") == ["tapped stable.save"]
    assert driver.actions == [("tap", {"id": "stable.save"})]


def test_tap_accepts_an_id_carrying_a_space() -> None:
    session, driver = _session(_el("save button"))
    assert session.dispatch("tap save button") == ["tapped save button"]
    assert driver.actions == [("tap", {"id": "save button"})]


def test_tap_needs_an_id() -> None:
    session, _driver = _session(_el("stable.save"))
    assert session.dispatch("tap") == ["usage: tap <id>"]


def test_tap_raises_element_not_found_for_an_id_the_screen_has_not_got() -> None:
    session, _driver = _session(_el("stable.save"))
    with pytest.raises(base.ElementNotFound):
        session.dispatch("tap stable.typo")


def test_tap_raises_ambiguous_selector_rather_than_picking_the_first_match() -> None:
    # Determinism first (prime directive 2): two content-distinct matches fail immediately.
    session, _driver = _session(
        _el("stable.save", label="Save"),
        _el("stable.save", label="Save draft"),
    )
    with pytest.raises(base.AmbiguousSelector):
        session.dispatch("tap stable.save")


def test_tap_raises_element_not_tappable_rather_than_scrolling_the_cover_away() -> None:
    # The deliberate v1 gap: `run` would clear the obstruction with a bounded scroll first, while
    # the shell surfaces the driver's own answer, which names the covering element.
    session, _driver = _session(
        _el("stable.save", frame=(0.0, 0.0, 100.0, 40.0)),
        _el("overlay", frame=(0.0, 0.0, 200.0, 200.0)),
    )
    with pytest.raises(base.ElementNotTappable, match="overlay"):
        session.dispatch("tap stable.save")


def test_type_focuses_the_element_before_typing() -> None:
    session, driver = _session(_el("stable.query"))
    assert session.dispatch("type stable.query hello world") == ["typed into stable.query"]
    assert driver.actions == [("tap", {"id": "stable.query"}), ("type", "hello world")]


def test_type_needs_both_an_id_and_text() -> None:
    session, driver = _session(_el("stable.query"))
    assert session.dispatch("type stable.query") == ["usage: type <id> <text>"]
    assert session.dispatch("type") == ["usage: type <id> <text>"]
    assert driver.actions == []


# --- back / screenshot -------------------------------------------------------------------------


def test_back_navigates_back() -> None:
    session, driver = _session()
    assert session.dispatch("back") == ["went back"]
    assert _kinds(driver) == ["back"]


def test_back_takes_no_argument() -> None:
    session, driver = _session()
    assert session.dispatch("back home") == ["usage: back"]
    assert driver.actions == []


def test_screenshot_writes_the_given_path() -> None:
    session, driver = _session()
    assert session.dispatch("screenshot shot.png") == ["wrote shot.png"]
    assert driver.actions == [("screenshot", "shot.png")]


def test_screenshot_auto_names_from_the_shared_run_id_timestamp() -> None:
    session, driver = _session()
    (line,) = session.dispatch("screenshot")
    path = line.removeprefix("wrote ")
    assert driver.actions == [("screenshot", path)]
    stem = path.removeprefix("repl-").removesuffix(".png")
    date, _, time = stem.partition("-")
    assert (len(date), len(time)) == (8, 6)
    assert stem.replace("-", "").isdigit()


# --- dispatch, help, and the prompt loop ---------------------------------------------------------


def test_a_blank_line_says_nothing() -> None:
    session, driver = _session()
    assert session.dispatch("   ") == []
    assert driver.actions == []


def test_help_lists_every_verb_dispatch_accepts() -> None:
    session, _driver = _session()
    listed = {line.split()[0] for line in session.dispatch("help")}
    assert listed == {"tree", "find", "tap", "type", "back", "screenshot", "help", "exit"}


def test_an_unknown_verb_points_at_help() -> None:
    session, _driver = _session()
    assert session.dispatch("swipe up") == [
        "unknown command: swipe (type `help` for the command set)"
    ]


@pytest.mark.parametrize("word", ["exit", "quit"])
def test_exit_and_quit_both_leave(word: str) -> None:
    session, _driver = _session()
    with pytest.raises(ReplExit):
        session.dispatch(word)


def _scripted(lines: list[str | type[BaseException]]) -> Callable[[str], str]:
    """A `read_line` that replays *lines*, raising an entry that is an exception class."""
    remaining = list(lines)

    def read_line(prompt: str) -> str:
        assert prompt == PROMPT
        nxt = remaining.pop(0)
        if isinstance(nxt, type):
            raise nxt
        return nxt

    return read_line


def _run_loop(driver: FakeDriver, lines: list[str | type[BaseException]]) -> list[str]:
    said: list[str] = []
    repl_loop(ReplSession(driver), _scripted(lines), said.append)
    return said


def test_the_loop_runs_each_line_then_leaves_on_exit() -> None:
    driver = FakeDriver(screen=[_el("stable.save")])
    said = _run_loop(driver, ["tap stable.save", "back", "exit"])
    assert said == ["tapped stable.save", "went back"]
    assert _kinds(driver) == ["tap", "back"]


def test_the_loop_leaves_on_end_of_input() -> None:
    # Ctrl-D: close the prompt's own line rather than leaving the shell mid-line.
    assert _run_loop(FakeDriver(), [EOFError]) == [""]


def test_an_interrupt_abandons_the_line_and_keeps_the_shell_alive() -> None:
    driver = FakeDriver(screen=[_el("stable.save")])
    said = _run_loop(driver, [KeyboardInterrupt, "tap stable.save", "exit"])
    assert said == ["", "tapped stable.save"]


def test_a_failing_command_is_reported_and_the_shell_reads_the_next_line() -> None:
    driver = FakeDriver(screen=[_el("stable.save")])
    said = _run_loop(driver, ["tap stable.typo", "tap stable.save", "exit"])
    assert said[0].startswith("ElementNotFound: ")
    assert said[1] == "tapped stable.save"


def test_an_ambiguous_selector_is_reported_by_name_the_way_run_reports_it() -> None:
    driver = FakeDriver(screen=[_el("dup", label="one"), _el("dup", label="two")])
    (reported,) = _run_loop(driver, ["tap dup", "exit"])
    assert reported.startswith("AmbiguousSelector: ")


def test_a_bug_in_a_command_is_not_swallowed_by_the_loop() -> None:
    # The loop reports what a typed command can legitimately end in; anything else must propagate,
    # so a real defect surfaces instead of reading as a bad id.
    class _Broken(FakeDriver):
        def back(self) -> None:
            raise ZeroDivisionError("not an answer to the operator's question")

    with pytest.raises(ZeroDivisionError):
        _run_loop(_Broken(), ["back"])


def test_an_unsupported_action_is_reported_rather_than_crashing_the_shell() -> None:
    # A backend that cannot perform a verb at all (a multi-touch gesture on a single-touch actuator
    # is the usual shape) is as legitimate an answer as a bad id — COMMAND_ERRORS names it too.
    class _NoBack(FakeDriver):
        def back(self) -> None:
            raise base.UnsupportedAction("no back on this backend")

    driver = _NoBack(screen=[_el("stable.save")])
    said = _run_loop(driver, ["back", "tap stable.save", "exit"])
    assert said[0].startswith("UnsupportedAction: ")
    assert said[1] == "tapped stable.save"


def test_an_os_error_is_reported_rather_than_crashing_the_shell() -> None:
    # A screenshot path the filesystem rejects (a missing directory, no permission) is a filesystem
    # fact the operator can act on, not a shell crash.
    class _BadPath(FakeDriver):
        def screenshot(self, path: str) -> None:
            raise OSError("no such directory")

    said = _run_loop(_BadPath(), ["screenshot bad/path.png", "back", "exit"])
    assert said[0].startswith("OSError: ")
    assert said[1] == "went back"


def test_a_called_process_error_is_reported_rather_than_crashing_the_shell() -> None:
    # adb's action methods bottom out in a bare `subprocess.run(..., check=True)` with no wrapping
    # into DeviceError, so a USB flake surfaces this directly rather than through the shell's other
    # error types.
    assert subprocess.CalledProcessError in COMMAND_ERRORS

    class _Flaky(FakeDriver):
        def back(self) -> None:
            raise subprocess.CalledProcessError(1, ["adb", "shell", "input", "keyevent", "4"])

    said = _run_loop(_Flaky(), ["back", "exit"])
    assert said[0].startswith("CalledProcessError: ")


def test_a_web_driver_error_is_reported_rather_than_crashing_the_shell() -> None:
    # The `--udid https://…` live route raises its own RuntimeError subclass on a lost connection or
    # a grid hiccup, not DeviceError.
    assert WebDriverError in COMMAND_ERRORS

    class _Wedged(FakeDriver):
        def back(self) -> None:
            raise WebDriverError("session terminated")

    said = _run_loop(_Wedged(), ["back", "exit"])
    assert said[0].startswith("WebDriverError: ")


def test_an_xcuitest_channel_error_is_reported_rather_than_crashing_the_shell() -> None:
    # The local XCUITest runner channel raises its own RuntimeError subclass on a lost/bad response
    # (a failed tap, type, or screenshot request), not DeviceError.
    assert XcuitestChannelError in COMMAND_ERRORS

    class _RunnerGone(FakeDriver):
        def back(self) -> None:
            raise XcuitestChannelError("runner stopped answering")

    said = _run_loop(_RunnerGone(), ["back", "exit"])
    assert said[0].startswith("XcuitestChannelError: ")


# --- the command's own wiring --------------------------------------------------------------------


def test_repl_is_registered_and_documents_its_web_only_flags() -> None:
    # Introspect the command's declared options rather than the rendered `--help` text — Rich
    # formats help in an environment-dependent way (terminal width, TTY detection), so its output is
    # not a stable substring to assert on (the idiom `test_record_exposes_token_budget_flags` uses).
    import typer.main

    result = runner.invoke(app, ["repl", "--help"])
    assert result.exit_code == 0
    repl_cmd = typer.main.get_command(app).commands["repl"]  # type: ignore[attr-defined]
    flags = {opt for p in repl_cmd.params for opt in (*p.opts, *p.secondary_opts)}
    for flag in ("--target", "--udid", "--backend", "--erase", "--headed", "--browser", "--config"):
        assert flag in flags


def test_repl_needs_a_target() -> None:
    assert runner.invoke(app, ["repl"]).exit_code != 0


def _fake_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\n"
        "targets:\n  demo: { bundleId: com.example.demo, idNamespaces: [home] }\n",
        encoding="utf-8",
    )
    return cfg


def _stub_launch(
    monkeypatch: pytest.MonkeyPatch, driver: FakeDriver | None, *, error: str = ""
) -> list[str]:
    """Stand in for the device boundary, so the command body around it runs for real.

    The driver launch, the udid probe, and the launch server are the external edges; stubbing just
    those leaves config resolution, actuator selection, the shell loop, and the exit path covered.
    Returns the list the launch-server teardown appends to, so a test can assert it was stopped.
    """
    stopped: list[str] = []
    monkeypatch.setattr(
        "bajutsu.common.backend_cli.simctl.resolve_udid", lambda u, run=None: "FAKE-UDID"
    )
    monkeypatch.setattr(
        repl_cli,
        "_start_launch_server_or_exit",
        lambda eff, **kw: (lambda: stopped.append("stop"), None),
    )

    def launch(*_args: object, **_kwargs: object) -> tuple[FakeDriver, None]:
        if error:
            raise device_errors.DeviceError(error)
        assert driver is not None
        return driver, None

    monkeypatch.setattr(repl_cli, "launch_driver", launch)
    return stopped


def test_repl_never_resolves_a_udid_for_the_web_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Resolving "booted" would shell out to simctl and crash off-macOS; a web target has no udid to
    # resolve at all, so the guard must skip the call rather than merely tolerate its result.
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [playwright] }\n"
        "targets:\n  demo: { baseUrl: 'http://localhost:9999', idNamespaces: [home] }\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("bajutsu.cli._shared.ensure_web_runtime", lambda *a, **k: None)
    monkeypatch.setattr("bajutsu.cli._shared.select_actuator", lambda *a, **k: "playwright")
    monkeypatch.setattr(
        repl_cli, "_start_launch_server_or_exit", lambda eff, **kw: ((lambda: None), None)
    )

    def unexpected(*_a: object, **_k: object) -> str:
        raise AssertionError("resolve_udid must not be called for the web backend")

    monkeypatch.setattr("bajutsu.common.backend_cli.simctl.resolve_udid", unexpected)
    monkeypatch.setattr(repl_cli, "launch_driver", lambda *a, **k: (FakeDriver(), None))

    result = runner.invoke(app, ["repl", "--target", "demo", "--config", str(cfg)], input="exit\n")
    assert result.exit_code == 0


def test_repl_resolves_a_udid_through_the_selected_environment_for_adb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # For --backend adb, resolving "booted" through simctl would shell out to `xcrun simctl` on an
    # Android-only host (or hand an iOS-shaped id to adb) and crash before the shell ever opens —
    # resolution must go through the selected environment's own resolver (adb's `resolve_serial`).
    monkeypatch.setattr("bajutsu.cli._shared.select_actuator", lambda *a, **k: "adb")

    def unexpected(*_a: object, **_k: object) -> str:
        raise AssertionError("simctl.resolve_udid must not be called for the adb backend")

    monkeypatch.setattr("bajutsu.common.backend_cli.simctl.resolve_udid", unexpected)
    monkeypatch.setattr(
        "bajutsu.common.backend_cli.adb.resolve_serial", lambda u, run=None: "ADB-SERIAL"
    )
    monkeypatch.setattr(
        repl_cli, "_start_launch_server_or_exit", lambda eff, **kw: ((lambda: None), None)
    )
    captured: dict[str, object] = {}

    def launch(udid: str, *_a: object, **_k: object) -> tuple[FakeDriver, None]:
        captured["udid"] = udid
        return FakeDriver(), None

    monkeypatch.setattr(repl_cli, "launch_driver", launch)

    result = runner.invoke(
        app, ["repl", "--target", "demo", "--config", str(_fake_config(tmp_path))], input="exit\n"
    )
    assert result.exit_code == 0
    assert captured["udid"] == "ADB-SERIAL"


def test_repl_defaults_to_no_erase_on_the_live_webdriver_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The live `--udid https://…` route's device is already booted with its build installed
    # (BE-0238); its environment explicitly rejects `Preconditions(erase=True)`, so an unset
    # `--erase` must default to off there rather than the local route's on.
    monkeypatch.setattr("bajutsu.cli._shared.select_actuator", lambda *a, **k: "xcuitest")
    monkeypatch.setattr(
        repl_cli, "_start_launch_server_or_exit", lambda eff, **kw: ((lambda: None), None)
    )
    captured: dict[str, object] = {}

    def launch(
        _udid: str, _eff: object, _actuator: str, pre: Preconditions, **_k: object
    ) -> tuple[FakeDriver, None]:
        captured["erase"] = pre.erase
        return FakeDriver(), None

    monkeypatch.setattr(repl_cli, "launch_driver", launch)

    result = runner.invoke(
        app,
        [
            "repl",
            "--target",
            "demo",
            "--udid",
            "https://grid.example/wd/hub",
            "--config",
            str(_fake_config(tmp_path)),
        ],
        input="exit\n",
    )
    assert result.exit_code == 0
    assert captured["erase"] is False


def test_repl_rejects_explicit_erase_on_the_live_webdriver_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An operator who explicitly asks for --erase on the live route gets a clean CLI error instead
    # of an UnsupportedAction traceback out of the environment's own `start`.
    monkeypatch.setattr("bajutsu.cli._shared.select_actuator", lambda *a, **k: "xcuitest")
    monkeypatch.setattr(
        repl_cli, "_start_launch_server_or_exit", lambda eff, **kw: ((lambda: None), None)
    )

    def unexpected(*_a: object, **_k: object) -> tuple[FakeDriver, None]:
        raise AssertionError("launch_driver must not be reached for a rejected --erase")

    monkeypatch.setattr(repl_cli, "launch_driver", unexpected)

    result = runner.invoke(
        app,
        [
            "repl",
            "--target",
            "demo",
            "--udid",
            "https://grid.example/wd/hub",
            "--erase",
            "--config",
            str(_fake_config(tmp_path)),
        ],
    )
    assert result.exit_code == 2
    assert "not supported on the live" in result.output


def test_repl_honors_an_explicit_no_erase_on_the_local_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An explicit --no-erase on the ordinary (non-live) route is neither the unset default nor the
    # live-route rejection — it passes straight through to the launch.
    _stub_launch(monkeypatch, FakeDriver())
    captured: dict[str, object] = {}

    def launch(
        _udid: str, _eff: object, _actuator: str, pre: Preconditions, **_k: object
    ) -> tuple[FakeDriver, None]:
        captured["erase"] = pre.erase
        return FakeDriver(), None

    monkeypatch.setattr(repl_cli, "launch_driver", launch)

    result = runner.invoke(
        app,
        ["repl", "--target", "demo", "--no-erase", "--config", str(_fake_config(tmp_path))],
        input="exit\n",
    )
    assert result.exit_code == 0
    assert captured["erase"] is False


def test_repl_launches_the_app_and_drives_the_typed_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = FakeDriver(screen=[_el("stable.save", label="Save")])
    _stub_launch(monkeypatch, driver)
    result = runner.invoke(
        app,
        ["repl", "--target", "demo", "--config", str(_fake_config(tmp_path))],
        input="tree\ntap stable.save\nexit\n",
    )
    assert result.exit_code == 0
    assert "demo is up on fake" in result.output
    assert "stable.save" in result.output
    assert driver.actions == [("tap", {"id": "stable.save"})]


def test_repl_exits_2_when_the_device_cannot_be_brought_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A device that will not come up is a fixable, reportable condition, not a traceback.
    _stub_launch(monkeypatch, None, error="no booted device")
    result = runner.invoke(
        app, ["repl", "--target", "demo", "--config", str(_fake_config(tmp_path))]
    )
    assert result.exit_code == 2
    assert "no booted device" in result.output


class _ClosingEnvironment:
    """Records whether `_close_owned_session` reached this environment's `teardown`."""

    def __init__(self) -> None:
        self.closed = 0

    def teardown(self, driver: base.Driver, eff: object) -> None:  # Environment shape
        self.closed += 1


def _patched_env(monkeypatch: pytest.MonkeyPatch, env: Environment) -> _ClosingEnvironment:
    recorder = _ClosingEnvironment()
    monkeypatch.setattr(env, "teardown", recorder.teardown)
    return recorder


@pytest.mark.parametrize(
    "env",
    [
        WebEnvironment("playwright"),
        XcuitestLiveEnvironment("xcuitest", "https://grid.example/wd/hub"),
    ],
    ids=["web-browser", "live-webdriver-session"],
)
def test_leaving_the_shell_closes_a_session_this_process_owns(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A browser and a grid-reserved WebDriver session would both outlive the command otherwise —
    # the second stays reserved until the grid expires it.
    recorder = _patched_env(monkeypatch, env)
    repl_cli._close_owned_session(env, FakeDriver(), _EFF)
    assert recorder.closed == 1


def test_leaving_the_shell_leaves_a_device_backed_app_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `FakeEnvironment` inherits the device-style teardown that terminates the app; the operator
    # expects the Simulator still showing the screen they were inspecting, so it must not run.
    env = FakeEnvironment("fake", "FAKE-UDID")
    recorder = _patched_env(monkeypatch, env)
    repl_cli._close_owned_session(env, FakeDriver(), _EFF)
    assert recorder.closed == 0


def test_a_teardown_failure_on_a_clean_exit_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A `finally` block that lets its own failure escape would replace whatever exception the shell
    # exited with — here, none at all (a clean `exit`) — so the teardown failure must surface as an
    # ordinary message, not a crash.
    driver = FakeDriver()
    _stub_launch(monkeypatch, driver)
    env = WebEnvironment("playwright")
    monkeypatch.setattr(repl_cli, "environment_for", lambda *a, **k: env)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("browser already closed")

    monkeypatch.setattr(env, "teardown", boom)
    result = runner.invoke(
        app, ["repl", "--target", "demo", "--config", str(_fake_config(tmp_path))], input="exit\n"
    )
    assert result.exit_code == 0
    assert "warning: closing the session failed: browser already closed" in result.output


def test_a_teardown_failure_never_masks_a_real_bug_from_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The loop's own contract is that a genuine defect propagates rather than being read as a bad
    # id (test_a_bug_in_a_command_is_not_swallowed_by_the_loop); a teardown hiccup on the way out
    # must not erase that exception in favor of its own.
    class _Broken(FakeDriver):
        def back(self) -> None:
            raise ZeroDivisionError("not an answer to the operator's question")

    _stub_launch(monkeypatch, _Broken())
    env = WebEnvironment("playwright")
    monkeypatch.setattr(repl_cli, "environment_for", lambda *a, **k: env)
    monkeypatch.setattr(
        env, "teardown", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("teardown boom"))
    )
    result = runner.invoke(
        app, ["repl", "--target", "demo", "--config", str(_fake_config(tmp_path))], input="back\n"
    )
    assert isinstance(result.exception, ZeroDivisionError)
