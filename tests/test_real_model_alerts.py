"""Real-model verification of the system-alert guard's accuracy (BE-0308).

`tests/test_alerts.py` proves the guard plumbs whatever `AlertDecision` it receives through to a
tap — a real check of the wiring, and no check at all of the guard's safety claim: that a real
vision call, looking at a real alert on a real screen, lands on the correct dismiss control rather
than on a destructive button beside it. Nothing in the suite ever asked a real model to look at a
real alert, so a wrong real answer — the guard failing at the one thing it exists to prevent —
would pass unnoticed. These tests close that gap over the dialogs captured from the showcase app by
`test_alert_fixtures_ondevice.py`.

Three layers, following BE-0295's shape:

- **Deterministic assertion self-checks** (no credential, always run): drive the real guard with
  stub locators aimed at each captured control and prove the assertion *accepts* a tap on the
  dismiss control and *rejects* one on the destructive button beside it. Without this a live run
  could pass vacuously — an assertion that accepts everything says nothing about a real model.
- **Committed-fixture well-formedness checks** (no credential): every committed fixture must load
  (which is what enforces its invariants, the screenshot-matches-recorded-screen cross-check
  included) and must carry geometry the assertion can actually discriminate on. These catch a
  fixture mangled by a hand edit or a merge; they cannot catch one whose recorded dismiss control
  names the wrong button, since only looking at the screenshot can tell — that is the live layer's
  job, not the gate's.
- **Key-gated live verification** (real model): show each committed dialog to the real vision path
  and assert the point the guard taps lands inside the correct control.

The live layer is signal-first, not a gate (the BE-0282 precedent): it skips whenever no AI
credential is configured, so the deterministic gate stays hermetic, and it needs no Simulator — the
dialogs are committed captures, and only the model call is live. No LLM ever touches the `run` / CI
verdict (prime directive 1): the guard is a Tier 1 live-AI operation and stays one.
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from pathlib import Path

import pytest
from alert_fixture_support import (
    AlertFixture,
    Control,
    FixtureDriver,
    assert_locates_dismiss_control,
    committed_fixtures,
    guard_tap_point,
    load_fixture,
    located_point,
    save_fixture,
)
from real_model_support import requires_credential

from bajutsu.agents.alerts import AlertDecision, ClaudeAlertLocator
from bajutsu.ai import create_backend
from bajutsu.elements import screen_size_from_elements

_SCREEN = (402.0, 874.0)
_DISMISS = Control(label="Don't Allow", frame=(40.0, 500.0, 150.0, 44.0))
_ALLOW = Control(label="Allow", frame=(212.0, 500.0, 150.0, 44.0))
_DELETE = Control(label="Delete", frame=(100.0, 460.0, 200.0, 44.0))
_CANCEL = Control(label="Cancel", frame=(100.0, 520.0, 200.0, 44.0))


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload))
    )


def _png(width: int, height: int) -> bytes:
    """A blank but genuinely decodable 8-bit grayscale PNG of the given pixel size.

    Decodable, not just a plausible IHDR: `screenshot_bytes` runs every capture through
    `downscale_png`, which opens the image before it compares the long edge against the cap. A
    header-only PNG would make that call raise and fall back to the input bytes, so a test asserting
    the bytes came through unchanged would be passing on the fallback rather than on the
    already-within-the-cap path it means to exercise.
    """
    ihdr = struct.pack(">II", width, height) + bytes((8, 0, 0, 0, 0))
    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 1))
        + _chunk(b"IEND", b"")
    )


# A screenshot the size a committed capture really is: the iPhone 17's 402x874 points at @3x, then
# downscaled by `screenshot_bytes` to a 1568 long edge (BE-0193). Sized under that cap on purpose —
# `downscale_png` returns an image already within it byte for byte, which is what lets the replay
# assert the guard handed the model exactly these bytes. `AlertFixture` refuses a screenshot whose
# aspect ratio is not the recorded screen's, so the synthetic fixtures must agree too (0.03% here).
_SCREEN_PNG = _png(721, 1568)


def _prompt_fixture() -> AlertFixture:
    """A synthetic OS prompt: the dismiss button, with a grant button beside it."""
    return AlertFixture(
        name="synthetic-prompt",
        png=_SCREEN_PNG,
        screen=_SCREEN,
        dismiss=_DISMISS,
        others=(_ALLOW,),
        note="synthetic",
    )


def _app_dialog_fixture() -> AlertFixture:
    """A synthetic app-owned dialog: nothing to dismiss, and a destructive button to avoid."""
    return AlertFixture(
        name="synthetic-app-dialog",
        png=_SCREEN_PNG,
        screen=_SCREEN,
        dismiss=None,
        others=(_DELETE, _CANCEL),
        note="synthetic",
    )


class _AimedLocator:
    """A locator that reports a prompt at the center of one captured control.

    Normalized against the fixture's own screen, since that is the divisor the guard multiplies
    back by — a locator aimed with one screen's bounds and replayed through another's would land
    somewhere neither meant.
    """

    def __init__(self, control: Control, screen: tuple[float, float]) -> None:
        x, y, w, h = control.frame
        self._decision = AlertDecision(
            present=True,
            x=(x + w / 2) / screen[0],
            y=(y + h / 2) / screen[1],
            label=control.label,
        )

    def locate(self, screenshot_png: bytes, instruction: str | None) -> AlertDecision:
        return self._decision


def _aimed_at(fixture: AlertFixture, control: Control) -> _AimedLocator:
    """A locator aimed at *control*, normalized against *fixture*'s captured screen."""
    return _AimedLocator(control, fixture.screen)


