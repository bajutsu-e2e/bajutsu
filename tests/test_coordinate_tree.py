"""Cross-backend tests for the shared `CoordinateTreeDriver` read path (BE-0254).

`IdbDriver` and `AdbDriver` inherit their transient-empty retry, exponential backoff, and stable-key
projection from `CoordinateTreeDriver`. These parametrize one set of assertions across *both* real
subclasses — driving each backend's actual `_describe` through an injected `run` returning that
backend's native dump text — so a future change to the base class is verified against idb and adb at
once, not against each driver's own copy of the test. Per-backend specifics (idb's companion-reset
wedge recovery, adb's wall-clock settle and scroll-into-view) stay in `test_idb.py` / `test_adb.py`.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from bajutsu.drivers.adb import AdbDriver
from bajutsu.drivers.coordinate_tree import CoordinateTreeDriver
from bajutsu.drivers.idb import IdbDriver


def _idb_tree(n: int) -> str:
    """idb describe-all JSON for a tree of `n` plain button elements (each with real geometry)."""
    items = [
        {
            "AXUniqueId": f"e{i}",
            "type": "Button",
            "frame": {"x": 0, "y": i * 10, "width": 10, "height": 10},
        }
        for i in range(n)
    ]
    return json.dumps(items)


def _adb_tree(n: int) -> str:
    """A `uiautomator dump` hierarchy of `n` sibling button nodes (each with real bounds)."""
    nodes = "".join(
        f'<node index="{i}" text="e{i}" resource-id="e{i}" class="android.widget.Button" '
        f'bounds="[0,{i * 10}][10,{i * 10 + 10}]" />'
        for i in range(n)
    )
    return f'<hierarchy rotation="0">{nodes}</hierarchy>'


# One factory per backend: given the sequence of element-counts each successive describe should
# yield (holding the last once exhausted), build the real driver over an injected run and a call
# counter. `_EMPTY_BACKOFF_S = 0` keeps the retry loop from sleeping in the test.
def _idb_backend(counts: list[int]) -> tuple[CoordinateTreeDriver, list[int]]:
    seq = [_idb_tree(n) for n in counts]
    calls = [0]

    def run(args: list[str]) -> str:
        if "describe-all" in args:
            calls[0] += 1
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return ""

    driver = IdbDriver("U", run=run)
    driver._EMPTY_BACKOFF_S = 0
    return driver, calls


def _adb_backend(counts: list[int]) -> tuple[CoordinateTreeDriver, list[int]]:
    seq = [_adb_tree(n) for n in counts]
    calls = [0]

    def run(args: list[str]) -> str:
        if "uiautomator" in args:
            calls[0] += 1
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return ""

    driver = AdbDriver("emulator-5554", run=run)
    driver._EMPTY_BACKOFF_S = 0
    return driver, calls


BackendFactory = Callable[[list[int]], tuple[CoordinateTreeDriver, list[int]]]

_BACKENDS = pytest.mark.parametrize("backend", [_idb_backend, _adb_backend], ids=["idb", "adb"])


@_BACKENDS
def test_query_retries_through_transient_empty(backend: BackendFactory) -> None:
    # A richer tree first (establishes the baseline), then a transient empty, then the full tree.
    driver, calls = backend([3, 1, 3])

    assert len(driver.query()) == 3  # baseline: _max_seen becomes 3
    assert len(driver.query()) == 3  # hits the empty then recovers to the full tree
    assert calls[0] == 3  # 1 baseline + (1 empty + 1 recovered)


@_BACKENDS
def test_query_does_not_retry_genuinely_sparse_screen(backend: BackendFactory) -> None:
    # No richer tree has ever been seen, so a small tree is taken at face value — never masked.
    driver, calls = backend([1])

    assert len(driver.query()) == 1
    assert calls[0] == 1  # returned immediately, no retry


@_BACKENDS
def test_query_returns_after_bounded_retries_when_empty_persists(backend: BackendFactory) -> None:
    # After a rich tree, a persistent empty is retried a bounded number of times and then returned —
    # query() must not hang masking a real empty screen.
    driver, calls = backend([3, 1])

    assert len(driver.query()) == 3  # baseline
    calls[0] = 0
    assert len(driver.query()) == 1  # gives up and returns the empty tree
    assert calls[0] == 1 + type(driver)._EMPTY_RETRIES  # initial read + bounded retries


@_BACKENDS
def test_empty_backoff_schedule_is_identical(backend: BackendFactory) -> None:
    # The shared exponential-backoff schedule: base 0.05, doubling, capped at 0.2, five entries —
    # the same on every coordinate backend because it lives in the base class.
    driver, _ = backend([1])
    driver._EMPTY_BACKOFF_S = 0.05  # undo the test factory's zeroing to read the real schedule
    seq = [driver._empty_backoff(i) for i in range(type(driver)._EMPTY_RETRIES)]
    assert seq == [0.05, 0.1, 0.2, 0.2, 0.2]
    assert sum(seq) <= 1.0  # bounded total added wait


@_BACKENDS
def test_stable_key_ignores_volatile_fields_and_updates_cache(backend: BackendFactory) -> None:
    # The identifier-frame projection is the settle key; query() caches it. A query populates the
    # cache with exactly the projection of the tree it read.
    driver, _ = backend([3])
    assert driver._last_stable_key is None
    tree = driver.query()
    assert driver._last_stable_key == type(driver)._stable_key(tree)

    # Volatile value/traits/label do not move the key; identifier or frame does.
    a: list = [
        {"identifier": "x", "label": "A", "value": "1", "traits": ["button"], "frame": (0, 0, 1, 1)}
    ]
    b: list = [{"identifier": "x", "label": "B", "value": "2", "traits": [], "frame": (0, 0, 1, 1)}]
    c: list = [
        {"identifier": "x", "label": "A", "value": "1", "traits": ["button"], "frame": (0, 9, 1, 1)}
    ]
    key = type(driver)._stable_key
    assert key(a) == key(b)  # only volatile fields differ
    assert key(a) != key(c)  # frame differs
