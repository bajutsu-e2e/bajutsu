"""Tests for scripts/assert_doctor_env.py — the E2E onboarding-gate assertion (BE-0304).

`bajutsu doctor` renders an ``environment:`` section whose every check is a ``✓`` (pass) or ``✗``
(fail) line; the iOS / Android / web E2E lanes run it for real and assert on that section. These
tests pin the parser that both the "the gate passes" (``ok``) and "the gate fails loudly" (``broken``)
lane cases lean on: it must isolate the ``environment:`` section from everything else doctor prints —
the ``capability preflight`` section (whose failure marker is the *different* ``✘``, U+2718), the
actuator-resolution disclosure, and the trailing optional ``Claude`` section — so a failure elsewhere
is never read as an environment failure, and vice versa.
"""

from __future__ import annotations

from scripts.assert_doctor_env import environment_section, section_has_failure

# A booted-Simulator run where every environment check passes. The capability-preflight and Claude
# sections bracket it to prove the parser stops at the right boundaries.
_HEALTHY = """\
capability preflight:
  ✘ [smoke] pinch not supported by adb

xcuitest runner: bundled

environment:
  ✓ target showcase-swiftui: bundleId set
  ✓ xcrun: /usr/bin/xcrun
  ✓ xcodebuild: Xcode 16.0
  ✓ Simulator booted: 1 booted

Claude (optional):
  ✓ reachable
"""

# The browserless / no-device host: the environment gate itself reports a ✗.
_BROKEN = """\
environment:
  ✓ playwright: installed
  ✗ chromium browser: `uv run playwright install chromium`

Claude (optional):
  – not configured (optional)
"""


def test_environment_section_isolated_from_surrounding_sections() -> None:
    section = environment_section(_HEALTHY)
    assert section is not None
    # Only the four indented check lines belong to the section — not the capability-preflight ✘
    # above it nor the Claude line below it.
    marks = [line.strip() for line in section if line.strip()]
    assert marks == [
        "✓ target showcase-swiftui: bundleId set",
        "✓ xcrun: /usr/bin/xcrun",
        "✓ xcodebuild: Xcode 16.0",
        "✓ Simulator booted: 1 booted",
    ]


def test_healthy_environment_has_no_failure() -> None:
    section = environment_section(_HEALTHY)
    assert section is not None
    assert section_has_failure(section) is False


def test_broken_environment_reports_a_failure() -> None:
    section = environment_section(_BROKEN)
    assert section is not None
    assert section_has_failure(section) is True


def test_capability_preflight_cross_marker_is_not_an_environment_failure() -> None:
    # The capability-preflight ✘ (U+2718) sits outside the environment section, so it must not be
    # read as an environment ✗ (U+2717) — the two markers are deliberately distinct.
    section = environment_section(_HEALTHY)
    assert section is not None
    assert not any("✘" in line for line in section)


def test_missing_environment_section_returns_none() -> None:
    # A config-error exit can print a different section and no `environment:` at all — the caller
    # must treat that as "could not verify", not as a silent pass.
    assert environment_section("scenario not found: foo.yaml\n") is None