class _AbsentLocator:
    """A locator that reports no prompt at all."""

    def locate(self, screenshot_png: bytes, instruction: str | None) -> AlertDecision:
        return AlertDecision(present=False)


# --- The containment predicate the whole verdict rests on ----------------------------------------


def test_control_rejects_an_empty_frame() -> None:
    # A zero-area button would make `contains` answer for a control that occupies nothing.
    with pytest.raises(ValueError, match="empty frame"):
        Control(label="flat", frame=(10.0, 20.0, 0.0, 44.0))


def test_control_contains_includes_its_edges() -> None:
    # Every verdict is this one predicate, and a real answer can land on a button's edge, so pin
    # all four comparisons rather than only the comfortable centre case.
    control = Control(label="x", frame=(10.0, 20.0, 30.0, 40.0))
    assert control.contains((10.0, 20.0))  # top-left corner
    assert control.contains((40.0, 60.0))  # bottom-right corner
    assert control.contains((25.0, 40.0))  # centre
    assert not control.contains((9.9, 40.0))
    assert not control.contains((40.1, 40.0))
    assert not control.contains((25.0, 19.9))
    assert not control.contains((25.0, 60.1))


def test_control_at_names_the_dismiss_control_when_frames_overlap() -> None:
    # `controls` puts the required control first so an ambiguous point resolves to it. Every failure
    # message names whatever `control_at` returns, so a regression here would misreport which
    # button a wrong answer reached for — in exactly the reports that matter most.
    overlapping = Control(label="Allow", frame=_DISMISS.frame)
    fixture = AlertFixture(
        name="overlap",
        png=_SCREEN_PNG,
        screen=_SCREEN,
        dismiss=_DISMISS,
        others=(overlapping,),
        note="synthetic",
    )
    centre = (_DISMISS.frame[0] + 1.0, _DISMISS.frame[1] + 1.0)
    hit = fixture.control_at(centre)
    assert hit is not None and hit.label == _DISMISS.label


# --- Deterministic assertion self-checks (no model; always run) ----------------------------------
# The live layer below is only as meaningful as this assertion's ability to fail, so pin both
# directions: an answer on the required control passes, and every wrong answer is caught and named.


