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

from bajutsu.drivers import base
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

#: The read path's own logger. Both fault-injection lanes (BE-0305) attach to this exact name, so the
#: tests below assert over it rather than over whatever else `caplog` happened to capture.
_LOGGER = "bajutsu.drivers.coordinate_tree"

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
def test_a_retried_transient_empty_is_reported(
    backend: BackendFactory, caplog: pytest.LogCaptureFixture
) -> None:
    # A read that only succeeded because it was retried must not look identical to one that never
    # faulted (BE-0305): the retry absorbs a real device fault, so it says so — naming the counts the
    # heuristic compared, which is what makes a lane's real degenerate read attributable to this loop.
    driver, _ = backend([3, 1, 3])
    driver.query()  # baseline: _max_seen becomes 3
    with caplog.at_level("WARNING", logger=_LOGGER):
        assert len(driver.query()) == 3
    # The rendered sentence, not two fragments: the counts are positional, so a transposed pair would
    # keep every fragment present while reporting a floor and a richest-seen tree that never existed —
    # and those numbers are exactly what the on-device lane's rationale rests on.
    assert (
        "read returned 1 element(s), below the 2-element floor after a 3-element tree was seen "
        "— a transient empty; retrying in 0.00s (attempt 1/5)" in caplog.text
    )
    # The record's logger, too: both fault-injection lanes attach to this name, so a record moved to
    # another logger would leave them waiting for something that can never arrive.
    assert [record.name for record in caplog.records] == [_LOGGER]


@_BACKENDS
def test_a_recovered_transient_empty_is_not_reported_as_exhausted(
    backend: BackendFactory, caplog: pytest.LogCaptureFixture
) -> None:
    # The negative half of the pair, and the contract the on-device lane leans on to tell a lost
    # lift-versus-retry race from a broken retry: a read that recovered must not also claim its budget
    # ran out.
    driver, _ = backend([3, 1, 3])
    driver.query()
    with caplog.at_level("WARNING", logger=_LOGGER):
        assert len(driver.query()) == 3
    assert "returning the degenerate tree" not in caplog.text


@_BACKENDS
def test_an_exhausted_retry_budget_is_reported(
    backend: BackendFactory, caplog: pytest.LogCaptureFixture
) -> None:
    # The complement: an empty that outlives the budget is handed back, so the caller's selector is
    # about to fail — say why rather than letting a bare "element not found" carry the whole story.
    driver, _ = backend([3, 1])
    driver.query()
    with caplog.at_level("WARNING", logger=_LOGGER):
        assert len(driver.query()) == 1
    assert "returning the degenerate tree" in caplog.text
    assert "attempt 5/5" in caplog.text  # the budget was spent, not skipped


@_BACKENDS
def test_an_unrecoverable_empty_yields_promptly_and_is_left_to_the_caller(
    backend: BackendFactory, caplog: pytest.LogCaptureFixture
) -> None:
    # The other degenerate read: one no same-source re-read can clear (an accessibility-bridge wedge,
    # BE-0231 Unit 6). It must leave the loop at once rather than spend the budget, and stay silent
    # here — `query`'s own recovery owns that diagnosis, and reporting "the budget ran out" would name
    # the wrong fault. No shipping backend overrides the hook today, so without this test the whole
    # branch is unexercised and could be deleted with every suite still green.
    driver, calls = backend([3, 1])
    driver.query()  # baseline: _max_seen becomes 3
    driver._is_unrecoverable_empty = lambda els: True  # type: ignore[method-assign]
    calls[0] = 0
    with caplog.at_level("WARNING", logger=_LOGGER):
        assert len(driver.query()) == 1
    assert calls[0] == 1  # yielded on the first read; the retry budget was not spent
    assert [r for r in caplog.records if r.name == _LOGGER] == []


@_BACKENDS
def test_a_genuinely_sparse_screen_is_not_reported_as_a_fault(
    backend: BackendFactory, caplog: pytest.LogCaptureFixture
) -> None:
    # A screen that has only ever been sparse is a real screen, not a fault: it is returned untouched
    # and must stay silent, or every such read would cry wolf in the job log.
    driver, _ = backend([1])
    with caplog.at_level("WARNING", logger=_LOGGER):
        assert len(driver.query()) == 1
    # Scoped to this logger: `caplog` captures at the root, so an unrelated warning from elsewhere in
    # the read path would otherwise fail this test for a reason that has nothing to do with its name.
    assert [r for r in caplog.records if r.name == _LOGGER] == []


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
    a: list[base.Element] = [
        {
            "identifier": "x",
            "label": "A",
            "value": "1",
            "traits": ["button"],
            "frame": (0, 0, 1, 1),
            "nativeZ": None,
        }
    ]
    b: list[base.Element] = [
        {
            "identifier": "x",
            "label": "B",
            "value": "2",
            "traits": [],
            "frame": (0, 0, 1, 1),
            "nativeZ": None,
        }
    ]
    c: list[base.Element] = [
        {
            "identifier": "x",
            "label": "A",
            "value": "1",
            "traits": ["button"],
            "frame": (0, 9, 1, 1),
            "nativeZ": None,
        }
    ]
    key = type(driver)._stable_key
    assert key(a) == key(b)  # only volatile fields differ
    assert key(a) != key(c)  # frame differs
