#!/usr/bin/env python3
"""Assert `bajutsu doctor`'s environment gate against a real E2E environment (BE-0304).

The onboarding gate — `provision.py`, `preflight.py`/`requirements.py`, and `simctl.py`'s JSON
parsers — is otherwise tested only through injected fakes, which are internally consistent with
today's behavior by construction and so can never observe drift from a real toolchain. The three E2E
lanes (ios-e2e.yml, android-e2e.yml, web-e2e.yml) run `bajutsu doctor` for real, once, against their
genuinely-real environment; this helper turns doctor's rendered `environment:` section into a
pass/fail the lane can gate on. It replaces the fragile inline `awk`/`grep` the web lane first shipped
with — a testable parser (tests/test_assert_doctor_env.py), the same "untested shell one-liner ->
unit-tested Python" move scripts/e2e_changes.py made.

Two modes:

- ``--expect ok`` (the provisioned lane): the section is present and carries no ``✗``. doctor's own
  exit code is ignored on purpose — with no app server reachable its *screen* probe faults and exits
  non-zero even when the environment gate (the one thing asserted here) passed.
- ``--expect broken`` (the deliberately-broken host): doctor exits non-zero AND the section carries a
  ``✗`` — the fail side no injected-fake test can prove, since a fake only ever reacts to a hand-fed
  boolean.

The ``✗`` this looks for is U+2717, the environment gate's failure marker (bajutsu/preflight.py).
It is deliberately distinct from the ``✘`` (U+2718) `capability preflight` uses, and the parser
scopes to the ``environment:`` section anyway, so a capability or scenario failure elsewhere in
doctor's output is never misread as an environment failure.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# The environment gate's failure marker (bajutsu/preflight.render). NOT the ✘ (U+2718) that
# `capability preflight` uses — kept separate so the two can never be confused.
_FAILURE_MARK = "✗"

# doctor prints the section under a bare `environment:` header at column 0, then one indented
# `  {✓|✗} name: detail` line per check (bajutsu/cli/commands/doctor.py).
_HEADER = "environment:"


def environment_section(output: str) -> list[str] | None:
    """The lines of doctor's ``environment:`` section, or ``None`` when it printed none.

    The section runs from the ``environment:`` header to the next non-indented line — the trailing
    optional ``Claude`` section, or end of output. Sections doctor prints *before* it (capability
    preflight, actuator resolution, the runner tier) are excluded, so their markers never count as
    environment checks. ``None`` (no header at all — e.g. a config-error exit) is distinct from an
    empty list and lets the caller fail rather than read absence as a pass.
    """
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if line == _HEADER:
            section: list[str] = []
            for rest in lines[i + 1 :]:
                # A non-empty, non-indented line opens the next section (e.g. `Claude (optional):`)
                # and closes this one; blank lines within stay part of it.
                if rest and not rest[0].isspace():
                    break
                section.append(rest)
            return section
    return None


def section_has_failure(section: list[str]) -> bool:
    """Whether any check line in the environment section is a ``✗`` (a failed environment check)."""
    return any(_FAILURE_MARK in line for line in section)


def _run_doctor(args: argparse.Namespace) -> subprocess.CompletedProcess[str]:
    """Run `bajutsu doctor --environment-only` via the module entry point, folding stderr into stdout.

    ``--environment-only`` stops doctor at the runnability gate, so the assertion is scoped to the
    environment section and never pays for (or flakes on) the screen probe — on iOS, the short-lived
    XCUITest runner. It also makes the exit code follow the gate directly, which the ``broken`` mode
    relies on.
    """
    cmd = [
        sys.executable,
        "-m",
        "bajutsu",
        "doctor",
        "--environment-only",
        "--target",
        args.target,
        "--config",
        args.config,
        "--udid",
        args.udid,
    ]
    # Empty `--backend` lets doctor fall back to the config's own backend (the web lane's convention),
    # so pass it through only when a lane names one explicitly (iOS: xcuitest, Android: android).
    if args.backend:
        cmd += ["--backend", args.backend]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--backend", default="")
    parser.add_argument("--config", required=True)
    parser.add_argument("--udid", default="booted")
    parser.add_argument("--expect", choices=("ok", "broken"), required=True)
    args = parser.parse_args()

    proc = _run_doctor(args)
    output = proc.stdout + proc.stderr
    # Echo doctor's own output so the CI log shows exactly what the gate saw.
    print(output, end="" if output.endswith("\n") else "\n")

    section = environment_section(output)
    if section is None:
        print("::error::doctor printed no environment section")
        return 1
    failed = section_has_failure(section)

    if args.expect == "ok":
        # The screen probe can exit non-zero with a passing environment gate, so ignore proc rc here.
        if failed:
            print("::error::doctor's environment gate reported a ✗")
            return 1
        return 0

    # expect == "broken": the gate must refuse the host (non-zero exit) AND show a ✗.
    if proc.returncode == 0:
        print("::error::doctor exited 0 in a broken environment; expected it to fail")
        return 1
    if not failed:
        print("::error::expected a ✗ in doctor's environment section, found none")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
