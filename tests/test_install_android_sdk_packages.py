"""Tests for scripts/install_android_sdk_packages.sh — the guard behind issue #2519.

`sdkmanager` reports a downloaded archive it cannot unpack as a warning and still exits 0, so on
CI a damaged system-image download used to surface only as a later `adb` `Connection refused`. The
script retries once and then fails naming the package. A fake `sdkmanager` stands in for the real
one, scripted per attempt, so each way an install can go wrong is reproduced on demand.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_android_sdk_packages.sh"
IMAGE = "system-images;android-34;google_apis;x86_64"
DAMAGED = (
    "Warning: An error occurred while preparing SDK package Google APIs Intel x86_64 Atom System "
    "Image: Error reading Zip content from a SeekableByteChannel."
)

# Each call reads its outcome from the next line of $FAKE_PLAN: `ok` installs the package (writes
# its package.xml), `damaged` prints sdkmanager's unpack warning and exits 0 without installing,
# `silent` exits 0 without installing or printing anything, `exit` fails with status 3.
_FAKE = r"""#!/usr/bin/env bash
pkg=$2
n=$(($(cat "$FAKE_CALLS" 2>/dev/null || echo 0) + 1))
echo "$n" >"$FAKE_CALLS"
echo "$pkg $3" >>"$FAKE_LOG"
case "$(sed -n "${n}p" "$FAKE_PLAN")" in
  ok) mkdir -p "$ANDROID_HOME/${pkg//;//}" && touch "$ANDROID_HOME/${pkg//;//}/package.xml" ;;
  damaged) echo "$FAKE_DAMAGED" ;;
  silent) ;;
  exit) echo "boom"; exit 3 ;;
esac
"""


def _run(
    tmp_path: Path, plan: list[str], *packages: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fake = tmp_path / "sdkmanager"
    fake.write_text(_FAKE)
    fake.chmod(0o755)
    (tmp_path / "plan").write_text("\n".join(plan) + "\n")
    sdk = tmp_path / "sdk"
    sdk.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "ANDROID_HOME": str(sdk),
        "SDKMANAGER": str(fake),
        "FAKE_PLAN": str(tmp_path / "plan"),
        "FAKE_CALLS": str(tmp_path / "calls"),
        "FAKE_LOG": str(tmp_path / "log"),
        "FAKE_DAMAGED": DAMAGED,
    }
    # The script's SDK_CHANNEL default is what the argv assertions pin, so an ambient value must
    # not reach it.
    env.pop("SDK_CHANNEL", None)
    proc = subprocess.run(
        ["bash", str(SCRIPT), *packages], env=env, capture_output=True, text=True, check=False
    )
    log = tmp_path / "log"
    return proc, log.read_text().splitlines() if log.exists() else []


def test_installs_each_package_once_when_every_download_is_sound(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, ["ok", "ok"], "emulator", IMAGE)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == ["emulator --channel=0", f"{IMAGE} --channel=0"]
    assert (tmp_path / "sdk/system-images/android-34/google_apis/x86_64/package.xml").is_file()
    assert "::warning::" not in proc.stdout


def test_retries_once_when_sdkmanager_only_warns_about_a_damaged_archive(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, ["damaged", "ok"], IMAGE)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == [f"{IMAGE} --channel=0"] * 2
    assert f"::warning::Android SDK package '{IMAGE}': sdkmanager could not unpack" in proc.stdout
    # The captured sdkmanager output stays in the log.
    assert DAMAGED in proc.stdout


def test_fails_naming_the_package_when_the_archive_is_damaged_twice(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, ["ok", "damaged", "damaged"], "emulator", IMAGE)
    assert proc.returncode == 1
    assert len(calls) == 3
    assert (
        f"::error::Android SDK package '{IMAGE}' is not installed after 2 attempts: "
        "sdkmanager could not unpack the downloaded archive"
    ) in proc.stdout


def test_a_damaged_archive_fails_even_when_a_stale_copy_is_installed(tmp_path: Path) -> None:
    # The warning alone decides: an earlier copy's package.xml must not mask a failed update.
    proc, _ = _run(tmp_path, ["ok", "damaged", "damaged"], "emulator", "emulator")
    assert proc.returncode == 1
    assert "::error::Android SDK package 'emulator'" in proc.stdout


def test_fails_when_sdkmanager_exits_zero_but_installs_nothing(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, ["silent", "silent"], IMAGE)
    assert proc.returncode == 1
    assert len(calls) == 2
    assert "google_apis/x86_64/package.xml is missing" in proc.stdout


def test_fails_when_sdkmanager_itself_fails(tmp_path: Path) -> None:
    proc, _ = _run(tmp_path, ["exit", "exit"], "emulator")
    assert proc.returncode == 1
    assert "sdkmanager exited 3" in proc.stdout


def test_refuses_to_run_without_packages_or_sdk_root(tmp_path: Path) -> None:
    proc = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert proc.returncode == 2
    env = {k: v for k, v in os.environ.items() if k != "ANDROID_HOME"}
    proc = subprocess.run(
        ["bash", str(SCRIPT), "emulator"], env=env, capture_output=True, text=True, check=False
    )
    assert proc.returncode != 0
    assert "ANDROID_HOME" in proc.stderr


def test_the_retry_clears_sdkmanagers_download_staging(tmp_path: Path) -> None:
    staged = tmp_path / "sdk" / ".temp" / "partial.zip"
    staged.parent.mkdir(parents=True)
    staged.write_text("damaged")
    proc, _ = _run(tmp_path, ["damaged", "ok"], IMAGE)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not staged.exists()
