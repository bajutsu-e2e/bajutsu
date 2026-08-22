"""Shared support for the system-alert guard's real-model verification (BE-0308).

The guard (`bajutsu/agents/alerts.py`) exists to stop a live AI operation from acting blindly into
an unexpected system dialog. Every other test of it hands it a `AlertDecision` its own author typed,
so it proves the *plumbing* and nothing about the guard's safety claim: that a real vision call,
looking at a real alert, lands on the correct dismiss control rather than on a destructive button
beside it. This module holds what both halves of that check need — the on-device capture harness's
writer, the replay reader, and the one assertion that decides whether the guard got it right — so
`test_alert_fixtures_ondevice.py` (capture) and `test_real_model_alerts.py` (verify) cannot drift on
what "correct" means.

What is asserted is the locator's own answer: its normalized coordinates mapped onto the screen the
fixture was captured against, checked for containment in the correct control's frame. That is the
question BE-0308 asks — is a real model's *decision* right — and it is deliberately narrower than
"does the guard clear the prompt on-device", which depends on the guard's actuation path rather than
on its vision. `FixtureDriver` and `guard_tap_point` below serve one deterministic test that pins
this mapping against `SystemAlertGuard`'s own arithmetic, so the fixture's contract and the product
cannot drift apart unnoticed.

Nothing here runs a model; the live callers pass a real locator in. No LLM ever touches the
`run` / CI verdict (prime directive 1) — this is the AI *authoring* surface alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bajutsu.agents.alerts import AlertDecision, AlertLocator, SystemAlertGuard
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.screenshots import png_size

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "be0308"

# The fixture schema's version, written into every file and checked on load. A capture whose ground
# truth means something different from what the assertion expects must fail loudly rather than be
# replayed under the new reading — the fixtures are committed artifacts that outlive the code that
# wrote them.
SCHEMA_VERSION = 1

# How far a capture's screenshot aspect ratio may sit from its recorded screen's before the
# fixture is refused. The two describe the same rectangle at different scales, so they agree up to
# the downscale's integer rounding (the committed captures land within 0.03%). A wider gap means
# the screenshot and the ground-truth frames are no longer the same screen — an orientation
# change, a JSON edited without its PNG — and every mapped coordinate would then be wrong.
_ASPECT_TOLERANCE = 0.01


@dataclass(frozen=True)
class Control:
    """One button on the captured dialog, as the device's own accessibility tree reported it."""

    label: str
    frame: base.Frame  # the product's own (x, y, width, height) shape, in device points

    def __post_init__(self) -> None:
        """Refuse a control with no area, whichever path built it.

        Enforced on the type rather than in the reader or the writer, so a fixture assembled
        directly — as the verification's own synthetic fixtures are — cannot skip the check.
        `contains` on an empty frame would answer for a button that occupies nothing.
        """
        if self.frame[2] <= 0 or self.frame[3] <= 0:
            raise ValueError(f"control {self.label!r} has an empty frame {self.frame!r}")

    def contains(self, point: base.Point) -> bool:
        """Whether *point* (device points) falls on this button, edges included."""
        x, y, w, h = self.frame
        return x <= point[0] <= x + w and y <= point[1] <= y + h


@dataclass(frozen=True)
class AlertFixture:
    """A real dialog captured from a real device, with machine-derived ground truth.

    `dismiss` is the one control the guard is required to tap, and is None exactly when the dialog
    is not an OS prompt at all — the app's own dialog, which the guard must leave alone. `others`
    holds every control on the same dialog that a correct answer must *not* hit, so a failure can
    name the button the guard reached for instead of only reporting a coordinate.
    """

    name: str
    png: bytes
    screen: tuple[float, float]  # the device's screen bounds in points
    dismiss: Control | None
    others: tuple[Control, ...]
    note: str

    def __post_init__(self) -> None:
        """Refuse a capture that cannot mean what a replay would read it to mean.

        On the type, for the same reason `Control` validates itself: the reader, the writer, and the
        verification's own synthetic fixtures all construct one, so a check living in any single
        function would leave the other paths unguarded. Each rejection below is a capture failure
        rather than a fixture — a broken capture that loaded quietly would be measured as ground
        truth, which is the one outcome a committed artifact must never reach.
        """
        width, height = self.screen
        if not self.png:
            raise ValueError(f"fixture {self.name!r} has no screenshot")
        if width <= 0 or height <= 0:
            raise ValueError(f"fixture {self.name!r} has degenerate screen bounds {self.screen!r}")
        pixels = png_size(self.png)
        if pixels[1] <= 0:
            raise ValueError(f"fixture {self.name!r} has an unreadable screenshot header")
        skew = abs(pixels[0] / pixels[1] - width / height) / (width / height)
        if skew > _ASPECT_TOLERANCE:
            raise ValueError(
                f"fixture {self.name!r} screenshot is {pixels[0]}x{pixels[1]} pixels, whose aspect "
                f"ratio is {skew:.1%} away from the recorded screen {self.screen!r} — the two are "
                "not the same screen, so no answer could be mapped onto the recorded frames"
            )
        for control in self.controls:
            x, y, w, h = control.frame
            if x < 0 or y < 0 or x + w > width or y + h > height:
                raise ValueError(
                    f"fixture {self.name!r}: control {control.label!r} frame {control.frame!r} "
                    f"falls outside the captured screen {self.screen!r}"
                )

    @property
    def present(self) -> bool:
        """Whether a genuine OS-level prompt is on screen — i.e. whether the guard must act."""
        return self.dismiss is not None

    @property
    def controls(self) -> tuple[Control, ...]:
        """Every button captured on this dialog, the required one (if any) first."""
        return (*([self.dismiss] if self.dismiss is not None else []), *self.others)

    def control_at(self, point: base.Point) -> Control | None:
        """The captured control *point* lands on, or None when it hits no recorded button."""
        return next((c for c in self.controls if c.contains(point)), None)


