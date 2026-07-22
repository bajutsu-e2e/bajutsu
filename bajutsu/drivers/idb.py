"""idb backend (headless, coordinate-based).

Parses `idb ui describe-all` JSON into normalized Elements and acts via
`idb ui tap/text/swipe`. idb has no semantic tap, so a tap resolves the target's
frame center first. The JSON key names follow fb-idb's describe-all output,
validated on-device against fb-idb (iPhone 17 Pro, recent iOS); re-check them if
the installed idb version changes the schema.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from collections.abc import Awaitable, Callable
from typing import Any

from bajutsu import simctl
from bajutsu.device_id import is_valid_device_id
from bajutsu.drivers import base
from bajutsu.drivers.coordinate_tree import CoordinateTreeDriver

RunFn = Callable[[list[str]], str]

_logger = logging.getLogger("bajutsu.idb")

# A short dwell on every tap. A zero-duration `idb ui tap` presses and releases in
# the same instant, which a UISwitch's gesture recognizer does not register — so a
# coordinate tap on a real Toggle never flips it. A brief hold actuates the switch
# while staying far below any long-press threshold, so plain buttons/rows behave
# identically. (Surfaced by the sample app's ctrl.toggle.)
_TAP_DURATION_S = 0.1


def _validated_udid(udid: str) -> str:
    # A udid from `--udid` / config reaches idb/xcrun argv (`--udid <id>`), so it follows the
    # shared `device_id` policy — never leading with `-`, which would be read as an option. Raises
    # simctl.DeviceError (not a bare ValueError) so a bad --udid surfaces as the CLI's clean exit-2
    # device fault, the same boundary adb's `_checked_serial` uses. No `.strip()`: the check is
    # exact, matching adb — a serial with surrounding whitespace is rejected outright.
    if is_valid_device_id(udid):
        return udid
    raise simctl.DeviceError(f"invalid udid: {udid!r}")


def _real_run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


# --- command builders ---


def describe_all_cmd(udid: str) -> list[str]:
    """The `idb` argv that dumps the accessibility tree as JSON."""
    return ["idb", "ui", "describe-all", "--udid", _validated_udid(udid), "--json"]


def disconnect_cmd(udid: str) -> list[str]:
    """The `idb` argv that drops this device's companion connection (BE-0231 Unit 6).

    Per-udid so a concurrent crawl lane on another Simulator keeps its own companion, unlike a
    global `idb kill`.
    """
    return ["idb", "disconnect", _validated_udid(udid)]


def connect_cmd(udid: str) -> list[str]:
    """The `idb` argv that (re)establishes this device's companion connection (BE-0231 Unit 6)."""
    return ["idb", "connect", _validated_udid(udid)]


def tap_cmd(udid: str, x: float, y: float) -> list[str]:
    """The `idb` argv that taps the point (x, y)."""
    return [
        "idb",
        "ui",
        "tap",
        "--udid",
        _validated_udid(udid),
        _num(x),
        _num(y),
        "--duration",
        str(_TAP_DURATION_S),
    ]


def swipe_cmd(udid: str, x1: float, y1: float, x2: float, y2: float) -> list[str]:
    """The `idb` argv that drags from (x1, y1) to (x2, y2)."""
    # A finite duration makes it a real drag; an instantaneous swipe isn't recognized
    # as a pan/drag gesture by SwiftUI.
    return [
        "idb",
        "ui",
        "swipe",
        "--udid",
        _validated_udid(udid),
        _num(x1),
        _num(y1),
        _num(x2),
        _num(y2),
        "--duration",
        "0.2",
    ]


# USB HID usage id for the keyboard Delete/Backspace key. fb-idb's `client.key_sequence` sends raw
# HID key events, so a backspace is this keycode — not the `\b` control character, which fb-idb's
# text keymap (`idb.common.hid.KEY_MAP`) has no entry for and rejects with "No keycode found for".
_HID_KEY_DELETE = 42

