"""The Android driver: adb plus UI Automator behind the common `Driver` seam."""

from __future__ import annotations

import contextvars
import math
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree as ET

from bajutsu.common import stall_diagnostics
from bajutsu.common.backend_cli import adb
from bajutsu.common.drivers import base, tracing
from bajutsu.common.drivers.actuation import Actuation, ActuationLog, Drained
from bajutsu.common.drivers.coordinate_tree import CoordinateTreeDriver, StableKey
from bajutsu.common.drivers.elements import screen_size_from_elements
from bajutsu.common.evidence import intervals

from ._catchup import _Catchup
from ._functions import _parse_wm_size, elements_with_identities, slice_hierarchy_root
from ._shared import NodeIdentity, logger
from .act_outcome import ActOutcome
from .act_request import ActRequest
from .adb_act_uncertain import AdbActUncertain
from .adb_act_unsupported import AdbActUnsupported
from .adb_resident_error import AdbResidentError
from .hierarchy_read import HierarchyRead

RunFn = Callable[[list[str]], str]


# Takes the mark a read must postdate (BE-0332 Unit 4): the resident server blocks until an
# accessibility event postdates it, then dumps once, rather than re-dumping until two hierarchies
# match. None on a read with no gesture pending (nothing to postdate) and on the dump fallback.
HierarchyFetch = Callable[[float | None], HierarchyRead]

# The device's current clock (`SystemClock.uptimeMillis`), on the same scale as a read's `mark`, so the
# host can take a "before the gesture" mark that a later read must postdate (BE-0332 Unit 3). Returns
# None when the channel cannot answer — the barrier then degrades to its wall-clock budget rather than
# failing a read, never accepting a stale tree in the bargain.
ClockFetch = Callable[[], float | None]


# Perform one gesture on the device, against an element the host already resolved. `acted` is False when
# it answered `stale` — the identity no longer names the same nodes there, so the host re-resolves rather
# than letting a coordinate be guessed. Raises `AdbResidentError` when the channel itself fails, which
# the driver degrades to its own coordinate path.
ActFn = Callable[[ActRequest], ActOutcome]

# Android's `uiautomator` dump reports bounds in raw display pixels, so that is the space stamped on
# this backend's actuation records — a coordinate only means something alongside its unit.
_UNIT = "pixel"


# BE-0415: one wrap per callable, folded in at `__init__` only when a trace is already open
# (`--trace-driver` is a whole-run flag, so a driver built while none is open never traces for the
# rest of its lifetime either — checked once here instead of per call). `_run` becomes a
# `subprocess` record (it shells out); the resident-channel callables become `transport` records.
def _traced_run(run: RunFn) -> RunFn:
    def _wrapped(args: list[str]) -> str:
        ctx = tracing.current_trace()
        if ctx is None:
            return run(args)
        started_at = time.time()
        t0 = time.perf_counter()
        try:
            return run(args)
        finally:
            ctx.record("subprocess", " ".join(args), started_at, time.perf_counter() - t0)

    return _wrapped


def _traced_fetch_hierarchy(fetch: HierarchyFetch) -> HierarchyFetch:
    def _wrapped(deadline: float | None) -> HierarchyRead:
        ctx = tracing.current_trace()
        if ctx is None:
            return fetch(deadline)
        started_at = time.time()
        t0 = time.perf_counter()
        try:
            return fetch(deadline)
        finally:
            ctx.record("transport", "fetch_hierarchy", started_at, time.perf_counter() - t0)

    return _wrapped


def _traced_fetch_clock(fetch: ClockFetch) -> ClockFetch:
    def _wrapped() -> float | None:
        ctx = tracing.current_trace()
        if ctx is None:
            return fetch()
        started_at = time.time()
        t0 = time.perf_counter()
        try:
            return fetch()
        finally:
            ctx.record("transport", "fetch_clock", started_at, time.perf_counter() - t0)

    return _wrapped


def _traced_act(act: ActFn) -> ActFn:
    def _wrapped(request: ActRequest) -> ActOutcome:
        ctx = tracing.current_trace()
        if ctx is None:
            return act(request)
        started_at = time.time()
        t0 = time.perf_counter()
        outcome: ActOutcome | None = None
        try:
            outcome = act(request)
        finally:
            # `outcome` stays None when `act(request)` raised, so this still records the elapsed
            # time (unlike a raising `act`, which carries no outcome to report a response for).
            response = (
                None
                if outcome is None
                else {"acted": outcome.acted, "published_mark": outcome.published_mark}
            )
            ctx.record("transport", "act", started_at, time.perf_counter() - t0, response)
        return outcome

    return _wrapped


