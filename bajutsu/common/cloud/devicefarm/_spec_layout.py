"""Where a test spec finds Bajutsu on the Device Farm host and runs from (BE-0465)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _SpecLayout:
    """Where a test spec finds Bajutsu on the Device Farm host and runs from.

    The defaults are an APPIUM_PYTHON run, which installs and runs from the unpacked test package.
    `package` is the shell-ready path the spec installs Bajutsu from; `stage` puts it there first;
    `runner_setup` runs in `pre_test` after the visibility probe; `cd` prefixes every run command;
    `runs` is the shell-ready `runs/` tree `post_test` collects.
    """

    package: str = '"$DEVICEFARM_TEST_PACKAGE_PATH"'
    stage: tuple[str, ...] = ()
    runner_setup: tuple[str, ...] = ()
    cd: str = ""
    runs: str = "runs"