# USB HID usage ids for a hardware Cmd+V chord — the paste fallback below needs a true chord (Cmd
# held down while V presses), not `key_sequence`'s sequential press-release of each key, so these
# are sent as raw HIDPress down/up events rather than through `key_sequence`.
_HID_KEY_LEFT_GUI = 227
_HID_KEY_V = 25


def _with_companion_client(udid: str, action: Callable[[Any], Awaitable[None]]) -> None:
    """Connect to `idb_companion` over the fb-idb gRPC client and run `action` on it (BE-0155).

    fb-idb's `idb ui text` takes the text as a required positional argument, so a secret or OTP typed
    through the CLI sits on the `idb` process's argv, where a co-tenant on the host could read it via
    `ps`/`/proc`. The CLI subcommands are only thin wrappers around the gRPC `client`, so we call the
    client directly instead: the value travels to `idb_companion` over gRPC and never lands on any
    command line. `action` receives the connected client, keeping the connection and event-loop
    boilerplate here while each caller chooses the right gRPC call (`text` for typing, `key_sequence`
    for backspaces).

    Runs its own event loop via `asyncio.run`: the idb driver is synchronous and is only ever called
    from threads with no running loop (the runner and the crawl workers).
    """
    import shutil

    companion_path = shutil.which("idb_companion")
    if companion_path is None:
        # Fail fast and legibly rather than letting a None path surface as an opaque
        # error deep inside fb-idb. idb_companion is a separate Homebrew formula. Checked
        # before the fb-idb import below so this stays meaningful even where the idb extra
        # isn't installed (e.g. the deterministic gate, which carries no backend deps).
        raise RuntimeError(
            "idb_companion not found on PATH — install it with "
            "`brew install facebook/fb/idb-companion`"
        )

    import asyncio
    import logging

    from idb.grpc.management import ClientManager

    async def _run() -> None:
        manager = ClientManager(
            companion_path=companion_path,
            logger=logging.getLogger("bajutsu.idb.companion"),
        )
        async with manager.from_udid(udid=udid) as client:
            await action(client)

    asyncio.run(_run())


def _type_text_via_companion(udid: str, text: str) -> None:
    """Type `text` into the focused field over the fb-idb gRPC companion path (BE-0155).

    Falls back to `_paste_text_via_companion` for text fb-idb's HID keymap can't encode as key
    presses. The keymap (`idb.common.hid.KEY_MAP`) only covers the US keyboard layout, so a
    character outside it (Japanese, Chinese, Korean, emoji, ...) makes `text_to_events` raise a bare
    `Exception("No keycode found for <char>")` — always *before* any key is sent, since `client.text`
    builds the full event list up front, so retrying the whole string via paste is safe: no partial
    input from the failed attempt to reconcile. That atomicity is an internal ordering guarantee of
    fb-idb's `text_to_events` (pinned `>=1.1.0`, no upper bound), not something this driver enforces —
    if a future release started emitting HID key events incrementally instead of building the whole
    list up front, mixed Latin/non-Latin text could land stray partial keys ahead of the pasted
    string, and nothing in the fast suite (only the on-device test drives the real library) would
    catch that drift.
    """
    try:
        _with_companion_client(udid, lambda client: client.text(text=text))
    except Exception as e:
        # Matched on fb-idb's exact current wording (pinned `>=1.1.0`, no upper bound), not a typed
        # exception of its own — a future fb-idb release rewording this message would make the match
        # miss silently, re-raising here instead of pasting, bringing back the crash this fallback
        # exists to prevent. Only the on-device test drives the real library; the fast-suite tests
        # only mock today's wording, so a drift wouldn't be caught there either.
        if not str(e).startswith("No keycode found for"):
            raise
        _paste_text_via_companion(udid, text)


