"""Lightweight evidence capture (the M1 default three).

- elements: an `elements.json` dump of the current screen
- screenshot: a PNG via the driver
- actionLog: the per-step record (action / result / duration) already lives in the
  manifest, so it is not re-captured here

Full capturePolicy triggering (rules / around-lifecycle / video / network) is
layered on later; these are the primitives it will call.
"""

from __future__ import annotations

import json
from pathlib import Path

from simpilot.drivers import base


def write_elements(driver: base.Driver, step_dir: Path) -> Path:
    step_dir.mkdir(parents=True, exist_ok=True)
    path = step_dir / "elements.json"
    path.write_text(
        json.dumps(driver.query(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def write_screenshot(driver: base.Driver, step_dir: Path, name: str = "after.png") -> Path:
    step_dir.mkdir(parents=True, exist_ok=True)
    path = step_dir / name
    driver.screenshot(str(path))
    return path


def capture(driver: base.Driver, step_dir: Path, kinds: list[str]) -> list[str]:
    """Capture the requested lightweight kinds; return the artifact file names."""
    written: list[str] = []
    for token in kinds:
        kind, _, modifier = token.partition(".")
        if kind == "elements":
            written.append(write_elements(driver, step_dir).name)
        elif kind == "screenshot":
            name = f"{modifier or 'after'}.png"
            written.append(write_screenshot(driver, step_dir, name).name)
        # actionLog is in the manifest; video / network / appTrace come later.
    return written