# --- The replay driver: one fixture, served as the guard's own inputs ----------------------------


class FixtureDriver(FakeDriver):
    """Serves one captured fixture to the guard: its real PNG, over a tree that is one bare window.

    The window carries the captured screen's bounds, so `screen_size_from_elements` — the scale
    `SystemAlertGuard.dismiss` multiplies a normalized answer back up by — is that screen, and the
    point the guard computes is directly comparable with the recorded control frames. This is what lets one
    deterministic test pin the fixture's own mapping against the guard's arithmetic. It is not a
    claim about what a real device's tree looks like beside a prompt: measured against the showcase
    app on iOS 26, the app tree stays fully readable there and its bounding box overshoots the
    screen, which is why the fixture records a viewport instead (BE-0326).
    """

    def __init__(self, fixture: AlertFixture) -> None:
        width, height = fixture.screen
        super().__init__(
            screen=[
                {
                    "identifier": None,
                    "label": fixture.name,
                    "traits": ["application"],
                    "value": None,
                    "frame": (0.0, 0.0, width, height),
                    "nativeZ": None,
                }
            ]
        )
        self._png = fixture.png

    def screenshot(self, path: str) -> None:
        Path(path).write_bytes(self._png)
        self.actions.append(("screenshot", path))


def guard_tap_point(fixture: AlertFixture, locator: AlertLocator) -> base.Point | None:
    """Where the real guard taps when shown *fixture*, or None when it taps nothing.

    Drives `SystemAlertGuard.dismiss` itself — screenshot, locate, normalize, tap — so the point
    returned is the product's own, not this module's idea of it.
    """
    driver = FixtureDriver(fixture)
    SystemAlertGuard(locator).dismiss(driver)
    taps = [arg for kind, arg in driver.actions if kind == "tap_point"]
    if not taps:
        return None
    point = taps[-1]
    assert isinstance(point, tuple)  # FakeDriver records tap_point as the (x, y) it was given
    return (float(point[0]), float(point[1]))


def located_point(fixture: AlertFixture, decision: AlertDecision) -> base.Point | None:
    """Where *decision* points on the captured screen, or None when it reports no prompt.

    The decision's coordinates are image-normalized, so multiplying by the captured screen's bounds
    maps them into the same point space as the recorded control frames regardless of the
    screenshot's pixel scale — the mapping `SystemAlertGuard.dismiss` performs, pinned against the
    real thing by `test_real_model_alerts.py`.
    """
    if not decision.present:
        return None
    return (decision.x * fixture.screen[0], decision.y * fixture.screen[1])


def assert_locates_dismiss_control(fixture: AlertFixture, locator: AlertLocator) -> None:
    """*locator* answered *fixture* correctly: the dismiss control's frame, or "no prompt".

    For a captured OS prompt that means the returned coordinates land inside the recorded dismiss
    control; for the app's own dialog it means the locator reported nothing present, since the guard
    must stay out of a dialog the app owns. Both failures name the button the answer points at, so a
    wrong answer reads as "points at 'Delete'" rather than as a pair of coordinates.
    """
    point = located_point(fixture, locator.locate(fixture.png, None))
    if fixture.dismiss is None:
        if point is None:
            return
        raise AssertionError(
            f"{fixture.name}: the locator reported a prompt at {point}, pointing at "
            f"{_describe(fixture.control_at(point))} — this dialog is the app's own, not an OS "
            f"prompt, so the guard must report it absent and leave it alone ({fixture.note})"
        )
    if point is None:
        raise AssertionError(
            f"{fixture.name}: the locator reported no prompt — the captured screen shows an OS "
            f"prompt whose {fixture.dismiss.label!r} button it was required to find ({fixture.note})"
        )
    if not fixture.dismiss.contains(point):
        raise AssertionError(
            f"{fixture.name}: the locator points at {_describe(fixture.control_at(point))} at "
            f"{point}, not {fixture.dismiss.label!r} at {fixture.dismiss.frame} ({fixture.note})"
        )