def _paste_text_via_companion(udid: str, text: str) -> None:
    """Type `text` by pasting it, for characters idb's HID text path can't encode (see above).

    idb has no native "paste" gRPC call, but a hardware Cmd+V chord reaches the same UIKit paste
    behavior a real paste gesture would: `UITextField`/`UITextView` wire Cmd+V to Paste for the
    focused responder whenever a hardware keyboard is attached, and the fb-idb HID channel idb
    already drives for `type`/`delete` presents itself as exactly that (verified on-device against a
    Simulator). The two presses must be a true chord — Cmd held down while V presses — not
    `key_sequence`'s sequential press-release of each key, so this sends the raw HID down/up events
    directly via `send_events`.

    Seeds the Simulator pasteboard with `text` via `simctl pbcopy` (Unicode round-trips there without
    the HID keymap's US-layout limit) and leaves it there rather than restoring whatever the
    pasteboard held before. Restoring immediately after `send_events` returns would race the
    focused app actually reading the pasteboard for the paste this chord triggers: fb-idb's `hid()`
    call is only known to ack once idb_companion has drained the HID event stream, not once the
    Simulator's app process has finished handling it, and that gap is exactly the kind of
    wall-clock-dependent behavior prime directive 2 (determinism) rules out — restoring too early
    would silently paste the *old* pasteboard content instead of `text`, intermittently, under
    exactly the load that makes it hard to catch in CI. A scenario combining a `type` of non-Latin
    text with a later `clipboard` assertion should account for this.

    Accepted trade-off: this reopens, for non-Latin text specifically, a
    narrower version of the side channel `_with_companion_client` above exists to close for the
    direct HID path — `text` sits on the Simulator's *global* pasteboard, readable by any other
    process on the host with `simctl` access to this udid (`xcrun simctl pbpaste --udid <udid>`),
    for as long as nothing else overwrites it. There is no lower-exposure channel available through
    idb itself: its HID text path is US-keyboard-only, so any non-Latin `type` has to transit the
    system pasteboard one way or another. Closing this fully would mean routing typed text through
    an app-side SDK channel instead (mirroring how BajutsuKit/BajutsuAndroid already provide
    app-process-only capabilities for network/clipboard, BE-0233) — real new scope, not a
    same-PR fix. Until then: avoid `type`-ing secrets/OTPs that contain non-Latin characters on the
    idb backend, since only those fall back to this path.
    """
    from idb.common.types import HIDDirection, HIDKey, HIDPress

    chord = [
        HIDPress(action=HIDKey(keycode=_HID_KEY_LEFT_GUI), direction=HIDDirection.DOWN),
        HIDPress(action=HIDKey(keycode=_HID_KEY_V), direction=HIDDirection.DOWN),
        HIDPress(action=HIDKey(keycode=_HID_KEY_V), direction=HIDDirection.UP),
        HIDPress(action=HIDKey(keycode=_HID_KEY_LEFT_GUI), direction=HIDDirection.UP),
    ]
    simctl.Env(udid).set_clipboard(text)
    _with_companion_client(udid, lambda client: client.send_events(chord))


def _delete_text_via_companion(udid: str, count: int) -> None:
    """Backspace `count` times on the focused field over the fb-idb gRPC companion path (BE-0265).

    Sends `count` Delete/Backspace HID key events via `client.key_sequence`, not the backspace control
    character through `client.text()`: fb-idb's text keymap has no entry for that control character and
    rejects it with "No keycode found for" (BE-0280). The HID key-event path is the hardware backspace
    the field honors natively. `select` / `copy` are not offered on idb — it is coordinate-only, so
    those raise UnsupportedAction and route to XCUITest, mirroring multi-touch.
    """
    _with_companion_client(udid, lambda client: client.key_sequence([_HID_KEY_DELETE] * count))


def screenshot_cmd(udid: str, path: str) -> list[str]:
    """The argv that writes a Simulator screenshot to path (via simctl)."""
    # idb's own frame capture is unreliable ("No Image available to encode"),
    # so screenshot via simctl, which is always available on the Simulator.
    return ["xcrun", "simctl", "io", _validated_udid(udid), "screenshot", path]


def _num(v: float) -> str:
    return str(round(v))  # idb ui tap/swipe require integer coordinates


