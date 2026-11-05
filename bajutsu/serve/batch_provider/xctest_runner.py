"""The device runner build an iOS fan-out ships for Device Farm to re-sign (BE-0465)."""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bajutsu.common.cloud.devicefarm import DeviceFarmError
from bajutsu.common.config.schema._merge import merge_defaults

# The placeholder an `.xctestrun` uses for its own directory.
_TESTROOT = "__TESTROOT__"


@dataclass(frozen=True)
class XctestRunner:
    """A target's runner build inside a job's bundle.

    Attributes:
        xctestrun: The `.xctestrun` the target's `driver.runner.testRunner` names.
        xctestrun_arc: Its path relative to the bundle directory, POSIX-style. The paths here are
            resolved, so a caller holding a symlinked bundle path cannot relativise them itself.
        app: The runner app its `TestHostPath` names, which the package carries at its root.
        build_files: Every file of the build — the `.xctestrun` and the products directory beside
            it. Device Farm hands the test spec a re-signed copy, so the bundle leaves these out.
    """

    xctestrun: Path
    xctestrun_arc: str
    app: Path
    build_files: tuple[Path, ...]


def xctest_runner(work_dir: Path, config: str, target: str) -> XctestRunner:
    """Find the runner build *target* names in the job's config, resolved inside *work_dir*.

    Raises:
        DeviceFarmError: The target names no `testRunner`, or the build is not in the bundle — an
            XCTEST_UI run has no runner to install without it.
    """
    config_path = work_dir / config
    raw = _target(config_path, target)
    runner = (raw.get("driver") or {}).get("runner") or {}
    test_runner = runner.get("testRunner") if isinstance(runner, dict) else None
    if not isinstance(test_runner, str):
        raise DeviceFarmError(
            f"target {target!r} names no driver.runner.testRunner: an iOS fan-out ships a device "
            "runner build for Device Farm to re-sign (`bajutsu runner build --device --out`)"
        )
    root = work_dir.resolve()
    xctestrun = (config_path.parent / test_runner).resolve()
    if not xctestrun.is_file() or not xctestrun.is_relative_to(root):
        raise DeviceFarmError(
            f"driver.runner.testRunner {test_runner!r} is not in this job's bundle ({xctestrun})"
        )
    host_path = _test_host_path(plistlib.loads(xctestrun.read_bytes()))
    if host_path is None:
        raise DeviceFarmError(f"{xctestrun} names no TestHostPath")
    app = Path(host_path.replace(_TESTROOT, str(xctestrun.parent))).resolve()
    if not app.is_dir() or not app.is_relative_to(xctestrun.parent):
        raise DeviceFarmError(f"the runner app {app.name} is not beside {xctestrun.name} ({app})")
    products = app.parent
    build_files = (xctestrun, *(p for p in sorted(products.rglob("*")) if p.is_file()))
    return XctestRunner(
        xctestrun=xctestrun,
        xctestrun_arc=xctestrun.relative_to(root).as_posix(),
        app=app,
        build_files=build_files,
    )


def _target(config_path: Path, target: str) -> dict[str, Any]:
    """*target*'s raw entry, with the config's defaults merged in as the loader merges them."""
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    targets = config.get("targets") if isinstance(config, dict) else None
    raw = targets.get(target) if isinstance(targets, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    defaults = config.get("defaults") if isinstance(config, dict) else None
    merged = merge_defaults(defaults, raw) if isinstance(defaults, dict) else raw
    return merged if isinstance(merged, dict) else {}


def _test_host_path(node: Any) -> str | None:
    """The first `TestHostPath` in an `.xctestrun`, whichever format version nests it."""
    if isinstance(node, dict):
        value = node.get("TestHostPath")
        if isinstance(value, str):
            return value
        children: Any = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    return next((found for child in children if (found := _test_host_path(child))), None)
