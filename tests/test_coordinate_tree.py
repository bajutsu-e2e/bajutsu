"""Tests for the shared `CoordinateTreeDriver` read path (BE-0254).

`AdbDriver` inherits its transient-empty retry, exponential backoff, and stable-key projection from
`CoordinateTreeDriver` — driving its actual `_describe` through an injected `run` returning UI
Automator's native dump text, so a change to the base class is verified against a real subclass, not
against a copy of the test. Adb-specific behavior (wall-clock settle, scroll-into-view) stays in
`test_adb.py`.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from bajutsu.drivers.adb import AdbDriver
from bajutsu.drivers.coordinate_tree import CoordinateTreeDriver


def _adb_tree(n: int) -> str:
    """A `uiautomator dump` hierarchy of `n` sibling button nodes (each with real bounds)."""
    nodes = "".join(
        f'<node index="{i}" text="e{i}" resource-id="e{i}" class="android.widget.Button" '
        f'bounds="[0,{i * 10}][10,{i * 10 + 10}]" />'
        for i in range(n)
    )
    return f'<hierarchy rotation="0">{nodes}</hierarchy>'


# A factory for the backend: given the sequence of element-counts each successive describe should
# yield (holding the last once exhausted), build the real driver over an injected run and a call
# counter. `_EMPTY_BACKOFF_S = 0` keeps the retry loop from sleeping in the test.
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

_BACKENDS = pytest.mark.parametrize("backend", [_adb_backend], ids=["adb"])


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
