"""Structural guards for the in-app touch visualization (`bajutsu run --touch-markers`).

The visualization draws inside the app under test, so its one safety property is that it stays
invisible to the accessibility tree every selector resolves against. That property rests entirely on
*what kind of object* is drawn: a `CALayer` is not a `UIResponder` and conforms to no accessibility
protocol, so XCUITest's snapshot cannot see it, while a `UIView` would surface as an `.other`
element and could turn a scenario's unique selector into an ambiguous one — a prime-directive-2
regression, in any app, at any step.

Nothing in the Python gate can run UIKit, so these tests guard the premise rather than the runtime:
the drawing code must not reach for a view, and the on-device scenario pair that exercises the
visualization must stay paired. The runtime half is the `golden` job in `.github/workflows/ios-e2e.yml`.
"""

from __future__ import annotations

import re
from pathlib import Path

from bajutsu.scenario.load import load_scenarios

ROOT = Path(__file__).resolve().parent.parent
TOUCH_SWIFT = ROOT / "BajutsuKit/Sources/BajutsuKit/BajutsuTouch.swift"
GOLDEN_XCUITEST = ROOT / "demos/showcase/scenarios/golden/golden_xcuitest.yaml"
ACTIVATION_KEY = "BAJUTSU_TOUCH_MARKERS"


def test_the_marker_is_drawn_as_a_layer() -> None:
    source = TOUCH_SWIFT.read_text(encoding="utf-8")
    assert "CAShapeLayer()" in source, "the marker must be drawn as a layer"
    assert "window.layer.addSublayer" in source, "the layer belongs to the window's layer"


def test_the_marker_never_becomes_a_view() -> None:
    """A `UIView` here would enter the accessibility tree and could break selector resolution."""
    source = TOUCH_SWIFT.read_text(encoding="utf-8")
    # Only constructor-shaped uses matter: the doc comment explains *why* a view is wrong, so a bare
    # substring search for "UIView" would flag the explanation itself.
    # `UIWindow` is a `UIView` subclass and is the rejected alternative by name — an overlay window
    # adds a top-level node to the tree — so it is an offender here too, as is any insert-shaped
    # sibling of `addSubview`.
    offenders = (
        re.findall(r"\bUIView\s*\(", source)
        + re.findall(r"\bUIWindow\s*\(", source)
        + re.findall(r"\baddSubview\b", source)
        + re.findall(r"\binsertSubview\b", source)
    )
    assert not offenders, f"the touch visualization must not build views: {offenders}"


def test_the_activation_key_is_off_unless_set_to_one() -> None:
    """The hook installs on `1` alone, so an app never given the key behaves as it does today."""
    source = TOUCH_SWIFT.read_text(encoding="utf-8")
    assert ACTIVATION_KEY in source, "the Swift side must read the same key the CLI writes"
    # Tolerant of formatting on purpose: pinning the exact spelling would turn a `Self.` qualifier
    # or a re-wrapped `guard` into a red suite for a refactor that changed no behaviour.
    assert re.search(r'environment\[\s*(?:Self\.)?activationKey\s*\]\s*==\s*"1"', source), (
        "the hook must install on the literal value '1' and nothing else"
    )


def test_the_golden_pair_stays_paired() -> None:
    """One scenario asserts the tree with the markers on, its twin with them off, same baseline.

    The pairing is the whole check: a scenario that stopped setting the launch env, or that drifted
    onto a different golden, would leave the visualization asserting nothing while still passing.
    """
    scenarios = load_scenarios(GOLDEN_XCUITEST.read_text(encoding="utf-8"))
    settings = [s.preconditions.launch_env.get(ACTIVATION_KEY) for s in scenarios]
    assert settings.count("1") == 1, "exactly one scenario runs with the visualization on"
    # The off twin must pin "0" rather than leave the key unset: the iOS lanes pass
    # `--touch-markers`, which fills the key in on any scenario that does not carry one, and an
    # unpinned twin would quietly become a second marked run — leaving the pair looking paired while
    # comparing nothing.
    assert settings.count("0") == 1, "its twin must pin the visualization off, not merely omit it"

    goldens = {a.golden.path for s in scenarios for a in s.expect if a.golden}
    assert len(goldens) == 1, f"both scenarios must assert one baseline, got {goldens}"


def test_the_marker_scenario_actually_touches() -> None:
    """A scenario that touches nothing draws no marker, so it would prove nothing."""
    scenarios = load_scenarios(GOLDEN_XCUITEST.read_text(encoding="utf-8"))
    marked = next(s for s in scenarios if ACTIVATION_KEY in s.preconditions.launch_env)
    assert any(step.tap is not None for step in marked.steps), "the scenario must perform a tap"