class AdbDriver(CoordinateTreeDriver):
    """Driver implementation for the Android emulator via adb + UI Automator.

    The transient-empty retry, exponential backoff, stable-key projection, and not-found resolve loop
    live in `CoordinateTreeDriver` (the reusable coordinate-backend core); this class supplies adb's
    own describe (`uiautomator dump` / resident channel + XML), its wall-clock `_settle`, the
    scroll-into-view and `sendevent` paths, and its actuators.
    """

    name = "adb"

    # Settle is bounded by wall-clock, not a fixed read count (BE-0245): a count-based cap ties the
    # settle window to how long each read happens to take, so a fast channel's reads could span only a
    # fraction of a second — short enough that a still-moving tree passes as settled and a tap fires on
    # a stale coordinate. Bounding by elapsed time instead keeps the window spanning a real animation
    # whatever the read costs: the loop polls until two consecutive reads share a frame projection, or
    # `_SETTLE_DEADLINE_S` elapses. A screen whose key was already proved stable — by `_settle`'s own
    # poll, or by a catch-up dwell-close — still settles in a single read; every other screen polls,
    # including a static one right after an actuation, which clears `_settled_key`. `_SETTLE_POLL_S`
    # is a small non-zero cadence so a fast read does not busy-spin (on the dump path the read
    # dwarfs it).
    # Set comfortably above the slow `uiautomator dump` read so that fallback path still gets several
    # attempts inside the window — the deadline is checked before each read, so a value near the read
    # latency would grant only one extra poll and shrink the settle window too far. A fast resident
    # read simply returns early on stability.
    _SETTLE_DEADLINE_S = 8.0  # ceiling on waiting for the tree to stop moving (spans a fling)
    _SETTLE_POLL_S = 0.1  # inter-read cadence on a fast channel; negligible against the dump read
    # Scroll-into-view (BE-0210): an action target that resolves to nothing in the current viewport
    # is scrolled toward and re-queried a bounded number of times before failing — a condition wait,
    # not a fixed sleep. Direction is always upward (content up, revealing rows below) — the
    # acceptance scenarios all scroll down a list, so this covers the common case. A target above
    # the current viewport exhausts retries and fails deterministically; bidirectional scroll is a
    # follow-up when a scenario needs it.
    _RESOLVE_TIMEOUT_S = 3.0  # the initial no-scroll resolve deadline (rides transient trees)
    _SCROLL_RETRIES = 3  # scroll-and-re-query attempts before a deterministic not-found failure
    _SCROLL_FROM_FRAC = 0.7  # swipe start, as a fraction of screen height
    _SCROLL_TO_FRAC = 0.3  # swipe end (< start ⇒ upward ⇒ content scrolls up)
    # The `scroll` pan's traversal speed, in device pixels per second, and the floor on the duration
    # it implies. A fixed 600 ms duration (what this was until BE-0400) makes the speed depend on the
    # distance asked for, so a large step traverses fast enough that the view stops tracking the whole
    # path: a 1440-pixel request travelled 1153 on the conformance emulator, 20 percent short. Holding
    # the *speed* fixed instead keeps every step size honest, the same correction the iOS runner
    # applies with `XCUIGestureVelocity`, and the value is that runner's 200 points per second carried
    # across at the two screens' physical scale. The floor keeps a small step from becoming a flick.
    _SCROLL_SPEED_PX_PER_S = 550
    _SCROLL_MIN_DURATION_MS = 600
    # Ceiling on waiting for a read to catch up with a gesture that already moved the content. One
    # number for one phenomenon, shared by two consumers: `read_lag()` hands it to the `scroll` loop
    # (BE-0326), and `_await_catchup` spends it on the actuator path. Android publishes the
    # accessibility update *after* the gesture has applied, so a read taken in between describes the
    # pre-gesture screen — self-consistently, and for longer than a single retry would ride out.
    #
    # Generous on purpose, because it is only ever spent on a read that still matches the pre-gesture
    # screen: a read that already caught up costs nothing. A gesture whose lag can outlast even this
    # budget (e.g. motion behind an element taller than the viewport) is a distinct case handled by
    # BE-0329's motion decisions, not by widening this ceiling further.
    _READ_LAG_S = 4.0
    # How long a changed projection must hold before it counts as caught up (see `_advance_catchup`).
    # Android republishes node bounds one node at a time rather than atomically, so a read taken
    # mid-catch-up can be torn — some frames already new, the rest still pre-gesture — and a dwell
    # requirement rides out that tear rather than closing the barrier on a half-updated tree.
    # Comfortably inside `_READ_LAG_S`, and paid only on a read that was still describing the pre-pan
    # screen.
    _CATCHUP_DWELL_S = 0.5

    def __init__(
        self,
        serial: str,
        run: RunFn = adb.real_run,
        *,
        fetch_hierarchy: HierarchyFetch | None = None,
        fetch_clock: ClockFetch | None = None,
        act: ActFn | None = None,
    ) -> None:
        super().__init__()
        self.serial = adb.checked_serial(serial)
        # BE-0415: a driver built while a trace is open times every subprocess call `run` issues and
        # every resident-channel round trip these three callables make. Checked once here, not on
        # every call — see `_traced_run` et al.'s own comment. `_run_text` (below) reads the same
        # ambient trace directly instead, since it is a plain staticmethod with no instance to wrap.
        if tracing.current_trace() is not None:
            run = _traced_run(run)
            if fetch_hierarchy is not None:
                fetch_hierarchy = _traced_fetch_hierarchy(fetch_hierarchy)
            if fetch_clock is not None:
                fetch_clock = _traced_fetch_clock(fetch_clock)
            if act is not None:
                act = _traced_act(act)
        self._run = run
        # When set, reads go through the resident channel and fall back to `uiautomator dump` only on
        # failure (BE-0245). Unset (the default) keeps today's dump-every-read behavior exactly.
        self._fetch_hierarchy = fetch_hierarchy
        # The resident channel's device-clock endpoint (BE-0332 Unit 3): read just before a gesture to
        # anchor the read-lag barrier on the device's own clock. None (the dump path, or an older
        # server) leaves the barrier on its wall-clock budget — a slower but equally safe degrade.
        self._fetch_clock = fetch_clock
        # The resident server's actuation endpoint, when the channel offers one. With it, a `tap`
        # resolves its target on the device — bounds read microseconds before the touch, in the warm
        # session — instead of the host computing a coordinate and `adb shell input` injecting it a
        # round trip later. None (the dump path, or an older server) keeps the coordinate actuators,
        # which stay the declared degraded mode rather than a silent second-best.
        self._act_fn = act
        # Each element's device-addressable identity, keyed by `id()` of the element dict the last read
        # produced — the same object `resolve_unique` hands back, so a resolved element maps straight to
        # what the device needs. Rebuilt on every read; a stale key simply misses and degrades.
        self._identities: dict[int, NodeIdentity] = {}
        self._last_tree: list[base.Element] = []
        # The raw dump text behind `_last_tree` (`RawSourceProvider`, the `rawTree` capture kind), and
        # the narrowed tree bajutsu actually parsed when narrowing changed something — both set on
        # every `_read_source()` call, and turned into a `RawSource` only by `last_raw_source()`, so a
        # run that never takes the capture never serializes the tree back (BE-0407 unit 23). None
        # until the first read.
        self._raw_reply: str | None = None
        self._parsed_root: ET.Element | None = None
        # The caught-up tree a confirmed gesture's own reply carried (BE-0407 unit 19), waiting for
        # the next `query()` to consume it in place of a read. None whenever there is none — before
        # the first gesture, after that read, and after anything that moves the screen since.
        self._seeded_tree: list[base.Element] | None = None
        # What this driver actually actuated, drained per step by the run loop. Android is the one
        # backend with two actuation channels, so the record's `via` is what tells a reader whether a
        # gesture went device-side (`identity`) or fell back to a host coordinate (`coordinate`).
        self._actuations = ActuationLog()
        # Latches the first device-actuation fault so a channel that answers reads but not `/act` — an
        # older server — degrades to coordinates once loudly rather than on every gesture.
        self._act_warned = False
        # Set once the channel proves it cannot serve `/act` (an older server 404s the path). Every
        # later gesture then goes straight to the coordinate path, so the degrade costs one probe for
        # the lease rather than a failed round trip — and a second resolve — on every tap (BE-0234).
        self._act_unavailable = False
        # The device event mark of the most recent read (BE-0332 Unit 3): `SystemClock.uptimeMillis` of
        # the newest accessibility event the resident reader had seen. None on the dump path. Set by
        # `_read_source` on every read, before `_advance_catchup` folds that read into the barrier.
        self._read_mark: float | None = None
        # Each opted-in view's own measured position from the last resident read (BE-0355). Empty on
        # the dump fallback, which carries no such reading.
        self._native_z: dict[str, float] = {}
        # Whether a read has postdated the *current* actuation's device mark (BE-0332 Unit 3). Reset on
        # every actuation and set true only when `_advance_catchup` closes a mark-anchored barrier on a
        # postdating read — never on the dump heuristic, an actuation that armed no mark, or a barrier
        # that timed out. `read_postdates_actuation` reports it. No production caller consults it now —
        # the `extract` poll's early release was withdrawn (see that poll's docstring) — but the
        # conformance suite checks the contract, so the flag stays accurate rather than guessed.
        self._read_ordered = False
        # Latches the first `/clock` fault so a persistently-broken clock endpoint on an otherwise-live
        # channel — which silently forfeits the early release and leaves the lane on its wall-clock
        # budget — is logged once rather than invisibly or on every gesture.
        self._clock_warned = False
        # Latches the first mark-less resident read (see `_read_source`), so a channel that serves
        # hierarchies without the read-mark header says so once rather than degrading invisibly.
        self._mark_warned = False
        # Ditto for a `/act` reply whose two marks contradict each other (see `_seed_from_act`).
        self._seed_mark_warned = False
        # Lazily resolved once for the sendevent double-tap path (BE-0208): whether adbd is root and
        # which node is the touchscreen. `_touch_probed` distinguishes "not yet looked" from "looked,
        # found nothing" so a device with no touchscreen is not re-probed on every double-tap.
        self._is_root: bool | None = None
        self._touch_dev: adb.TouchDevice | None = None
        self._touch_probed = False
        # The true display size (BE-0326), resolved once via `wm size`; the resolution is fixed for a run.
        self._screen: base.Point | None = None
        # The outstanding read-lag barrier for a pan, if any (see `_advance_catchup`). Closed by the
        # first read that shows the pan and holds, so a run whose tree keeps up never waits.
        self._catchup: _Catchup | None = None
        # The last key actually proved to be a rest state — by `_settle`'s own two-consecutive-reads
        # poll, or by `_advance_catchup`'s projection-dwell closing a barrier (also a two-observations
        # -apart proof, just made of the reads a `wait`/`assert` already took). Deliberately separate
        # from `_last_stable_key`, which every `query()` call overwrites regardless of caller: trusting
        # *that* cache on a bare match would let `_settle` skip its poll on the strength of a single
        # unrelated read that never itself proved the tree was at rest — how a still-animating tree
        # got treated as settled in the `gestures` flake this field exists to fix. None until proved,
        # and reset to None whenever a poll runs out its budget without converging.
        self._settled_key: StableKey | None = None
        # Whether the cached projection still describes the screen. False after anything actuates, so
        # a pan re-reads its catch-up baseline instead of inheriting a pre-actuation one
        # (`_pan_baseline`). Actuators clear it by routing through `_act`, and every read sets it,
        # rather than each actuator clearing it by hand: an actuator added later that reached for
        # `_run` directly would otherwise silently reintroduce a stale baseline. Read-only commands
        # (`screenshot`, `wm size`) stay on `_run`, so they neither clear nor re-set it.
        self._tree_current = False

    def invalidate_settled_cache(self) -> None:
        """Clear every cache `_settle()`/`_pan_baseline` trust (`base.SettledCacheInvalidator`).

        For a change to the screen this driver did not itself actuate. `_act()` and its
        device-side/text-entry equivalents call this for the driver's own gestures.
        But an app relaunch or a crawl reset replaces the screen through `adb.Env` directly — never
        through this driver's actuators — so nothing upstream of this method would otherwise know to
        distrust a key proved on the screen before it. If the relaunched screen's projection happens
        to coincide with that stale key (the common case: a scenario starting and ending on the same
        home screen), `_settle`'s fast path would trust a single read of a screen this driver never
        proved at rest — the same class of bug `_settled_key` exists to close, reached through a door
        outside the driver's own actuators. One method, one place every such caller reaches for,
        rather than each hand-rolling the same three-field reset (which is exactly how the
        `_device_act`/`type_text` gaps this same item fixed were introduced in the first place)
        (BE-0351).
        """
        self._tree_current = False
        self._read_ordered = False
        self._settled_key = None
        # A tree a gesture's own reply carried (BE-0407 unit 19) describes the screen as of that
        # gesture, so anything that moves the screen afterwards retires it for the same reason it
        # retires `_settled_key`. `_device_act` seeds it *after* calling here, which is the only
        # ordering that leaves it alive.
        self._seeded_tree = None

    def _act(self, args: list[str]) -> str:
        """Issue an adb command that changes the screen, marking the cached projection stale.

        Every actuator goes through this rather than `_run` directly, so "did the screen move since the
        last read?" has one owner. `_pan_baseline` needs that answer: a pan whose catch-up baseline
        predates an actuation is worse than no baseline at all, because the first post-pan read moves
        off it and the barrier credits the pan as published. `test_every_actuator_invalidates_the_cached_tree`
        guards the set, so an actuator added later cannot quietly keep using `_run`.

        It also clears `_read_ordered`: a new actuation is one the next read must postdate afresh, so any
        order confirmed for the previous one is stale (BE-0332 Unit 3).

        And it clears `_settled_key`: a key proven stable before this actuation describes a screen this
        actuation may have just changed, so `_settle`'s fast path must not trust a later coincidental
        match against it — the same staleness `_read_ordered` guards against, one cache higher.
        """
        self.invalidate_settled_cache()
        return self._run(args)

    def query(self) -> list[base.Element]:
        """A settled tree read, answered from a gesture's own reply when it carried one.

        The device dumps the caught-up tree into the `/act` reply of a gesture whose publish it
        confirmed (BE-0407 unit 19), so the read the host would take next has already happened — a
        whole round trip, measured at 400-600 ms on this backend. It is consumed once and then gone:
        the *next* read after that is a genuine one, and any actuation in between retires it through
        `invalidate_settled_cache`.

        Safe precisely because of what the confirmation says: an accessibility event postdates the
        injection, so this tree cannot be the pre-gesture screen. Where the device could not confirm,
        it sends no tree and nothing is seeded — the driver reads for itself and arms the barrier, as
        it always did.
        """
        seeded, self._seeded_tree = self._seeded_tree, None
        if seeded is not None:
            return seeded
        return super().query()

    def _seed_from_act(self, read: HierarchyRead | None, mark: float | None) -> None:
        """Adopt the caught-up tree a confirmed gesture's reply carried, as if a read had returned it.

        Everything `_describe` does for a read of its own, minus the round trip: the identity map the
        next gesture counts peers against, the raw-dump record the `rawTree` capture reads, and the
        stable-key bookkeeping `_record_tree` keeps.

        What it deliberately does *not* set is `_settled_key`. The device's own settle is bounded by
        a read count (`SETTLE_DUMPS`), and on expiry it returns the last, still-tearing dump with
        nothing on the wire to say so — so treating it as proof of rest would let `_settle` take its
        fast path and perform no confirming read at all, on a screen that may still be animating.
        That is the same trap `_advance_catchup` refuses when a barrier closes on a device mark, and
        the one `_SETTLE_DEADLINE_S` is wall-clock rather than count-based to avoid. The round trip
        is still saved: `_settle`'s poll consumes the seed as its first read instead of skipping the
        poll.

        The mark is re-checked here rather than taken on trust: the reply already had to carry a
        publish confirmation to get this far, and requiring the tree's own mark to postdate the
        gesture too means a server that mislabelled one header cannot seed a pre-gesture screen.

        A degenerate tree is refused for a different reason. `_read_settled_tree`'s transient-empty
        retry rides out the mid-transition dump this device is known to produce, and a seeded tree
        reaches `query()` without passing through it — so a sparse reply would be handed to a
        selector that the retry would have saved. Refusing it costs the round trip this unit saves
        and nothing else: the driver simply reads, with the retry, as it did before.
        """
        if read is None or read.mark is None:
            return  # the ordinary absence: an older server, or a gesture it could not confirm
        if mark is not None and read.mark <= mark:
            # Not ordinary at all: the reply confirmed a publish *and* carried a tree whose own mark
            # does not postdate the gesture, so the server's two marks disagree. Silence would leave
            # this guard indistinguishable from dead code while it fired on every gesture of a lease.
            if not self._seed_mark_warned:
                self._seed_mark_warned = True
                logger.warning(
                    "resident actuation confirmed a publish but its tree's read mark (%.0f) does "
                    "not postdate the gesture (%.0f); reading instead of seeding",
                    read.mark,
                    mark,
                )
            return
        root = read.root if read.root is not None else slice_hierarchy_root(read.text)
        # `read.native_z`, not `self._native_z`: the reply's own measurements belong to the reply's
        # own tree, and this runs before the driver adopts them.
        els, identities = elements_with_identities(root, read.native_z)
        # `not els` as well as the transient test, which needs a richer tree to have been seen
        # first: an empty seed would answer a selector with nothing at all, and the read it
        # replaced would have retried.
        if not els or self._is_transient_empty(els):
            logger.debug("device reply carried a degenerate tree; reading again instead of seeding")
            return
        self._read_mark = read.mark
        self._native_z = read.native_z
        self._raw_reply = read.text
        self._parsed_root = read.root if read.narrowed else None
        self._identities = {id(el): ident for el, ident in zip(els, identities, strict=True)}
        self._last_tree = els
        self._record_tree(els)
        self._seeded_tree = els

    def _describe(self) -> list[base.Element]:
        # `_read_source` refreshes `_native_z` for this read, so it is read after, never before.
        root = self._read_source()
        els, identities = elements_with_identities(root, self._native_z)
        self._identities = {id(el): ident for el, ident in zip(els, identities, strict=True)}
        # The tree the identity map above describes. `_device_act` counts an element's peers against
        # *this* list, never against a tree it captured earlier: `_resolve` re-queries on a transient
        # not-found and `_scroll_into_view` re-settles, so the element it hands back can belong to a
        # later read than the one the caller settled — and the map is rebuilt by every read.
        self._last_tree = els
        return els

    def last_raw_source(self) -> base.RawSource | None:
        """The raw dump behind `_last_tree` (`base.RawSourceProvider`), or None before the first read.

        The `parsed_input` half is serialized here rather than on the read, because this accessor is
        the `rawTree` capture's own seam and nothing else calls it: a run that never takes that
        capture — every run by default — never pays to turn the narrowed tree back into a string
        (BE-0407 unit 23).
        """
        if self._raw_reply is None:
            return None
        parsed_input = (
            ET.tostring(self._parsed_root, encoding="unicode")
            if self._parsed_root is not None
            else None
        )
        return base.RawSource(text=self._raw_reply, suffix=".xml", parsed_input=parsed_input)

    def _read_source(self) -> ET.Element | None:
        """The hierarchy tree to parse: the resident channel when available, else `uiautomator dump`.

        Both sources speak UI Automator's own XML, so the parse behind them is unchanged
        (`elements_with_identities`, BE-0245). A resident-channel failure degrades to the dump
        subprocess with a loud warning —
        never silently, so a slower fallback read stays visible — leaving the backend no worse off
        than the dump-every-read path it replaces. The failure latches: the channel is disabled after
        the first fault so the rest of the lease reads via dump without re-logging or re-paying the
        connect timeout on every read. The fetch itself tears the resident server down on that fault
        (`ResidentServer.start`), releasing the device's single UiAutomation session so the dump
        fallback is a clean degrade rather than one poisoned by a wedged-but-alive server.
        """
        if self._fetch_hierarchy is not None:
            # Tell the resident channel which mark to wait past (BE-0332 Unit 4): the pending gesture's
            # actuation mark, so the device blocks until a read postdates it and returns that read in one
            # round trip. None when nothing is pending, so a read that follows no gesture never waits.
            since = self._catchup.actuation_mark if self._catchup is not None else None
            t0 = time.monotonic()
            try:
                read = self._fetch_hierarchy(since)
                self._read_mark = read.mark
                self._native_z = read.native_z
                logger.debug(
                    "resident read in %.2fs: mark %s, blocked on %s",
                    time.monotonic() - t0,
                    f"{read.mark:.0f}" if read.mark is not None else "none",
                    f"{since:.0f}" if since is not None else "nothing",
                )
                if read.mark is None and not self._mark_warned:
                    # A live channel whose reads carry no `X-Bajutsu-Read-Mark` disables the ordering
                    # test silently: `_advance_catchup`'s mark branch can never be satisfied, so every
                    # mark-armed gesture spends the whole `_READ_LAG_S` budget and closes on the timeout
                    # warning instead. That is slow and, worse, indistinguishable from a healthy run in
                    # the log — the timeout message names the benign case too. Say it once, so "the lane
                    # is on the mark path" stops being an assumption.
                    self._mark_warned = True
                    logger.warning(
                        "resident reads carry no %s header; the read-lag barrier cannot use the "
                        "device mark and every armed gesture will spend its full %.1fs budget",
                        "X-Bajutsu-Read-Mark",
                        self._READ_LAG_S,
                    )
                # `read.text` is the device's own reply, untouched — the primary artifact. The tree
                # bajutsu actually parsed becomes the secondary one (`parsed_input`) only where
                # narrowing changed something, and only if a `rawTree` capture asks for it; see
                # `last_raw_source`.
                self._raw_reply = read.text
                self._parsed_root = read.root if read.narrowed else None
            except AdbResidentError as exc:
                logger.warning(
                    "resident hierarchy read failed (%s); falling back to `uiautomator dump` for "
                    "reads and coordinate injection for gestures, for the rest of this lease",
                    exc,
                )
                self._fetch_hierarchy = None
                self._fetch_clock = None  # the clock endpoint shares the dead channel
                # Ditto for `/act`: a connection that cannot serve a read cannot serve an actuation
                # either, so this latches the coordinate degrade here rather than leaving `_device_act`
                # to rediscover the same dead channel — and re-warn — on every gesture for the rest of
                # the lease (BE-0339 Unit 4).
                self._act_unavailable = True
                # The read channel is gone rather than momentarily noisy, so this is the moment the
                # state explaining it still exists (BE-0367). Hooked here, at the propagation site,
                # and never on the act path: `AdbActUnsupported` and `AdbActUncertain` fire during
                # perfectly healthy runs, and capturing there would spend this run's cap before a
                # genuine stall could use it. Off unless the operator opted in.
                stall_diagnostics.capture(
                    "resident-read", stall_diagnostics.device_probes(self.serial)
                )
            else:
                # A body the channel could not narrow (garbled, or a null root mid-transition) comes
                # back with no tree; slicing it here yields None too, and the caller's empty-tree
                # degrade applies exactly as it did when this returned that body as text.
                return read.root if read.root is not None else slice_hierarchy_root(read.text)
        # The dump subprocess carries no event mark, so the barrier reverts to its wall-clock budget,
        # and no measured position either, so every element reports the honest absence.
        self._read_mark = None
        self._native_z = {}
        t0 = time.monotonic()
        text = self._run(adb.dump_cmd(self.serial))
        logger.debug(
            "dump read in %.2fs (no mark: the barrier is on its wall clock)", time.monotonic() - t0
        )
        self._raw_reply = text
        self._parsed_root = None  # already untouched: no narrowing
        return slice_hierarchy_root(text)

    def _record_tree(self, els: list[base.Element]) -> list[base.Element]:
        els = super()._record_tree(els)
        self._tree_current = True  # this read describes the screen as it is now
        self._advance_catchup(els)
        return els

    def _pan_baseline(self) -> StableKey | None:
        """The projection to measure a pan's catch-up against: the screen as it stands right now.

        Read afresh when something has actuated since the last read. A baseline predating that
        actuation already differs from whatever the next read shows, so the first post-pan read would
        move off it, `_advance_catchup` would credit that as the pan being published, and the pan's own
        lag would go unwaited — the fix would silently not apply. The common paths cost nothing extra:
        a directional `swipe` and a `drag` resolve their endpoints from a `query()` in the same step,
        and `_scroll_toward` runs straight after a `_settle`. Only a pan reached with no fresh read
        (the coordinate form `swipe: { from, to }`, or one preceded by a screenshot capture) pays one.

        A pan still waiting to publish is drained first, because re-reading cannot rescue that case: the
        read itself would return the pre-pan screen, so the new baseline would predate the earlier pan
        and the earlier pan's publish would later be mistaken for this one's. Two pans back-to-back is
        not hypothetical — it is the shape of the scenario this fix targets, whose consecutive `swipe`
        steps resolve their endpoints through `query()` and never reach `_settle`, so nothing else
        drains the barrier between them. A single pan pays nothing: `_catchup` is None and
        `_await_catchup` returns at once.
        """
        self._await_catchup()
        if not self._tree_current:
            self.query()
        return self._last_stable_key

    def _capture_mark(self) -> float | None:
        """The device clock (`SystemClock.uptimeMillis`) right now, or None if it cannot be read.

        Taken just before a gesture (BE-0332 Unit 3) so a later read must postdate it — on the device's
        own clock, no host-to-device skew — to count as caught up. None on the dump path (no resident
        clock channel) or an older server without the `/clock` endpoint; the barrier then rests on its
        wall-clock budget, which never accepts a stale read, only waits longer for a fresh one.
        """
        if self._fetch_clock is None:
            return None
        mark = self._fetch_clock()
        if mark is None and not self._clock_warned:
            # The read channel is live (a clock probe was configured) but `/clock` did not answer — an
            # older server without the endpoint, or a one-off fault. The barrier stays correct on its
            # wall-clock budget for each gesture whose mark probe returns None (the probe keeps being
            # attempted on subsequent gestures; this latch only suppresses repeated identical warnings).
            self._clock_warned = True
            logger.warning(
                "resident clock probe returned no mark; read-lag barrier falls back to its "
                "wall-clock budget for gestures where the probe cannot answer"
            )
        return mark

    def _arm_catchup(self, pre_key: StableKey | None, actuation_mark: float | None) -> None:
        """Open a catch-up barrier for the gesture that just fired, anchored on `actuation_mark`.

        Called after the gesture returns, so the budget starts when the content actually stopped. With
        no projection to compare against there is nothing to detect, and nothing is armed. `actuation_mark`
        is the device clock taken before the gesture (BE-0332 Unit 3): when present, `_advance_catchup`
        closes the barrier on the first read that postdates it; when None (the dump path), it falls back
        to the projection-changed-and-dwelt heuristic.
        """
        if pre_key is not None:
            now = time.monotonic()
            self._catchup = _Catchup(
                pre_key, now + self._READ_LAG_S, pre_key, now, actuation_mark, now
            )
            logger.debug(
                "catchup armed: budget %.1fs, %s",
                self._READ_LAG_S,
                (
                    f"device mark {actuation_mark:.0f}"
                    if actuation_mark is not None
                    else f"no mark, dwell {self._CATCHUP_DWELL_S}s on the dump path"
                ),
            )

    def _advance_catchup(self, els: list[base.Element]) -> None:
        """Fold one read into the pending gesture's barrier, closing it once the tree has caught up.

        Runs on **every** read, not only the ones `_await_catchup` issues, so the reads the runner
        already takes between a gesture and the next actuator — a `wait`, an `assert`, a post-step
        capture — close the barrier and a run whose tree keeps up waits for nothing.

        With a device event mark (BE-0332 Unit 3, the resident channel), the answer is exact: the read
        caught up the moment its mark postdates `actuation_mark`. That is a true ordering test — the
        device published an update after the gesture — so it needs no host dwell: the resident reader
        blocks on that same mark (BE-0332 Unit 4, `GET /source?since=`) until it can return a tree the
        gesture has reached. The mark settles *staleness* (the read postdates the gesture); the device
        then closes *tearing* (a dump caught mid-republish) with its own bounded settle before it
        answers, so the two together make the read whole, not merely fresh. A read carrying no mark (the
        channel died back to dump mid-barrier) simply never satisfies it, and the wall-clock deadline in
        `_await_catchup` bounds the wait.

        Without a mark (the `uiautomator dump` fallback), the barrier keeps its earlier heuristic: a
        read counts as caught up only once its projection differs from the pre-gesture one *and* has
        held for `_CATCHUP_DWELL_S`. Differing alone is not enough, because the catch-up is not
        atomic: Android republishes node bounds one node at a time, so a read taken mid-catch-up is
        *torn* (some frames new, the rest still pre-gesture). Closing the barrier on a torn read would
        hand the next actuator a partly-stale tree with no dwell left to ride the tear out, and
        `_settle`'s two-equal-reads poll would accept it — the same failure this fix exists to stop,
        reached by another door. Requiring the dwell is BE-0245's "bound by elapsed time, not read
        count" applied to the catch-up itself. A tear outlasting the dwell would still get through;
        the dwell is sized above the widest one observed, not proven impossible.

        A degenerate read is ignored outright: its projection differs from every real one, so crediting
        it would spend the barrier on a tree the read path is itself still retrying.
        """
        catchup = self._catchup
        if catchup is None or self._is_transient_empty(els):
            return
        if catchup.actuation_mark is not None:
            if self._read_mark is not None and self._read_mark > catchup.actuation_mark:
                self._catchup = None
                # Order confirmed by the device, not merely by the barrier going quiet: only this path
                # sets `_read_ordered`, so `read_postdates_actuation` never mistakes a timed-out or
                # dump-heuristic close for a genuine postdate (BE-0332 Unit 3).
                self._read_ordered = True
                logger.debug(
                    "catchup closed by device mark in %.2fs (read %.0f > actuation %.0f)",
                    time.monotonic() - catchup.armed_at,
                    self._read_mark,
                    catchup.actuation_mark,
                )
            return
        key = self._last_stable_key
        now = time.monotonic()
        if key != catchup.key:
            catchup.key, catchup.since = key, now
        if key != catchup.pre_key and now - catchup.since >= self._CATCHUP_DWELL_S:
            self._catchup = None
            # The dwell is itself a two-observations-apart proof of rest (BE-0245's own rationale for
            # requiring it, not just a changed projection) — `_settle`'s fast path may trust a future
            # match against this key without re-polling. The mark-closing branch above sets no such
            # thing: postdating the actuation proves order, not rest, so a still-moving fling would
            # hand `_settle` a premature "proven" key with no dwell to have caught it.
            self._settled_key = key
            logger.debug("catchup closed by projection dwell in %.2fs", now - catchup.armed_at)

    def _catchup_evidence(self, catchup: _Catchup) -> str:
        """Why the barrier below did not close, in the terms of whichever test it was applying.

        The timeout message names two causes it cannot tell apart, and the numbers here are what
        separates them. On the mark path a newest read mark that never passed the actuation mark says
        the device published no accessibility event at all — the gesture moved nothing, or never
        landed. One that passed it would have closed the barrier, so seeing it here at all is the
        finding. On the dump path the projection either moved or it did not.
        """
        if catchup.actuation_mark is None:
            moved = catchup.key != catchup.pre_key
            return f"the projection, which {'moved but never dwelt' if moved else 'never moved'}"
        newest = self._read_mark
        if newest is None:
            return f"device mark {catchup.actuation_mark:.0f}, and no read carried a mark at all"
        return (
            f"device mark {catchup.actuation_mark:.0f}; the newest read was "
            f"{newest:.0f} ({newest - catchup.actuation_mark:+.0f}ms)"
        )

    def _await_catchup(self) -> None:
        """Re-read until the pending actuation shows in the tree, or its lag budget is spent.

        The one thing the two-consecutive-equal-reads settle below cannot do on its own: a lagging
        Android tree is *self-consistently* lagging, so any number of reads agree with each other and
        agree on the pre-actuation frames. What separates a caught-up read from a stale one is
        `_advance_catchup`'s test, which this drives reads until; the wall-clock budget bounds the wait
        when an actuation legitimately changed no frame (a pan already at the end of the content, or a
        tap that only moved a mirrored value — BE-0332 arms the barrier for center-resolving taps too).
        """
        while (catchup := self._catchup) is not None:
            if time.monotonic() >= catchup.deadline:
                # Loudly, not silently: the actuator may be about to resolve a coordinate from a tree
                # that never published the last actuation, which is the failure whose bare `expect`
                # mismatch cost a full artifact investigation to explain. Both causes are named because
                # the driver cannot tell them apart, and the benign one is routine — an actuation that
                # moves no frame (a pan at the end of the content, or a tap that only changes a mirrored
                # value) never differs the projection, so the barrier can only end here. The message
                # names the actuator neutrally because BE-0332 arms this for taps, not only pans;
                # asserting the lag would send an investigator after a bug that never happened.
                logger.warning(
                    "read lag: the last gesture did not show in the tree within %.1fs — either the "
                    "tree never published it, or it moved no frame (e.g. a pan already at the end of "
                    "the content, or a tap that changed only a mirrored value). Resolving from the "
                    "current screen. Waited on %s",
                    self._READ_LAG_S,
                    self._catchup_evidence(catchup),
                )
                self._catchup = None
                return
            time.sleep(self._SETTLE_POLL_S)
            self.query()  # `_advance_catchup` closes the barrier once the tree has caught up

    def settled_query(self) -> list[base.Element]:
        """A tree fit to resolve an actuation target from (`base.SettledReadProvider`).

        The driver's own actuators reach `_settle` through `_center`; a directional `swipe` and a
        `drag` resolve their anchor above the driver, so without this seam they would anchor on a
        bare `query()` — the one selector-addressed actuation the catch-up barrier never covered.
        """
        return self._settle()

    def _settle(self) -> list[base.Element]:
        """Wait until the tree's identifier-frame projection stops changing, or give up.

        Compares (identifier, frame) only — ignoring volatile value/traits/label — so data changes on
        a static screen do not trigger extra polls. The fast path trusts a match against
        `_settled_key`, never against `_last_stable_key` directly — that cache is overwritten by
        every `query()` call regardless of caller (`wait`'s own poll, a bare `assert`, `_pan_baseline`,
        the not-found retry in `_resolve`, ...), so a bare match against it would let a read that
        merely happened to agree with some unrelated single prior read skip the poll, on no stronger
        evidence than coincidence. `_settled_key` is set only where genuine rest was actually proved:
        here, when this method's own poll sees two consecutive reads agree, and in
        `_advance_catchup`, when its projection-dwell closes a barrier — a real two-observations-apart
        proof by the same logic, just reached through the reads a `wait`/`assert` already took rather
        than a poll of this method's own. It is deliberately *not* set when a catch-up barrier closes
        on a device-mark postdate: that proves order (this read is not stale relative to the gesture),
        not rest (the tree stopped moving) — a fling can keep publishing well past the first read that
        postdates the gesture's mark, so trusting that alone would resurrect the very bug this fast
        path exists to avoid. The poll itself is bounded by a wall-clock deadline, not a fixed read
        count, so it spans a real animation whatever the read costs — the resident channel's fast read
        (BE-0245) would otherwise collapse the window and let a still-moving tree pass as settled.

        A pending pan is waited out first (`_await_catchup`), then the stability poll runs as before.
        Both halves are needed: the first gets past a wholly pre-pan tree, and the second gets past
        the *torn* tree that the catch-up passes through — Android republishes node bounds one node at
        a time, so the read that first differs can still carry most of the old frames.
        """
        self._await_catchup()
        t0 = time.monotonic()
        tree = self.query()
        key = self._last_stable_key
        if self._settled_key is not None and key == self._settled_key:
            return tree
        # From here, not from `t0`: the budget is sized for the *polling* reads, so folding the first
        # read into it would cost the ~2.4s dump path a whole attempt — the shrink the constant's own
        # comment warns against. `t0` stays for the elapsed figure the log reports.
        deadline = time.monotonic() + self._SETTLE_DEADLINE_S
        reads = 1
        while time.monotonic() < deadline:
            time.sleep(self._SETTLE_POLL_S)
            tree = self.query()
            reads += 1
            new_key = self._last_stable_key
            if new_key == key:
                # Proved, not merely observed: this is the first time two consecutive reads have
                # actually agreed, so — unlike `_last_stable_key` — trusting a future match against
                # this key on the fast path above is sound.
                self._settled_key = new_key
                logger.debug("settled after %d reads in %.2fs", reads, time.monotonic() - t0)
                return tree
            key = new_key
        # Louder than the poll's other exits: the actuator about to resolve from this tree is
        # resolving from a screen that was still moving when the budget ran out. Also clears
        # `_settled_key`: the tree never proved stable this round, so a later call must not treat a
        # bare match against whatever it lands on next as already proved either.
        self._settled_key = None
        logger.warning(
            "settle: the tree was still changing after %d reads in %.1fs; resolving from the "
            "latest one",
            reads,
            # Measured, not the budget constant: the first read happens before the deadline starts,
            # so on the ~2.4s dump path the constant would undercount the real wait by that much.
            # The success line above reports the same figure, so the two exits are comparable.
            time.monotonic() - t0,
        )
        return tree

    def drain_actuations(self) -> Drained:
        """The concrete actuations performed since the last drain (`ActuationReporter`)."""
        return self._actuations.drain()

    def _log_coordinate(
        self,
        gesture: str,
        point: base.Point,
        el: base.Element,
        *,
        duration_s: float | None = None,
        scale: float | None = None,
        radians: float | None = None,
    ) -> None:
        """Record a host-injected coordinate aimed at an element the driver resolved itself."""
        self._actuations.record(
            Actuation(
                gesture=gesture,
                via="coordinate",
                unit=_UNIT,
                points=(point,),
                frame=el["frame"],
                target=el["identifier"],
                duration_s=duration_s,
                scale=scale,
                radians=radians,
            )
        )

    def _log_identity(self, gesture: str, el: base.Element, duration_ms: int | None) -> None:
        """Record a device-side actuation: the element it named, and no coordinate (the device chose it)."""
        self._actuations.record(
            Actuation(
                gesture=gesture,
                via="identity",
                unit=_UNIT,
                frame=el["frame"],
                target=el["identifier"],
                duration_s=None if duration_ms is None else duration_ms / 1000,
            )
        )

    def _center(self, sel: base.Selector) -> tuple[base.Point, base.Element]:
        """The target's frame center, plus the element it came from (for the actuation record)."""
        point, _, el = self._center_with_screen(sel)
        return point, el

    def _center_with_screen(
        self, sel: base.Selector
    ) -> tuple[base.Point, base.Point, base.Element]:
        """The target's frame center and the screen extent, both in tree (pixel) coordinates.

        The screen extent lets the sendevent double-tap scale a center into the touch device's raw
        range (BE-0208); it is constant across a scroll, so the settled tree gives it even when the
        target itself was only reached by scrolling. The resolved element travels with them so the
        caller records what it actuated without resolving twice.
        """
        frame, screen, el = self._resolve_frame_and_screen(sel)
        return base.frame_center(frame), screen, el

    def _resolve_frame_and_screen(
        self, sel: base.Selector
    ) -> tuple[base.Frame, base.Point, base.Element]:
        """The target's frame, the screen extent (both in tree pixels), and the resolved element.

        Shared by the center-based actuators (tap / double-tap) and the two-finger gestures (BE-0232),
        which need the frame's size, not just its center — so an occluded `pinch` / `rotate` target
        also raises `ElementNotTappable` here, correctly, though the scroll safety net
        (`_tap_with_recovery`, above the driver) wraps only the tap family in this first slice.
        """
        tree = self._settle()
        try:
            el, tree = self._resolve(sel, timeout=self._RESOLVE_TIMEOUT_S, initial_tree=tree)
        except base.ElementNotFound:
            # Not in the current viewport — scroll toward it and re-query (BE-0210). An ambiguous
            # match still fails fast: only not-found triggers a scroll, so `resolve_unique`'s
            # AmbiguousSelector propagates unchanged. The settled tree seeds the first scroll so it
            # is oriented on stable frames rather than a fresh (possibly mid-transition) read.
            el, tree = self._scroll_into_view(sel, tree)
        # The single most useful line when an actuation lands on nothing: the coordinate it is about
        # to touch, and the tree it came from. A frame from a stale read looks entirely ordinary on
        # its own — it is only wrong relative to where the content actually is.
        logger.debug("resolved %r to frame %s of %d elements", sel, el["frame"], len(tree))
        # Document order as a paint-order proxy (`base.topmost_at_point`): correct for Compose's
        # `zIndex` and the ordinary undecorated case on either toolkit; can misjudge a View-based
        # layout that uses `elevation`, which reorders drawing without reordering the accessibility
        # tree (measured on-device during this feature's design spike, not merely theorized).
        base.raise_if_covered(tree, el, sel)
        return el["frame"], screen_size_from_elements(tree), el

    def is_tappable(self, sel: base.Selector) -> bool:
        """Whether `sel` resolves to a unique element that is not covered by another.

        Enforces the same document-order proxy `_resolve_frame_and_screen` uses at actuation
        time, but deliberately as its own settled read rather than a call through that method:
        that method's not-found path calls `_scroll_into_view`, which actually scrolls — routing
        this pure query through it would let a single call silently move the screen, which
        `scroll_until_tappable`'s stop predicate (called repeatedly, once per scroll step) would
        then double-count against its own step bound. Not found means "not tappable" (`False`),
        matching every other backend's convention for a target not yet in the tree; an ambiguous
        selector still raises `AmbiguousSelector`, since occlusion is a different question from
        selector ambiguity.

        Never actuates and never has a fixed sleep, so it is safe to call repeatedly with no side
        effects — but it is not a bare single-shot read either: `_settle()` below is a bounded
        catch-up read plus a stability poll (up to `_SETTLE_DEADLINE_S`), and `_resolve` carries its
        own bounded retry (`_RESOLVE_TIMEOUT_S`) for a target that is only momentarily absent. Both
        bounded waits are deliberate, not incidental, so a caller polling this in a loop (the
        recovery path above) pays real, if bounded, time on top of what each scroll step already
        settles for.
        """
        tree = self._settle()
        try:
            el, tree = self._resolve(sel, timeout=self._RESOLVE_TIMEOUT_S, initial_tree=tree)
        except base.ElementNotFound:
            return False
        return base.topmost_at_point(tree, base.frame_center(el["frame"]), el) is None

    def _scroll_into_view(
        self, sel: base.Selector, tree: list[base.Element]
    ) -> tuple[base.Element, list[base.Element]]:
        """Scroll toward `sel` and re-query, bounded by `_SCROLL_RETRIES`, then fail deterministically.

        A condition wait, not a fixed sleep: each attempt swipes once (default up), then re-reads
        via `_settle` so the scroll's fling has stopped before the tree is resolved (a bare read
        right after the swipe can miss an element still sliding in, over-scrolling past it), and
        retries the unique resolve. A selector that never renders still raises ElementNotFound.

        Returns the tree the resolution succeeded against alongside the element, so a caller that
        also needs the current (post-scroll) screen — not the pre-scroll one it seeded this call
        with — never has to re-query for it.
        """
        for attempt in range(1, self._SCROLL_RETRIES + 1):
            self._scroll_toward(tree)
            tree = self._settle()
            try:
                el = base.resolve_unique(tree, sel)
            except base.ElementNotFound:
                logger.debug(
                    "scroll %d/%d: %r still not in the tree", attempt, self._SCROLL_RETRIES, sel
                )
                continue
            logger.debug(
                "scroll %d/%d brought %r into the tree", attempt, self._SCROLL_RETRIES, sel
            )
            return el, tree
        raise base.ElementNotFound(f"一致なし（scroll しても見つからず）: {sel!r}")

    def _scroll_toward(self, tree: list[base.Element]) -> None:
        w, h = screen_size_from_elements(tree)
        if w <= 0 or h <= 0:
            # A degenerate/empty tree gives no screen extent to swipe across; a zero-length or
            # edge-column swipe would be a silent no-op that burns the retry budget and then fails
            # with a misleading "not found after scroll". Fail loudly with the real cause (BE-0210).
            raise base.ElementNotFound("scroll 不可（要素ツリーが空。UI Automator が要素を返さず）")
        cx = w / 2
        self.swipe((cx, h * self._SCROLL_FROM_FRAC), (cx, h * self._SCROLL_TO_FRAC))

    def _actuate_centered(self, args: list[str]) -> None:
        """Actuate a command whose target was just resolved, then open a read-lag barrier for it.

        A center-resolving tap can change the layout (open a menu, expand a row, advance a stepper),
        and Android publishes that update a beat after the actuation returns — so without a barrier the
        next actuator's `_settle` accepts the still-pre-tap tree and resolves against stale frames, the
        `gestures` long-press flake (BE-0332 Unit 2). The just-resolved tree is the baseline, so no
        extra read is paid; a tap that moves nothing visible spends the budget exactly as a pan at the
        end of the content does. `tap_point` is deliberately excluded: it resolves no selector, so it
        has no target-from-a-layout to postdate, and arming there would steal a following pan's fresh
        baseline (`_pan_baseline`).

        Precondition: the caller has just resolved its target through `_center*` → `_settle`, which
        both drained any outstanding prior barrier (`_await_catchup`) and populated `_last_stable_key`
        with the layout resolved against. That is why the baseline is `_last_stable_key` directly, not
        a fresh `_pan_baseline()` read — the resolve already paid for both. A center actuator wired here
        without that preceding resolve would leave `_last_stable_key` None and silently arm nothing.
        """
        pre_key = self._last_stable_key
        mark = self._capture_mark()
        # The coordinate the device path exists to avoid computing this far ahead of the touch. Paired
        # with the `resolved … to frame` line above, this is the whole of "where did it actually tap".
        logger.debug("coordinate injection: %s", " ".join(args[-5:]))
        self._act(args)
        self._arm_catchup(pre_key, mark)

    # How many times a `stale` reply is answered by re-resolving before the gesture falls back to the
    # coordinate path. `stale` means the screen moved between the host's resolve and the device's, so a
    # re-read is the fix and usually succeeds at once; a target that keeps moving is a moving target,
    # not a channel fault, so the attempts are few. Mirrors the XCUITest channel's own bound (BE-0289).
    _STALE_MAX_ATTEMPTS = 3

    def _device_act(self, sel: base.Selector, kind: str, duration_ms: int | None = None) -> bool:
        """Perform `kind` on `sel` device-side, or return False to leave it to the coordinate path.

        Resolution stays here: `_resolve_frame_and_screen` settles the tree and `resolve_unique` picks
        the element, so an ambiguous selector still fails immediately and a not-found one still scrolls
        into view — the determinism core is untouched. Only the *coordinate* moves to the device, which
        reads the element's bounds from its own dump microseconds before injecting.

        Returns False, never raising, on every *infrastructure* reason the device path cannot serve
        this gesture: no channel, an element whose identity this read did not record, a `stale` reply
        that re-resolving did not settle, or a channel fault. The caller then injects a coordinate
        exactly as before, so a device without the endpoint is no worse off than one that never had
        it. It does still raise `ElementNotTappable` — a real test outcome, not an infrastructure
        fallback — when the resolved target is covered; see below.

        This is the resident channel's own resolution, distinct from `_resolve_frame_and_screen`'s (the
        coordinate-path fallback below): both enforce the same tappability check, since a resident
        channel being available — the common case — must not silently exempt an occluded target from
        it, only the *coordinate* moves to the device.
        """
        if self._act_fn is None or self._act_unavailable:
            return False
        for _ in range(self._STALE_MAX_ATTEMPTS):
            tree = self._settle()
            try:
                el, tree = self._resolve(sel, timeout=self._RESOLVE_TIMEOUT_S, initial_tree=tree)
            except base.ElementNotFound:
                el, tree = self._scroll_into_view(sel, tree)
            base.raise_if_covered(tree, el, sel)
            identity = self._identities.get(id(el))
            if identity is None:
                # A seeded read: this driver never parsed that tree, so it recorded no identity to
                # address the element by. Nothing to send, so the coordinate path takes the gesture.
                logger.debug(
                    "device %s: %r came from an unparsed tree; coordinates it is", kind, sel
                )
                return False
            same = [e for e in self._last_tree if self._identities.get(id(e)) == identity]
            index = next((i for i, e in enumerate(same) if e is el), None)
            if index is None:
                logger.debug(
                    "device %s: %r outlived the read its peers were counted from", kind, sel
                )
                # `el` outlived the read its peers were counted from. Rather than send an ordinal
                # measured against the wrong screen, leave this gesture to the coordinate path.
                return False
            if self._act_unavailable:
                # Every read between the entry guard and here — `_settle`, a not-found retry inside
                # `_resolve`, `_scroll_into_view` — goes through `_read_source`, which can itself
                # discover a dead resident channel mid-loop and latch this (BE-0339 Unit 4). One check
                # right before the send, rather than one after each read, covers every such window:
                # nothing between the entry guard and here needs the channel, only the send does. Left
                # unchecked, the request would go out to a connection the driver just tore down, fault,
                # and log a "the channel stays in use" warning that contradicts the latch just set.
                return False
            pre_key = self._last_stable_key
            # Two different marks, conflated into one until BE-0407 unit 16. `since` is what the
            # device's own pre-injection read must postdate — the *previous* gesture's mark, the same
            # value `_read_source` passes — and `mark` anchors the barrier armed for *this* gesture,
            # so it is the clock as of now. Sending `mark` for both asked the device to wait for an
            # event newer than the instant the request was built: on a settled screen no such event
            # can exist, so every gesture spent the server's whole `POSTDATE_BUDGET_MS`. Measured on
            # an API 34 emulator: 2217ms and 2042ms with the fresh mark, 64ms and 62ms with the
            # pending one — the wait is spent before the staleness check, so a `stale` reply paid it
            # too. Nothing is weakened: `_settle` above has already drained any pending barrier, the
            # device still settles its own bounds read, and the value sent is exactly the one the
            # endpoint's contract asks for.
            request = ActRequest(
                kind=kind,
                identity=identity,
                index=index,
                count=len(same),
                since=self._catchup.actuation_mark if self._catchup is not None else None,
                duration_ms=duration_ms,
            )
            mark = self._capture_mark()
            # Recorded per attempt, before the endpoint answers, so a declined or faulted request still
            # shows what it aimed at — and a gesture that then falls back records the coordinate
            # injection after this, in the order the two happened. The device picks the touch point
            # from its own dump, so there is no point to state here; `target` is the *normalized*
            # identifier, never the `NodeIdentity` tuple sent over the wire, whose `content-desc` and
            # `text` components can hold a resolved `${secrets.*}` (see `actuation.py`, rule 3).
            self._log_identity(kind, el, duration_ms)
            try:
                outcome = self._act_fn(request)
            except AdbActUnsupported as exc:
                self._actuations.settle(False)
                # Permanent for this lease: stop probing, so the degrade costs one round trip rather
                # than one per gesture (BE-0234).
                self._act_unavailable = True
                if not self._act_warned:
                    self._act_warned = True
                    logger.warning(
                        "resident actuation unavailable (%s); falling back to coordinate injection, "
                        "which resolves a target a round trip before it is touched",
                        exc,
                    )
                return False
            except AdbActUncertain as exc:
                # The record is deliberately left unsettled here, so `accepted` stays None — "the
                # driver could not tell", the honest reading of a reply that never arrived.
                # The request went out and the device injects before it answers, so a coordinate
                # injection here could be the *second* touch. Treat the gesture as having happened and
                # arm the barrier for it: if it did not, the step's own condition wait fails loudly on
                # a real assertion, where an extra tap could navigate the screen out from under the
                # rest of the scenario. Failing by doing less is the recoverable direction.
                logger.warning(
                    "resident actuation may or may not have landed (%s); continuing as if it did "
                    "rather than injecting a coordinate on top of it",
                    exc,
                )
                self.invalidate_settled_cache()
                self._arm_catchup(pre_key, mark)
                return True
            except AdbResidentError as exc:
                # A blip on the *act* call specifically — distinct from `_read_source`'s own
                # `AdbResidentError`, which does latch the read channel off for the rest of the lease
                # (BE-0339 Unit 4), because there the failing call *is* the shared connection reads
                # depend on too. This one is scoped to `/act`: a socket write or response glitch on
                # this single request says nothing about whether the next read, or the next act call,
                # will succeed. Degrade this one gesture and keep the channel; latching here would hand
                # every later gesture back to the coordinate path precisely when the device is least
                # settled, on evidence that does not support it.
                self._actuations.settle(False)
                logger.warning(
                    "resident actuation faulted (%s); this gesture falls back to coordinate "
                    "injection, the channel stays in use",
                    exc,
                )
                return False
            self._actuations.settle(outcome.acted)
            if outcome.acted:
                logger.debug(
                    "device %s on %r: identity %r, %d of %d", kind, sel, identity, index, len(same)
                )
                # The gesture happened on the device, so the cached tree is stale whichever branch
                # follows — the same bookkeeping `_act` does for a coordinate injection.
                self.invalidate_settled_cache()
                if outcome.published_mark is not None and (
                    mark is None or outcome.published_mark > mark
                ):
                    # The device followed its own gesture to the accessibility event that published it
                    # before answering (BE-0339 Unit 5), so the next read cannot describe the
                    # pre-gesture screen and there is nothing left for a barrier to wait out. Skipping
                    # it is worth a read: `_settle` would otherwise open with `_await_catchup`'s poll
                    # sleep plus a whole extra `query()`, the dominant per-step cost on this backend
                    # (BE-0234).
                    #
                    # The mark is compared, not merely counted. `mark` and `published_mark` are both
                    # `SystemClock.uptimeMillis` readings, so the ordering the header claims is
                    # checkable here — and a reply whose mark does not actually postdate the gesture
                    # (a server that repurposes the header, a value carried over from an earlier
                    # injection) would otherwise disable the barrier on the strength of the header
                    # merely existing. `mark is None` is the older server with no `/clock` endpoint:
                    # nothing to compare against, so the presence of a publish is all there is to go
                    # on, exactly as the barrier itself falls back to its wall-clock budget there.
                    #
                    # The claim is the device's, never this driver's assumption. A first pass at this
                    # unit asserted the resident session synchronized with the platform's idle state
                    # and stopped arming on that basis; it does not, and a coordinate-resolving
                    # follower (`pinch`, `rotate`, a directional `swipe`/`drag` anchor) has no `stale`
                    # re-resolve to self-heal with — see BE-0339's Progress log. Only a mark the device
                    # actually observed clears the barrier now, so an endpoint that cannot confirm
                    # falls through to the branch below rather than being taken at its word.
                    logger.debug(
                        "device %s on %r: publish confirmed at %.0f; no catchup barrier armed",
                        kind,
                        sel,
                        outcome.published_mark,
                    )
                    # The same confirmation licenses adopting the tree the reply carried, when it
                    # carried one (BE-0407 unit 19) — so the read `_settle` opens with is already done.
                    self._seed_from_act(outcome.read, mark)
                else:
                    self._arm_catchup(pre_key, mark)
                return True
            logger.debug("device %s on %r: the device called it stale; re-resolving", kind, sel)
        logger.warning(
            "resident actuation reported the target moved on every attempt; falling back to "
            "coordinate injection for this gesture"
        )
        return False

    def tap(self, sel: base.Selector) -> None:
        if self._device_act(sel, "tap"):
            return
        (x, y), el = self._center(sel)
        self._log_coordinate("tap", (x, y), el)
        self._actuate_centered(adb.tap_cmd(self.serial, x, y))

    def tap_point(self, p: base.Point) -> None:
        self._actuations.record(Actuation(gesture="tap", via="coordinate", unit=_UNIT, points=(p,)))
        self._act(adb.tap_cmd(self.serial, p[0], p[1]))

    def double_tap(self, sel: base.Selector) -> None:
        # Unlike the other actuators, this one goes to the device for its *timing*, not its
        # coordinate. Every host recipe below leaves the gap between the two taps to something
        # incidental and bets it lands inside the platform's double-tap window; the device builds the
        # `MotionEvent`s itself and states the interval. Two in-process `UiDevice.click` calls were
        # tried first and failed the same way the host recipes do — `click` settles between them — so
        # the endpoint now stamps the events rather than chaining a convenience API (BE-0339).
        if self._device_act(sel, "doubleTap"):
            return
        # adb has no native double-tap. `input tap ; input tap` chains both taps in one round-trip,
        # but each `input` starts a JVM, so the gap still overruns the platform's double-tap window
        # (BE-0210). On a rooted device with a discoverable touchscreen, a raw `sendevent` sequence
        # narrows that gap to five process spawns (BE-0208), though a loaded host can still miss the
        # window and land the touches as two single taps. Both stay as the degraded path for a device
        # with no resident channel.
        point, screen, el = self._center_with_screen(sel)
        # The tree-space point, not the raw touch-device range `scale_to_touch` maps it into below: the
        # raw range is an artifact of the injection method, and recording it would make two double-taps
        # on the same element look like different coordinates.
        self._log_coordinate("doubleTap", point, el)
        dev = self._touch_device() if self._rooted() else None
        if dev is not None:
            raw_x, raw_y = adb.scale_to_touch(point, screen, dev)
            cmd = adb.sendevent_double_tap_cmd(self.serial, dev.path, raw_x, raw_y)
        else:
            cmd = adb.double_tap_cmd(self.serial, point[0], point[1])
        self._actuate_centered(cmd)

    def _rooted(self) -> bool:
        """Whether adbd runs as root (`id -u` is 0), cached — a precondition for `sendevent`."""
        if self._is_root is None:
            try:
                self._is_root = self._run(adb.id_u_cmd(self.serial)).strip() == "0"
            except (subprocess.CalledProcessError, OSError):
                self._is_root = False
        return self._is_root

    def _touch_device(self) -> adb.TouchDevice | None:
        """The touchscreen node from `getevent -lp`, probed once and cached (None if none / failure)."""
        if not self._touch_probed:
            self._touch_probed = True
            try:
                self._touch_dev = adb.parse_touch_device(
                    self._run(adb.getevent_probe_cmd(self.serial))
                )
            except (subprocess.CalledProcessError, OSError):
                self._touch_dev = None
        return self._touch_dev

    def long_press(self, sel: base.Selector, duration: float) -> None:
        ms = round(duration * 1000)
        if self._device_act(sel, "longPress", duration_ms=ms):
            return
        # `input` has no press-and-hold, so a zero-length swipe with a duration acts as a long press.
        (x, y), el = self._center(sel)
        self._log_coordinate("longPress", (x, y), el, duration_s=duration)
        self._actuate_centered(adb.swipe_cmd(self.serial, x, y, x, y, ms))

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        pre_key = self._pan_baseline()
        mark = self._capture_mark()
        self._actuations.record(
            Actuation(gesture="swipe", via="coordinate", unit=_UNIT, points=(frm, to))
        )
        self._act(adb.swipe_cmd(self.serial, frm[0], frm[1], to[0], to[1]))
        self._arm_catchup(pre_key, mark)

    def viewport(self) -> base.Point:
        # The true display size in raw pixels (BE-0326). A lazy list (RecyclerView / LazyColumn) keeps
        # a few buffered rows either side of the viewport in the a11y tree, so
        # `screen_size_from_elements` overshoots the screen and the `scroll` stop condition would
        # misjudge an off-screen center as on-screen; `wm size` reports the real display. Cached: the
        # resolution is fixed for a run.
        if self._screen is None:
            self._screen = _parse_wm_size(self._run(adb.wm_size_cmd(self.serial)))
        return self._screen

    def read_lag(self) -> float:
        # How long a read may describe the screen as it was before the last gesture (BE-0326). Android
        # publishes the accessibility update *after* the scroll has moved the content, so a `query()`
        # taken in between can return the pre-scroll tree even though the content already moved.
        # `waitForIdle` alone does not close that window (BE-0245) — the queue looks idle before the
        # update lands — so the `scroll` loop is told to keep re-reading rather than call the first
        # unchanged read the end of content. This budget is the ceiling for reads that carry no device
        # mark (the `uiautomator dump` fallback); the resident channel now closes the window exactly
        # with the mark (BE-0332 Unit 4). Only ever spent on a region that looks stopped, never on a
        # step that landed.
        return self._READ_LAG_S

    def read_postdates_actuation(self) -> bool:
        """Whether a read has postdated the last actuation's device mark (BE-0332 Unit 3).

        True only once `_advance_catchup` has closed a mark-anchored barrier on a read whose event mark
        postdates the mark taken before the gesture — a positive confirmation from the device, reset by
        the next actuation. It is deliberately *not* `self._catchup is None`: that would also read true
        when the barrier timed out on a tree that never caught up, when it closed on the dump-path
        heuristic (no mark to confirm order), or when the actuation armed no barrier at all (`type_text`,
        `back`, `tap_point`, the dump path).

        **No production caller reads this today.** The `extract` poll used to, releasing early on a
        confirmed order, until that release was found to accept a stale value: the mark says an
        accessibility event postdates the gesture, not that the property being copied out has been
        republished (`_settle_extract_read` says why at length). The driver's own catch-up barrier is
        the remaining ordering consumer, and it reads `_read_mark` directly in `_advance_catchup` rather
        than through here. Narrowing that barrier (BE-0339 Unit 5) has since landed without needing this
        seam either: a gesture whose publish the device confirmed arms no barrier at all, so it never
        sets `_read_ordered`. Nothing checks the flag either — the conformance suite's marked-read case
        (`driver_conformance.py::test_a_read_postdates_a_content_moving_gesture`) deliberately asserts
        the observable ordering instead, because this flag is legitimately false whenever a barrier
        closes on its budget. So the protocol stays declared for the `ReadOrderProvider` contract
        alone: a live contract with neither a live caller nor a test, kept because retiring it is a
        decision about the contract rather than about this driver.
        """
        return self._read_ordered

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        # A non-inertial pan (BE-0326): `input swipe` over a longer duration than the default drag
        # keeps the list moving with the finger and stopping when the gesture ends, so the scroll
        # leaves no fling momentum. A short swipe over the same distance flings — its post-lift
        # travel varies by device, which is exactly the non-determinism the `scroll` action removes.
        pre_key = self._pan_baseline()
        mark = self._capture_mark()
        duration_ms = self._scroll_duration_ms(frm, to)
        self._actuations.record(
            Actuation(
                gesture="scroll",
                via="coordinate",
                unit=_UNIT,
                points=(frm, to),
                duration_s=duration_ms / 1000,
            )
        )
        self._act(adb.swipe_cmd(self.serial, frm[0], frm[1], to[0], to[1], duration_ms))
        self._arm_catchup(pre_key, mark)

    def _scroll_duration_ms(self, frm: base.Point, to: base.Point) -> int:
        """How long this pan should take, so its speed is the same whatever distance it covers."""
        distance = math.hypot(to[0] - frm[0], to[1] - frm[1])
        return max(
            self._SCROLL_MIN_DURATION_MS,
            round(distance / self._SCROLL_SPEED_PX_PER_S * 1000),
        )

    def back(self) -> None:
        # The true system back: a KEYCODE_BACK key event. Android has no on-screen "back" element to
        # tap (unlike iOS's OS back button), so this is a key event, not a coordinate — BE-0210.
        self._actuations.record(Actuation(gesture="back", via="key", unit=_UNIT))
        self._act(adb.keyevent_cmd(self.serial, adb.KEYCODE_BACK))

    def pinch(self, sel: base.Selector, scale: float) -> None:
        # Two contacts spread from / close to the target centre by `scale`, driven as a raw two-slot
        # `sendevent` sweep (BE-0232) — the machinery the double-tap established, one slot to two.
        self._two_finger_gesture(
            sel,
            "pinch",
            lambda c, half: adb.pinch_contacts(c, half, scale),
            scale=scale,
        )

    def rotate(self, sel: base.Selector, radians: float) -> None:
        # Two contacts sweep a diameter of the target through `radians` about its centre (BE-0232).
        self._two_finger_gesture(
            sel,
            "rotate",
            lambda c, half: adb.rotate_contacts(c, half, radians),
            radians=radians,
        )

    def _two_finger_gesture(
        self,
        sel: base.Selector,
        action: str,
        contacts: Callable[
            [base.Point, float], tuple[tuple[base.Point, base.Point], tuple[base.Point, base.Point]]
        ],
        *,
        scale: float | None = None,
        radians: float | None = None,
    ) -> None:
        """Drive a two-finger gesture: resolve the target, then emit the raw two-slot sweep (BE-0232).

        A rooted device with a discoverable touchscreen is required. Unlike the double-tap there is no
        single-touch approximation of two fingers, so a missing precondition fails loudly with a clear
        `UnsupportedAction` naming the root requirement — never a degraded gesture that silently passes.
        """
        if not self._rooted():
            raise base.UnsupportedAction(
                f"{action} は rooted device が必要; 二本指ジェスチャに単一タッチの代替は無い"
                "（sendevent で /dev/input に書き込むため root が要る）"
            )
        dev = self._touch_device()
        if dev is None:
            raise base.UnsupportedAction(
                f"{action} 不可（touchscreen node が getevent に見つからず、二本指の接点を撃てない）"
            )
        frame, screen, el = self._resolve_frame_and_screen(sel)
        # gesture_anchor keeps both fingers (and a ~2x pinch-out) inside the target (BE-0251).
        cx, cy, half = base.gesture_anchor(frame)
        if half <= 0:
            # A zero-size frame collapses both contacts onto the centre — a zero-travel sequence the
            # platform reads as a tap, not a gesture, so the mirrored value never flips and the wait
            # times out with a misleading cause. Fail loudly with the real one, as `_scroll_toward`
            # does for a degenerate screen extent (BE-0232).
            raise base.UnsupportedAction(
                f"{action} 不可（対象の frame が退化しており二本指の接点を配置できない）: {sel!r}"
            )
        start, end = contacts((cx, cy), half)
        raw_start = (
            adb.scale_to_touch(start[0], screen, dev),
            adb.scale_to_touch(start[1], screen, dev),
        )
        raw_end = (adb.scale_to_touch(end[0], screen, dev), adb.scale_to_touch(end[1], screen, dev))
        # A zoom or a rotation moves every frame on screen just as a pan does, so it carries the same
        # publish lag and takes the same catch-up barrier. `_resolve_frame_and_screen` above already
        # read the tree, so the baseline costs nothing here.
        pre_key = self._pan_baseline()
        mark = self._capture_mark()
        # The anchor in tree space, not the raw touch-device range: `gesture_anchor`'s rule plus the
        # frame and the scale/rotation fully determine the two contacts from it, and the raw range is
        # an artifact of the injection method (as it is for the sendevent double-tap).
        self._log_coordinate(action, (cx, cy), el, scale=scale, radians=radians)
        self._act(adb.sendevent_gesture_cmd(self.serial, dev.path, raw_start, raw_end))
        self._arm_catchup(pre_key, mark)

    def select_option(self, sel: base.Selector, option: str) -> None:  # noqa: ARG002  # Driver shape
        raise base.UnsupportedAction(
            "selectOption は <select> を持つ web バックエンド専用; Android ネイティブに <select> はない"
        )

    def set_picker_value(self, sel: base.Selector, value: str) -> None:  # noqa: ARG002  # Driver shape
        raise base.UnsupportedAction(
            "setPickerValue は iOS の picker wheel 専用; Android に相当するコントロールはない"
        )

    def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:  # noqa: ARG002  # Driver shape
        # BE-0316 is iOS-only: Android surfaces a system permission dialog in the topmost-window
        # dump, so an ordinary `tap` already reaches it. Preflight rejects the step before any device
        # work (adb never advertises HANDLE_SYSTEM_ALERT); this is the mid-run backstop.
        raise base.UnsupportedAction(
            "handleSystemAlert は iOS 専用; Android のシステムダイアログは通常の tap で操作できる"
        )

    def enter_app(self, bundle_id: str) -> None:  # noqa: ARG002  # Driver shape
        # app: は XCUITest backend 専用。preflight がデバイス側の作業に入る前にこのシナリオ
        # を弾くので、これはmid-runのbackstopにすぎない。
        raise base.UnsupportedAction("app は iOS XCUITest 専用; Android には対応する仕組みがない")

    def leave_app(self) -> None:
        raise base.UnsupportedAction("app は iOS XCUITest 専用; Android には対応する仕組みがない")

    def system_alert_labels(self) -> list[str]:
        # No SpringBoard on Android; the reactive guard's native path never runs here (BE-0315).
        return []

    def notification_banner_frame(self) -> base.Frame | None:
        # No SpringBoard on Android; a heads-up notification reaches the app's own tree, unlike an
        # iOS foreground banner, so this backend never advertises HANDLE_NOTIFICATION_BANNER (BE-0416).
        return None

    def dismiss_blocking_tip(self, tree: list[base.Element] | None = None) -> bool:  # noqa: ARG002  # Driver shape
        # TipKit is an iOS framework. Android's nearest equivalents (`TooltipCompat`, Compose
        # Material3 tooltips) are per-app widgets with no shared tree shape, so they stay BE-0314
        # `interrupts` territory rather than a built-in guard.
        return False

    def type_text(self, text: str) -> None:
        # `text` is deliberately absent from the record — not even its length (see `actuation.py`).
        self._actuations.record(Actuation(gesture="typeText", via="focused", unit=_UNIT))
        # Feed the `input text` command to `adb shell` over stdin, not on the argv, so a secret / OTP
        # never lands in the adb process command line where `ps` could read it (BE-0155). Routed
        # through a class-level attribute so tests can patch it.
        # `_run_text` bypasses `_act`, so invalidate by hand: the input may have just changed the
        # screen exactly as any other actuation would.
        self.invalidate_settled_cache()
        self._run_text(adb.shell_cmd(self.serial), adb.text_script(text))

    @staticmethod
    def _run_text(cmd: list[str], script: str) -> None:
        # BE-0415: reads the ambient trace directly (this is a plain staticmethod, so it has no
        # instance to be wrapped through at construction time like `_run`/`_fetch_hierarchy`/etc.).
        ctx = tracing.current_trace()
        if ctx is None:
            subprocess.run(cmd, input=script, capture_output=True, text=True, check=True)
            return
        started_at = time.time()
        t0 = time.perf_counter()
        try:
            subprocess.run(cmd, input=script, capture_output=True, text=True, check=True)
        finally:
            ctx.record("subprocess", " ".join(cmd), started_at, time.perf_counter() - t0)

    def delete_text(self, count: int) -> None:
        # `count` backspaces (KEYCODE_DEL) in one `input keyevent` call. The orchestrator focuses the
        # field first, so the deletes land in it (BE-0265).
        self._actuations.record(Actuation(gesture="deleteText", via="focused", unit=_UNIT))
        self._act(adb.keyevents_cmd(self.serial, [adb.KEYCODE_DEL] * count))

    def select_all(self) -> None:
        # Ctrl+A selects the focused field's whole content (BE-0265).
        self._actuations.record(Actuation(gesture="selectAll", via="focused", unit=_UNIT))
        self._act(adb.keycombination_cmd(self.serial, [adb.KEYCODE_CTRL_LEFT, adb.KEYCODE_A]))

    def copy_selection(self) -> None:
        # Ctrl+C copies the active selection to the clipboard, read back by the `clipboard` assertion.
        self._actuations.record(Actuation(gesture="copy", via="focused", unit=_UNIT))
        self._act(adb.keycombination_cmd(self.serial, [adb.KEYCODE_CTRL_LEFT, adb.KEYCODE_C]))

    def screenshot(self, path: str) -> None:
        adb.Env(self.serial, run=self._run).screenshot(path)

    def screenshot_in_background(self, path: str) -> Callable[[], None]:
        """Start `screenshot` on a worker thread; the returned join waits for the PNG to land.

        `base.BackgroundScreenshotProvider`'s side of the overlap. It runs the very same `screenshot`
        rather than a second `screencap` call of its own, so the binary-capture seam
        (`adb.Env._run_capture`) a test patches is still the one path pixels take.

        Nothing here needs a lock: `screenshot` builds a throwaway `adb.Env` and shells out, reading
        `self.serial` and `self._run` and touching no mutable driver state — unlike every other call
        on this driver, which is why the overlap is safe on this backend alone. A failure crosses the
        thread boundary and is re-raised, unchanged, out of the join, so the caller fails exactly as it
        would have; the thread is a daemon so a caller that dies before joining cannot wedge the
        interpreter on it.

        BE-0415: records its own `subprocess` entry from inside the thread, rather than relying on
        `TracingDriver`'s generic per-call wrap. That wrap can only time the call it wraps — here,
        starting the thread and returning `join`, which takes microseconds — not the actual
        `screencap` capture, which happens afterwards, overlapped with the rest of the same step. The
        thread also runs inside a copy of the *calling* thread's `contextvars.Context`: a bare
        `threading.Thread` starts its target in a fresh, empty context, so `tracing.current_trace()`
        would otherwise see no open trace here even while one is open on the caller's thread.
        """
        failure: list[Exception] = []

        def run() -> None:
            ctx = tracing.current_trace()
            started_at = time.time()
            t0 = time.perf_counter()
            try:
                self.screenshot(path)
            except Exception as exc:
                # Deliberately broad, and not a swallow: whatever the synchronous `screenshot` would
                # have raised is carried across the thread boundary and re-raised verbatim by `join`.
                failure.append(exc)
            finally:
                if ctx is not None:
                    ctx.record(
                        "subprocess",
                        "screenshot_in_background",
                        started_at,
                        time.perf_counter() - t0,
                    )

        thread = threading.Thread(
            target=contextvars.copy_context().run,
            args=(run,),
            name="bajutsu-adb-screenshot",
            daemon=True,
        )
        thread.start()

        def join() -> None:
            thread.join()
            if failure:
                raise failure[0]

        return join

    def driver_interval(self, kind: str, path: Path) -> intervals.Interval | None:
        """A whole-scenario interval recording via adb, or None for an unsupported kind.

        The device pool hands this to the `FileSink` so the same backend-independent `capture` policy
        that drives the simctl providers on iOS drives the adb ones here — Android is not `simctl`, so
        it routes through this driver-supplied seam rather than the sink's simctl path (the iOS backend,
        which has no such method, leaves the seam None and takes the simctl path). `video` records via
        `screenrecord` (pulled off the device on stop); `deviceLog` streams `logcat`. `appTrace` has
        no adb analogue, so it returns None.
        """
        if kind == "video":
            # Unreachable through the device pool today — `AndroidEnvironment` always prestarts
            # (`records_video_up_front` is True), so the sink adopts that running interval instead of
            # reaching this on-demand path. Confirmed anyway, for a caller that reaches this driver
            # directly: an on-demand start deserves the same true_start confirmation the prestarted
            # path gets, not a silent regression to the pre-fix drift.
            return intervals.start_screenrecord(
                self.serial, path, run=self._run, confirm_started=True
            )
        if kind == "deviceLog":
            return intervals.start_logcat(self.serial, path)
        return None

    # No semantic tap and no native network monitoring — the lean end of the capability model.
    # Of the device-control family it advertises only `setLocation` + `clipboard`:
    # `setLocation` over the emulator console (BE-0211), `clipboard` over an ordered `am broadcast`
    # to the app's in-app receiver (BajutsuAndroid, BE-0233); adb declares it because the backend can
    # drive it given a cooperating app. The
    # per-operation tokens (BE-0212) let it declare exactly that subset, so preflight admits those
    # steps and fails the rest fast. A class constant so the preflight (BE-0082) reads it via
    # `backends.capabilities_for` with no device. `multiTouch` is declared statically here too, so
    # `gestures_multitouch` is admitted on adb; the rooted-device precondition for the two-finger
    # `sendevent` sweep is enforced at actuation time (`_two_finger_gesture`), not in the set, so on a
    # non-rooted device the gesture step fails fast with a clear `UnsupportedAction` (BE-0232).
    # `network` is deliberately NOT declared here even though adb captures traffic (BE-0283): that
    # token means *native* driver observation (only Playwright has it), and `capability_preflight`
    # leaves `network` ungated precisely because the app-side collector satisfies it without a backend
    # advertising it — the same accommodation the iOS backend relies on. Declaring it would wrongly claim native
    # observation and is not needed for a `request` assertion to run on adb.
    CAPABILITIES = (
        frozenset(
            {
                base.Capability.QUERY,
                base.Capability.ELEMENTS,
                base.Capability.SCREENSHOT,
                base.Capability.MULTI_TOUCH,
                base.Capability.TEXT_SELECTION,
                base.Capability.DC_SET_LOCATION,
                base.Capability.DC_CLIPBOARD,
            }
        )
        | base.ANDROID_PERMISSION_CAPABILITIES
    )

    def capabilities(self) -> set[str]:
        return set(self.CAPABILITIES)