def test_accepts_an_answer_on_the_dismiss_control() -> None:
    fixture = _prompt_fixture()
    assert_locates_dismiss_control(fixture, _aimed_at(fixture, _DISMISS))


def test_rejects_an_answer_on_the_button_beside_the_dismiss_control() -> None:
    fixture = _prompt_fixture()
    with pytest.raises(AssertionError, match="'Allow'"):
        assert_locates_dismiss_control(fixture, _aimed_at(fixture, _ALLOW))


def test_rejects_a_locator_that_misses_a_real_prompt() -> None:
    with pytest.raises(AssertionError, match="reported no prompt"):
        assert_locates_dismiss_control(_prompt_fixture(), _AbsentLocator())


def test_rejects_an_answer_inside_the_apps_own_destructive_dialog() -> None:
    # The failure names the destructive button, so the report says what the answer reached for
    # rather than only where — the whole point of recording the wrong controls too.
    fixture = _app_dialog_fixture()
    with pytest.raises(AssertionError, match="'Delete'"):
        assert_locates_dismiss_control(fixture, _aimed_at(fixture, _DELETE))


def test_accepts_leaving_the_apps_own_dialog_alone() -> None:
    assert_locates_dismiss_control(_app_dialog_fixture(), _AbsentLocator())


def test_the_fixture_mapping_agrees_with_the_guards_own_arithmetic() -> None:
    # The assertion maps a normalized answer onto the fixture's screen itself, so pin that mapping
    # against `SystemAlertGuard.dismiss`'s: over a tree that is one window of the captured screen,
    # the point the guard computes and the point the fixture maps to must be the same one. Were they
    # to drift, the verification would be measuring its own arithmetic rather than the product's.
    fixture = _prompt_fixture()
    for control in fixture.controls:
        locator = _aimed_at(fixture, control)
        mapped = located_point(fixture, locator.locate(fixture.png, None))
        assert mapped == guard_tap_point(fixture, locator), control.label


def test_the_replay_driver_serves_the_captured_screenshot_and_screen() -> None:
    # The guard divides the model's pixel answer by the PNG's size and multiplies by the tree's, so
    # both must come from the same capture or every mapped point is wrong.
    fixture = _prompt_fixture()
    assert screen_size_from_elements(FixtureDriver(fixture).query()) == _SCREEN
    seen: list[bytes] = []

    class _Recording:
        def locate(self, screenshot_png: bytes, instruction: str | None) -> AlertDecision:
            seen.append(screenshot_png)
            return AlertDecision(present=False)

    assert guard_tap_point(fixture, _Recording()) is None
    assert seen == [fixture.png]


# --- Fixture persistence: a broken capture must never land as a passing replay --------------------