def _describe(control: Control | None) -> str:
    return repr(control.label) if control is not None else "no recorded button"


# --- Fixture persistence ------------------------------------------------------------------------


def _control_payload(control: Control) -> dict[str, Any]:
    return {"label": control.label, "frame": list(control.frame)}


def _control_of(payload: dict[str, Any], where: str) -> Control:
    """One serialized control, rebuilt — `Control` itself refuses an empty frame."""
    frame = payload["frame"]
    if len(frame) != 4:
        raise ValueError(f"{where}: a control frame must be [x, y, width, height], got {frame!r}")
    return Control(
        label=str(payload["label"]),
        frame=(float(frame[0]), float(frame[1]), float(frame[2]), float(frame[3])),
    )


def save_fixture(
    name: str,
    png: bytes,
    *,
    screen: tuple[float, float],
    dismiss: Control | None,
    others: tuple[Control, ...],
    note: str,
    directory: Path | None = None,
) -> Path:
    """Persist a captured dialog as `<name>.png` plus its ground-truth `<name>.json`.

    Builds the `AlertFixture` first and writes only if it constructs, so the writer enforces exactly
    the invariants a later read enforces — nothing lands on disk that `load_fixture` would then
    refuse, and nothing lands that it would accept while meaning something else.

    Returns:
        The path of the JSON file written.

    Raises:
        ValueError: If the capture violates any of `AlertFixture`'s invariants — an empty
            screenshot, degenerate screen bounds, a screenshot that is not the recorded screen, or a
            control outside it.
    """
    fixture = AlertFixture(
        name=name, png=png, screen=screen, dismiss=dismiss, others=others, note=note
    )
    target = directory if directory is not None else FIXTURES_DIR
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{name}.png").write_bytes(png)
    payload = {
        "schema": SCHEMA_VERSION,
        "screen": [fixture.screen[0], fixture.screen[1]],
        "dismiss": _control_payload(dismiss) if dismiss is not None else None,
        "others": [_control_payload(c) for c in others],
        "note": note,
    }
    json_path = target / f"{name}.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return json_path


def load_fixture(name: str, directory: Path | None = None) -> AlertFixture:
    """Read a committed fixture back, refusing anything the assertion could misread.

    The schema version is checked here rather than on the type, being a property of the serialized
    form; every other invariant belongs to `AlertFixture` and is enforced by constructing one, so a
    committed file edited by hand or mangled by a merge fails on the next read rather than being
    replayed as ground truth.

    Raises:
        ValueError: If the schema version is unknown, the screenshot is missing, or the payload
            violates any `AlertFixture` invariant.
    """
    target = directory if directory is not None else FIXTURES_DIR
    payload = json.loads((target / f"{name}.json").read_text(encoding="utf-8"))
    schema = payload.get("schema")
    if schema != SCHEMA_VERSION:
        raise ValueError(
            f"fixture {name!r} declares schema {schema!r}, not {SCHEMA_VERSION} — its ground truth "
            "may mean something else; re-capture it rather than replaying it"
        )
    png_path = target / f"{name}.png"
    screen = payload["screen"]
    if len(screen) != 2:
        raise ValueError(f"fixture {name!r} screen must be [width, height], got {screen!r}")
    raw_dismiss = payload.get("dismiss")
    return AlertFixture(
        name=name,
        png=png_path.read_bytes() if png_path.exists() else b"",
        screen=(float(screen[0]), float(screen[1])),
        dismiss=_control_of(raw_dismiss, f"fixture {name!r}") if raw_dismiss is not None else None,
        others=tuple(_control_of(c, f"fixture {name!r}") for c in payload.get("others", [])),
        note=str(payload.get("note", "")),
    )


def committed_fixtures(directory: Path | None = None) -> list[str]:
    """The names of every committed fixture, sorted — empty until a capture lands (signal-first)."""
    target = directory if directory is not None else FIXTURES_DIR
    if not target.is_dir():
        return []
    return sorted(p.stem for p in target.glob("*.json"))
