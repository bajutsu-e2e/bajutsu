"""Locate and materialize the wheel-bundled generic XCUITest Simulator runner (BE-XXXX).

The XCUITest runner is app-agnostic (BE-0019): one built ``.xctestrun`` plus its products drives
whatever app a run targets. Shipping that runner as wheel package data lets ``xcuitest.testRunner``
be optional — a Simulator run that names no runner resolves to the bundled one here. The products
are inert on any non-macOS install (the runner only ever runs against a Simulator), so the base
wheel stays pure-Python; the bytes ride along as unused data.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import bajutsu

# The packaged products directory: ``.xctestrun`` plus the test bundles beside it, populated by the
# release build step (``make runner-bundle``) and force-included via pyproject ``artifacts``. Absent
# in a plain source checkout and on a Linux wheel, so callers treat "no bundle" as a normal state.
_BUNDLE_DIR = Path(__file__).resolve().parents[2] / "_xcuitest_runner"
_RUNNER_NAME = "BajutsuRunner.xctestrun"


def bundled_products_dir() -> Path | None:
    """Return the packaged runner products directory if this build ships one, else ``None``."""
    return _BUNDLE_DIR if (_BUNDLE_DIR / _RUNNER_NAME).is_file() else None


def _cache_root() -> Path:
    """The per-user cache root for the materialized runner, honoring ``XDG_CACHE_HOME``."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "bajutsu" / "xcuitest-runner"


def materialize(
    source: Path,
    *,
    version: str = bajutsu.__version__,
    cache_root: Path | None = None,
) -> Path:
    """Copy *source* products into a per-version writable cache and return the ``.xctestrun`` path.

    The installed wheel's package data is read-only, yet a run patches a copy of the ``.xctestrun``
    beside the products to inject its launch environment, so the runner must sit somewhere writable.
    The copy is keyed by *version*: an upgrade lands in a new directory, and a warm cache is reused
    without recopying.

    Args:
        source: The products directory to copy (typically ``bundled_products_dir()``).
        version: Cache key; defaults to the installed Bajutsu version.
        cache_root: Override the cache location (tests inject a ``tmp_path``).

    Returns:
        The path to the materialized ``.xctestrun``.
    """
    dest = (cache_root or _cache_root()) / version
    runner = dest / _RUNNER_NAME
    if not runner.is_file():
        # Copy into a sibling temp dir then rename, so a crash mid-copy never leaves a
        # half-populated version directory that a later run would mistake for a warm cache.
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(source, tmp)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp, dest)
    return runner
