"""Shared read/settle core for the coordinate-based device backends (BE-0254).

`IdbDriver` (iOS Simulator via idb) and `AdbDriver` (Android via adb + UI Automator) both dump an
accessibility tree from the device, normalize it into `Element`s, and act by tapping a resolved
frame's center. Their determinism-sensitive read path — the transient-empty retry, the exponential
backoff, the stable-key projection, and the not-found resolve loop — is identical, so it lives here
once: a fix to the transient-empty heuristic (tightening the backoff cap, changing `_READY_MIN`
after a new flake is diagnosed) is made in one place and both backends inherit it, rather than the
two read paths silently drifting apart.

A subclass supplies only its own tree source (`_describe`) and keeps whatever is genuinely
backend-specific: its actuators, and its own `_settle`. The two `_settle` methods now poll the same
`_stable_key` projection on the same wall-clock-deadline shape (idb adopted it in BE-0299 Unit 4,
adb in BE-0245), differing only in their deadline and poll-interval constants; folding them into one
`_settle` here is a natural follow-up now that the shapes match. It is left per-backend for now to
keep BE-0299 Unit 4 scoped to idb — each backend keeps and tests its own `_settle` — rather than
restructuring adb's settle in the same change.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from bajutsu.drivers import base

# The settle projection: identifier + frame per element, sorted — a screen's stable shape ignoring
# volatile value/traits/label.
_StableKey = tuple[tuple[str, base.Frame], ...]


class CoordinateTreeDriver(ABC):
    """Shared transient-empty / resolve machinery for a coordinate-based tree backend.

    A subclass implements `_describe()` (its own device dump + parse) and its own actuators; the
    read-path discipline that decides whether a freshly read tree is a real screen or a transient
    mid-transition artifact lives here, so both coordinate backends share one copy of it.
    """

    # During a screen transition the device intermittently returns a near-empty accessibility tree
    # even though the screen has visually rendered. These bound a short retry so query() rides over
    # that transient without masking a genuinely sparse screen for long.
    _READY_MIN = 2  # a tree this size or larger is treated as settled
    _EMPTY_RETRIES = 5  # extra describe attempts on a degenerate tree
    _EMPTY_BACKOFF_S = 0.05  # base delay; doubles each attempt up to the cap
    _EMPTY_BACKOFF_MAX_S = 0.2  # cap on a single backoff (total added <= ~0.75s, bounded)

    def __init__(self) -> None:
        self._max_seen = 0  # richest tree seen on this device; gates the empty retry
        self._last_stable_key: _StableKey | None = None

    @abstractmethod
    def _describe(self) -> list[base.Element]:
        """Read the device's accessibility tree once and normalize it into Elements.

        The one seam between the shared read path and the backend-specific dump: idb's
        `ui describe-all` + JSON parse, adb's `uiautomator dump` + XML parse.
        """

    def query(self) -> list[base.Element]:
        """A settled tree read: the transient-empty retry, then the stable-key bookkeeping.

        A degenerate result is retried a bounded number of times once a richer tree has been seen on
        this device (see `_is_transient_empty`), so a single-shot assertion or wait does not act on
        the transient snapshot; a screen that has only ever been sparse is returned as-is (never
        masked). A subclass that layers a backend-specific recovery on top (idb's companion reset)
        overrides this and drives `_read_settled_tree` / `_record_tree` itself.
        """
        return self._record_tree(self._read_settled_tree())

    def _read_settled_tree(self) -> list[base.Element]:
        """Read once, retrying a transient-empty tree with exponential backoff.

        Stops early on an unrecoverable empty (`_is_unrecoverable_empty`) so a backend does not burn
        the backoff loop on a read that a same-source re-read can never clear (idb's
        accessibility-bridge wedge, BE-0231 Unit 6) — the caller (`query`) handles that case.
        """
        els = self._describe()
        for i in range(self._EMPTY_RETRIES):
            if not self._is_transient_empty(els) or self._is_unrecoverable_empty(els):
                break
            time.sleep(self._empty_backoff(i))
            els = self._describe()
        return els

    def _record_tree(self, els: list[base.Element]) -> list[base.Element]:
        """Update the richest-seen count and the settle cache from a freshly read tree."""
        self._max_seen = max(self._max_seen, len(els))
        self._last_stable_key = self._stable_key(els)
        return els

    def _is_transient_empty(self, els: list[base.Element]) -> bool:
        """Whether a result looks like the device's mid-transition empty tree, not a real screen.

        Fewer than `_READY_MIN` elements, but only once a richer tree has been observed (so the first
        sparse screen seen is taken at face value).
        """
        return len(els) < self._READY_MIN and self._max_seen >= self._READY_MIN

    def _is_unrecoverable_empty(self, els: list[base.Element]) -> bool:
        """Whether a degenerate read is one a same-source re-read can never clear.

        Default: none. A subclass whose device has such a failure mode (idb's accessibility-bridge
        wedge) overrides this so `_read_settled_tree` yields it promptly to `query`'s recovery path
        rather than spending the backoff loop on a read that cannot recover.
        """
        return False

    def _empty_backoff(self, attempt: int) -> float:
        """Exponential backoff for the transient-empty retry: base * 2**attempt, capped.

        Recovers fast when the empty clears on the first retry and spaces out later, while the cap
        keeps the total added wait bounded.
        """
        # 2.0** (not 2**) keeps the result a float: mypy types int**int as Any (it is float for a
        # negative exponent), which would leak through min() as an Any return.
        return min(self._EMPTY_BACKOFF_S * 2.0**attempt, self._EMPTY_BACKOFF_MAX_S)

    @staticmethod
    def _stable_key(els: list[base.Element]) -> _StableKey:
        """Identifier-frame projection for settle: ignores volatile value/traits/label."""
        return tuple(sorted((e["identifier"] or "", e["frame"]) for e in els))

    def _resolve(
        self,
        sel: base.Selector,
        timeout: float = 3.0,
        poll: float = 0.2,
        *,
        initial_tree: list[base.Element] | None = None,
    ) -> base.Element:
        # Real-device trees can be transiently empty during transitions; retry not-found while
        # keeping ambiguity fail-fast.
        deadline = time.monotonic() + timeout
        tree = initial_tree if initial_tree is not None else self.query()
        while True:
            try:
                return base.resolve_unique(tree, sel)
            except base.ElementNotFound:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(poll)
                tree = self.query()

    def wait_for(self, sel: base.Selector) -> bool:
        """Single-shot: whether `sel` matches the current screen (BE-0118).

        Delegates to the shared `base.default_wait_for` so every backend shares one body; the
        deadline poll lives in `base.wait_until`, so the timeout is honoured identically (BE-0251).
        """
        return base.default_wait_for(self, sel)