# --- describe-all parsing ---

# The normalized trait `_norm_type` produces for idb's top-level app element ("AXApplication" ->
# "application"); the accessibility-bridge-wedge check keys on it (BE-0231 Unit 6).
_APPLICATION_TRAIT = "application"


def _norm_type(t: str) -> str:
    t = t.removeprefix("AX")
    return t[:1].lower() + t[1:] if t else t


def _str_or_none(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


def _traits(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    type_ = item.get("type") or item.get("role")
    if isinstance(type_, str) and type_:
        out.append(_norm_type(type_))
    if item.get("enabled") is False:
        out.append(base.Trait.NOT_ENABLED)
    if item.get("selected") is True:
        out.append(base.Trait.SELECTED)
    return out


def _frame(item: dict[str, Any]) -> base.Frame:
    f = item.get("frame") or {}
    return (
        float(f.get("x", 0)),
        float(f.get("y", 0)),
        float(f.get("width", 0)),
        float(f.get("height", 0)),
    )


def _to_element(item: dict[str, Any]) -> base.Element:
    label = item.get("AXLabel") if item.get("AXLabel") is not None else item.get("label")
    value = item.get("AXValue") if item.get("AXValue") is not None else item.get("value")
    return {
        "identifier": _str_or_none(item.get("AXUniqueId")),
        "label": _str_or_none(label),
        "value": _str_or_none(value),
        "traits": _traits(item),
        "frame": _frame(item),
    }


def parse_describe_all(text: str) -> list[base.Element]:
    """Parse describe-all output (a JSON array, or newline-delimited JSON objects)."""
    text = text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [_to_element(json.loads(line)) for line in text.splitlines() if line.strip()]
    items = data if isinstance(data, list) else [data]
    return [_to_element(it) for it in items if isinstance(it, dict)]


class IdbDriver(CoordinateTreeDriver):
    """Driver implementation for the iOS Simulator via idb.

    The transient-empty retry, exponential backoff, stable-key projection, and not-found resolve loop
    live in `CoordinateTreeDriver` (shared with `AdbDriver`); this class supplies idb's own describe
    (`ui describe-all` + JSON), its wall-clock-bounded `_settle`, the accessibility-bridge-wedge
    recovery, and its actuators.
    """

    name = "idb"

    # The settle poll is bounded by elapsed time, not a fixed read count (BE-0299 Unit 4), matching
    # `AdbDriver`: a fixed count grants far less margin once the machine running it is slow or loaded
    # (three ~50 ms polls ≈ 150 ms, ~53x narrower than the Android side's 8 s), so a still-moving tree
    # can pass as settled on a loaded CI host. A stable screen still settles in one read (the first
    # `query()` matches the cached key); only a genuinely-animating screen polls.
    _SETTLE_DEADLINE_S = 8.0  # ceiling on waiting for the tree to stop moving (spans an animation)
    _SETTLE_POLL_S = 0.05  # inter-read cadence; describe-all's own latency provides natural spacing
    _AX_RESET_RETRIES = 3  # companion-connection resets on a persistent accessibility-bridge wedge

    def __init__(self, udid: str, run: RunFn = _real_run) -> None:
        # Validate once at the object boundary so every use of self.udid is covered — the argv
        # builders and the gRPC companion path (_type_text_via_companion) alike, plus any future
        # use site — without each having to remember to wrap it.
        super().__init__()
        self.udid = _validated_udid(udid)
        self._run = run

    def query(self) -> list[base.Element]:
        """describe-all, parsed and normalized into Elements.

        idb sometimes returns a near-empty tree mid-transition; once a richer tree
        has been seen on this device, a degenerate result is retried a bounded number
        of times so a single-shot assertion or wait does not act on the transient
        snapshot. A screen that has only ever been sparse (max seen < _READY_MIN) is
        returned as-is, so a genuinely small screen is never masked. That retry (and the
        stable-key bookkeeping this method closes with) is the shared `_read_settled_tree` /
        `_record_tree`; idb overrides `query` only to layer the wedge recovery below.

        A distinct failure survives that retry: idb's accessibility bridge can fail to attach to
        the app window on a cold-boot first launch, returning only the zero-frame `application` root
        even though the app has rendered (BE-0231 Unit 6). That is not a transient the same-companion
        re-read clears, so on this signature the companion connection is reset (forcing a fresh
        attach) and re-read, bounded — and if it persists, the degenerate tree is returned so the
        wait still fails loudly with the Unit 1 diagnostic rather than being masked.
        """
        els = self._read_settled_tree()
        for _ in range(self._AX_RESET_RETRIES):
            if not self._is_ax_bridge_wedged(els):
                break
            self._reset_companion()
            els = self._read_settled_tree()
        return self._record_tree(els)

    def _is_unrecoverable_empty(self, els: list[base.Element]) -> bool:
        # A wedge satisfies `_is_transient_empty` once a richer tree has been seen (both are
        # `len < _READY_MIN`), but a same-companion re-read can never clear it — so
        # `_read_settled_tree` stops early and yields it to `query`, which resets the companion,
        # rather than burning the backoff loop on a read that cannot recover (BE-0231 Unit 6).
        return self._is_ax_bridge_wedged(els)

    def _settle(self) -> list[base.Element]:
        """Wait until the tree's identifier-frame projection stops changing, or give up.

        Compares (identifier, frame) only — ignoring volatile value, traits, label — so data changes
        on a static screen do not trigger extra polls. The first call (no cached key) returns
        immediately; a cached-match also returns in one query. Only a cache miss starts the poll,
        which is bounded by a wall-clock deadline rather than a fixed read count (BE-0299 Unit 4), so
        it spans a real animation whatever the read costs — the same shape `AdbDriver._settle` adopted
        in BE-0245, and for the same reason: a fixed poll count runs out on a slow/loaded machine and
        lets a still-moving tree pass as settled.
        """
        prev_key = self._last_stable_key
        tree = self.query()
        key = self._last_stable_key
        if prev_key is None or key == prev_key:
            return tree
        deadline = time.monotonic() + self._SETTLE_DEADLINE_S
        while time.monotonic() < deadline:
            time.sleep(self._SETTLE_POLL_S)
            tree = self.query()
            new_key = self._last_stable_key
            if new_key == key:
                return tree
            key = new_key
        return tree

    def _describe(self) -> list[base.Element]:
        return parse_describe_all(self._run(describe_all_cmd(self.udid)))

    @staticmethod
    def _is_ax_bridge_wedged(els: list[base.Element]) -> bool:
        """Whether describe-all shows only an application root with no geometry (BE-0231 Unit 6).

        The signature of an accessibility bridge that has not attached to the app window: a lone
        `application` element with a zero-area frame, seen while the app has actually rendered. It is
        keyed on the degenerate frame — not merely on being sparse — so it is told apart from idb's
        generic mid-transition empty (which `_is_transient_empty` owns) and from a genuinely sparse
        screen, both of which carry real geometry; that lets it fire even on the first screen, where
        no richer tree has been seen yet.
        """
        if len(els) != 1 or _APPLICATION_TRAIT not in els[0]["traits"]:
            return False
        _, _, width, height = els[0]["frame"]
        return width <= 0 or height <= 0

    def _reset_companion(self) -> None:
        """Reset this device's idb companion connection to force a fresh accessibility attach.

        Per-udid disconnect then connect (not a global `idb kill`), so a concurrent crawl lane on
        another Simulator keeps its companion. Best-effort: a disconnect with no live connection, or
        a connect race, raises CalledProcessError that must not abort the query — the re-read after
        this decides whether recovery worked. A failure is logged (not masked), so a reset that keeps
        failing — and would otherwise burn every retry silently — stays visible in the run's logs.
        """
        for cmd in (disconnect_cmd(self.udid), connect_cmd(self.udid)):
            try:
                self._run(cmd)
            except subprocess.CalledProcessError as exc:
                _logger.warning("idb companion reset step failed: %s (%s)", cmd, exc)

    def _center(self, sel: base.Selector) -> base.Point:
        tree = self._settle()
        el = self._resolve(sel, initial_tree=tree)
        return base.frame_center(el["frame"])

    def tap(self, sel: base.Selector) -> None:
        x, y = self._center(sel)
        self._run(tap_cmd(self.udid, x, y))

    def tap_point(self, p: base.Point) -> None:
        self._run(tap_cmd(self.udid, p[0], p[1]))

    def double_tap(self, sel: base.Selector) -> None:
        # idb has no double-tap; two quick taps at the same point register as one.
        x, y = self._center(sel)
        self._run(tap_cmd(self.udid, x, y))
        self._run(tap_cmd(self.udid, x, y))

    def pinch(self, sel: base.Selector, scale: float) -> None:
        raise base.UnsupportedAction(
            "pinch は multiTouch が必要; idb は単一タッチ（HID に複数指イベントがない）"
        )

    def rotate(self, sel: base.Selector, radians: float) -> None:
        raise base.UnsupportedAction(
            "rotate は multiTouch が必要; idb は単一タッチ（HID に複数指イベントがない）"
        )

    def long_press(self, sel: base.Selector, duration: float) -> None:
        x, y = self._center(sel)
        self._run(
            [
                "idb",
                "ui",
                "tap",
                "--udid",
                _validated_udid(self.udid),
                _num(x),
                _num(y),
                "--duration",
                str(duration),
            ]
        )

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._run(swipe_cmd(self.udid, frm[0], frm[1], to[0], to[1]))

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        # A real idb drag scrolls the OS's scroll views, so a directional scroll is just a swipe.
        self.swipe(frm, to)

    def select_option(self, sel: base.Selector, option: str) -> None:
        raise base.UnsupportedAction(
            "selectOption は <select> を持つ web バックエンド専用; iOS ネイティブに <select> はない"
        )

    def back(self) -> None:
        # No hardware back on iOS: tap the OS navigation back button (BE-0210).
        self.tap({"id": base.OS_BACK_BUTTON})

    def type_text(self, text: str) -> None:
        # Via _type_text (a patchable class attribute), so tests can intercept the value
        # without a companion, and so it never touches argv — see _type_text_via_companion.
        self._type_text(self.udid, text)

    _type_text = staticmethod(_type_text_via_companion)

    def delete_text(self, count: int) -> None:
        # Via _delete_text (patchable, like _type_text) so tests intercept without a companion.
        self._delete_text(self.udid, count)

    _delete_text = staticmethod(_delete_text_via_companion)

    def select_all(self) -> None:
        raise base.UnsupportedAction(
            "select は idb では未対応（座標専用バックエンドで select-all の手段がない）; "
            "codegen→XCUITest を使うこと"
        )

    def copy_selection(self) -> None:
        raise base.UnsupportedAction(
            "copy は idb では未対応（select ができないため）; codegen→XCUITest を使うこと"
        )

    def screenshot(self, path: str) -> None:
        self._run(screenshot_cmd(self.udid, path))

    # No semantic tap and no native network monitoring. The whole device-control family
    # (`DEVICE_CONTROL_ALL`) because the iOS Simulator lifecycle wires a real simctl-backed
    # `DeviceControl` for idb runs (BE-0128; per-operation tokens since BE-0212). Exposed as a
    # class constant so the preflight (BE-0082) can read it via `backends.capabilities_for` without
    # constructing a driver.
    CAPABILITIES = (
        frozenset(
            {
                base.Capability.QUERY,
                base.Capability.ELEMENTS,
                base.Capability.SCREENSHOT,
            }
        )
        | base.DEVICE_CONTROL_ALL
        | base.IOS_PERMISSION_CAPABILITIES
    )

    def capabilities(self) -> set[str]:
        return set(self.CAPABILITIES)
