"""Where one job's files sit in its Device Farm package (BE-0465)."""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.common.cloud.devicefarm import XCTEST_PACKAGE_DIR, TestType

from .xctest_runner import XctestRunner


@dataclass(frozen=True)
class _PackageLayout:
    """Where one job's files sit in its package, and in the directory its test spec runs from.

    `runner` is an iOS job's runner build, which makes the package an XCTEST_UI one: the runner app
    at the package root, everything else under `XCTEST_PACKAGE_DIR`. `bundle` prefixes the job's
    bundle inside the directory the test spec runs from.
    """

    runner: XctestRunner | None
    bundle: str = ""

    @property
    def test_type(self) -> TestType:
        """The Device Farm test type the package goes up as."""
        return "XCTEST_UI" if self.runner is not None else "APPIUM_PYTHON"

    @property
    def xctest_runner(self) -> str | None:
        """The `.xctestrun` path the test spec puts Device Farm's re-signed runner at, if any."""
        return f"{self.bundle}{self.runner.xctestrun_arc}" if self.runner is not None else None

    def place(self, arc: str) -> str:
        """*arc*'s path inside the package, given its path inside the directory the spec runs from."""
        if self.runner is None:
            return arc
        return XCTEST_PACKAGE_DIR if arc == "." else f"{XCTEST_PACKAGE_DIR}/{arc}"