def _write_raw(tmp_path: Path, name: str, payload: dict[str, object], *, png: bytes) -> None:
    """A fixture pair written straight to disk, bypassing `save_fixture`'s own validation.

    The point of every load test below is a file `save_fixture` would have refused — a hand edit, a
    mangled merge — so they cannot go through the writer to produce one.
    """
    if png:
        (tmp_path / f"{name}.png").write_bytes(png)
    (tmp_path / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_fixture_round_trips_through_disk(tmp_path: Path) -> None:
    fixture = _prompt_fixture()
    save_fixture(
        fixture.name,
        fixture.png,
        screen=fixture.screen,
        dismiss=fixture.dismiss,
        others=fixture.others,
        note="a round-trip",
        directory=tmp_path,
    )
    reloaded = load_fixture(fixture.name, tmp_path)
    assert reloaded.png == fixture.png
    assert reloaded.screen == fixture.screen
    assert reloaded.dismiss == _DISMISS
    assert reloaded.others == (_ALLOW,)
    assert reloaded.present is True
    assert reloaded.controls == (_DISMISS, _ALLOW)
    assert_locates_dismiss_control(reloaded, _aimed_at(reloaded, _DISMISS))


def _save(tmp_path: Path, **overrides: object) -> Path:
    kwargs: dict[str, object] = {
        "name": "broken",
        "png": _SCREEN_PNG,
        "screen": _SCREEN,
        "dismiss": _DISMISS,
        "others": (),
        "note": "",
        "directory": tmp_path,
    }
    kwargs.update(overrides)
    name = kwargs.pop("name")
    png = kwargs.pop("png")
    return save_fixture(name, png, **kwargs)  # type: ignore[arg-type]


def test_save_rejects_an_empty_screenshot(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no screenshot"):
        _save(tmp_path, png=b"")


def test_save_rejects_degenerate_screen_bounds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="degenerate screen"):
        _save(tmp_path, screen=(0.0, 0.0), dismiss=None)


def test_save_rejects_a_screenshot_that_is_not_the_recorded_screen(tmp_path: Path) -> None:
    # The locator normalizes against the screenshot's pixels while the recorded frames live in the
    # screen's points, so the two must describe one rectangle. A landscape capture beside a portrait
    # screen — an orientation change, or a JSON updated without its PNG — maps every answer wrong.
    with pytest.raises(ValueError, match="not the same screen"):
        _save(tmp_path, png=_png(1568, 721))


@pytest.mark.parametrize(
    "frame",
    [
        pytest.param((_SCREEN[0] - 10.0, 100.0, 44.0, 44.0), id="past-the-right-edge"),
        pytest.param((100.0, _SCREEN[1] - 10.0, 44.0, 44.0), id="past-the-bottom-edge"),
        pytest.param((-1.0, 100.0, 44.0, 44.0), id="left-of-the-screen"),
        pytest.param((100.0, -1.0, 44.0, 44.0), id="above-the-screen"),
    ],
)
def test_save_rejects_a_control_outside_the_captured_screen(
    tmp_path: Path, frame: tuple[float, float, float, float]
) -> None:
    # A frame read in a different coordinate space than the screen's would look like this, and would
    # make every replayed comparison meaningless — so it fails at capture time, not at replay.
    with pytest.raises(ValueError, match="outside the captured screen"):
        _save(tmp_path, dismiss=Control(label="off", frame=frame))


def test_save_accepts_a_control_flush_against_the_screen_edge(tmp_path: Path) -> None:
    # The bound is inclusive: a button whose frame ends exactly at the screen edge is on the screen.
    flush = Control(label="flush", frame=(0.0, 0.0, _SCREEN[0], _SCREEN[1]))
    assert _save(tmp_path, name="flush", dismiss=flush).exists()


def test_load_rejects_an_unknown_schema(tmp_path: Path) -> None:
    _write_raw(tmp_path, "stale", {"schema": 99, "screen": list(_SCREEN)}, png=_SCREEN_PNG)
    with pytest.raises(ValueError, match="schema"):
        load_fixture("stale", tmp_path)


def test_load_rejects_a_missing_screenshot(tmp_path: Path) -> None:
    _write_raw(
        tmp_path,
        "shotless",
        {"schema": 1, "screen": list(_SCREEN), "dismiss": None, "others": []},
        png=b"",
    )
    with pytest.raises(ValueError, match="no screenshot"):
        load_fixture("shotless", tmp_path)


def test_load_rejects_degenerate_screen_bounds(tmp_path: Path) -> None:
    _write_raw(
        tmp_path,
        "flat",
        {"schema": 1, "screen": [0, 874], "dismiss": None, "others": []},
        png=_SCREEN_PNG,
    )
    with pytest.raises(ValueError, match="degenerate screen"):
        load_fixture("flat", tmp_path)


def test_load_rejects_a_malformed_control_frame(tmp_path: Path) -> None:
    _write_raw(
        tmp_path,
        "short",
        {
            "schema": 1,
            "screen": list(_SCREEN),
            "dismiss": {"label": "Don't Allow", "frame": [40, 500, 150]},
            "others": [],
        },
        png=_SCREEN_PNG,
    )
    with pytest.raises(ValueError, match=r"\[x, y, width, height\]"):
        load_fixture("short", tmp_path)


def test_load_rejects_an_empty_control_frame(tmp_path: Path) -> None:
    _write_raw(
        tmp_path,
        "flatbutton",
        {
            "schema": 1,
            "screen": list(_SCREEN),
            "dismiss": {"label": "Don't Allow", "frame": [40, 500, 150, 0]},
            "others": [],
        },
        png=_SCREEN_PNG,
    )
    with pytest.raises(ValueError, match="empty frame"):
        load_fixture("flatbutton", tmp_path)


def test_load_rejects_a_control_outside_the_captured_screen(tmp_path: Path) -> None:
    # The reader must refuse what the writer refuses. A committed fixture edited by hand, or mangled
    # by a merge, is read far more often than it is written — so the read path is where a corrupted
    # frame has to fail, not only the write path that produced the file once.
    _write_raw(
        tmp_path,
        "escaped",
        {
            "schema": 1,
            "screen": list(_SCREEN),
            "dismiss": {"label": "Don't Allow", "frame": [40, 850, 150, 48]},
            "others": [],
        },
        png=_SCREEN_PNG,
    )
    with pytest.raises(ValueError, match="outside the captured screen"):
        load_fixture("escaped", tmp_path)


def test_committed_fixtures_is_empty_when_the_directory_is_absent(tmp_path: Path) -> None:
    # The parametrization below degrades to a visible skip off this, so it must not raise.
    assert committed_fixtures(tmp_path / "not-captured-yet") == []


# --- Committed-fixture ground-truth checks (no credential: run on every gate) ---------------------

_NAMES: list[object] = committed_fixtures() or [
    pytest.param("", marks=pytest.mark.skip(reason="no captured alert fixture yet (BE-0308)"))
]


@pytest.mark.parametrize("name", _NAMES)
def test_committed_fixture_loads_and_records_distinct_buttons(name: str) -> None:
    # Loading is the check: `AlertFixture`'s invariants — a readable screenshot whose aspect ratio
    # is the recorded screen's, every frame inside that screen, no empty frame — all run here. On
    # top of them, two properties of the recorded buttons that a mangled fixture would break:
    # every button carries a label, and no two of them are the same button.
    fixture = load_fixture(name)
    labels = [c.label for c in fixture.controls]
    assert all(labels), f"{name}: a recorded button has no label: {labels}"
    assert len(set(labels)) == len(labels), f"{name}: duplicate button labels {labels}"


@pytest.mark.parametrize("name", _NAMES)
def test_committed_fixture_geometry_discriminates_the_wrong_button(name: str) -> None:
    # Aiming at the recorded dismiss control and finding it inside itself proves nothing — a frame
    # always contains its own centre. What does carry information is the other direction: on this
    # real geometry, an answer on each *wrong* button must be rejected. A fixture whose frames
    # overlapped, or whose dismiss control had swallowed the others, would pass the assertion for a
    # wrong answer, and the live layer would then be unable to fail.
    fixture = load_fixture(name)
    wrong = fixture.others if fixture.dismiss is not None else fixture.controls
    assert wrong, f"{name}: records no wrong button, so the live check cannot discriminate"
    for control in wrong:
        with pytest.raises(AssertionError, match=re.escape(repr(control.label))):
            assert_locates_dismiss_control(fixture, _aimed_at(fixture, control))


# --- Key-gated live verification (real model) ----------------------------------------------------


@pytest.mark.live
@requires_credential
@pytest.mark.parametrize("name", _NAMES)
def test_real_model_locates_the_correct_dismiss_control(name: str) -> None:
    assert_locates_dismiss_control(load_fixture(name), ClaudeAlertLocator(backend=create_backend()))
