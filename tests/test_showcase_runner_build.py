"""The showcase runner-build recipes stage exactly one `.xctestrun` after an SDK change.

`xcodebuild build-for-testing` names its output `BajutsuRunner_<sdk>.xctestrun` and never deletes
the previous SDK's file, so after an Xcode upgrade the recipe's `cp` glob matched two sources plus a
file destination and failed with "Not a directory". These tests run the real Makefile recipes with
`xcodegen` / `xcodebuild` stubbed on `PATH`, so they need no Xcode and run on Linux too.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHOWCASE = ROOT / "demos" / "showcase"

# Writes the fresh per-SDK .xctestrun under `<cwd>/<-derivedDataPath>/Build/Products`, as the real
# build-for-testing does.
_XCODEBUILD_STUB = """#!/bin/sh
dd=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-derivedDataPath" ]; then dd="$2"; fi
  shift
done
mkdir -p "$dd/Build/Products"
echo new > "$dd/Build/Products/BajutsuRunner_iphonesimulator27.0-arm64-x86_64.xctestrun"
"""

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="needs make")


def _stub_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("xcodegen", "#!/bin/sh\nexit 0\n"), ("xcodebuild", _XCODEBUILD_STUB)):
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    return bin_dir


@pytest.mark.parametrize(
    ("target", "dd", "extra"),
    [
        ("runner-build", "dd", []),
        ("runner-build-device", "dd-device", ["DEVELOPMENT_TEAM=ABCDE12345"]),
    ],
)
def test_runner_build_ignores_a_previous_sdks_xctestrun(
    tmp_path: Path, target: str, dd: str, extra: list[str]
) -> None:
    runner = tmp_path / "Runner"
    products = runner / "build" / dd / "Build" / "Products"
    products.mkdir(parents=True)
    stale = products / "BajutsuRunner_iphonesimulator26.5-arm64-x86_64.xctestrun"
    stale.write_text("stale\n", encoding="utf-8")
    env = {**os.environ, "PATH": f"{_stub_bin(tmp_path)}{os.pathsep}{os.environ['PATH']}"}

    result = subprocess.run(
        ["make", "-C", str(SHOWCASE), target, f"RUNNER={runner}", *extra],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (products / "BajutsuRunner.xctestrun").read_text(encoding="utf-8") == "new\n"
    assert not stale.exists()
