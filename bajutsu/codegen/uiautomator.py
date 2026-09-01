"""Generate a native UI Automator test (Kotlin) from a recorded scenario (BE-0209).

A passing scenario is the deterministic source of truth; emitting a UI Automator test lets a
team run the same flow in their existing Android instrumentation CI — no bajutsu runtime, no
adb driver of ours, and no AI at test time. The mapping is purely structural (no AI).

UI Automator is the closer twin of the adb backend (`common/drivers/adb.py`): both take a
cross-process, black-box view of the app through `resource-id` / `text` / `content-desc`, so the
emitter is the faithful *reverse* of the driver's own read of the tree — `resource-id` (with the
`<package>:id/` prefix the driver strips) → `By.res`, `text` → `By.text`, `content-desc` →
`By.desc`. The generated test drives `UiDevice` / `UiObject2` and asserts with JUnit, mirroring
what the driver does at run time rather than an Espresso view-matcher idiom.

Only a single-field selector (`id` / `label` / `value` / `idMatches` / `labelMatches`) maps to a
`BySelector`; a compound selector (`traits` / `within` / `index`, or several fields together) has
no faithful single-selector form and emits a `// TODO`. Constructs the adb backend cannot drive —
the device-control family, multi-touch beyond pinch, and every network assertion — emit a labeled
`// TODO` naming why, never a wrong guess, so the output is always reviewable. A runtime-only
construct the shared walk cannot translate at all (`if` / `forEach` / `extract`) fails loudly with
a `CodegenError` instead of a silent no-op stub (BE-0297).
"""

from __future__ import annotations

import re

from bajutsu.assertions import request_label
from bajutsu.codegen.common import (
    AfterEmission,
    class_name,
    ident,
    indent_lines,
    interrupts_setup_lines,
    is_plain_substring,
    manual_todo,
    ms,
    network_unsupported,
    permissions_setup_lines,
    render_test_file,
)
from bajutsu.common.drivers import base
from bajutsu.scenario import AfterRule, Assertion, Gone, Scenario, Step, WaitRequest
from bajutsu.scenario.models.assertions import CountMatch, TextMatch, Wait

# The adb backend has no network-interception surface (common/drivers/adb.py CAPABILITIES), so a network
# `request` assertion / `until: { request }` wait has no faithful translation — a labeled TODO
# naming the endpoint, like the device-control steps, not a bare "unsupported".
_NO_NETWORK = network_unsupported("the adb backend")

# Directional swipe on an element: UiObject2.swipe(Direction, percent). The percent is the drag
# extent as a fraction of the element — intrinsic to the gesture, like the XCUITest swipe helpers.
_SWIPE_PERCENT = "0.75f"
_DIRECTION = {"up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT"}

# fnmatch metacharacters an `idMatches` glob may carry beyond a literal. `*`/`?` map cleanly to a
# regex (`.*`/`.`); a `[…]` character class needs the fnmatch-vs-regex negation translation
# (`[!` → `[^`), so a glob carrying one falls back to `// TODO` rather than a subtly-wrong regex.
_GLOB_CLASS_CHARS = set("[]")

# A scenario `id` is the *local* name (the adb driver strips the `<package>:id/` prefix); a native
# id carries that prefix in the tree while a Compose testTag surfaced via testTagsAsResourceId does
# not, so the emitted selector makes the prefix optional to match either — the reverse of the strip.
_ID_PREFIX = "(.*:id/)?"

# Subdirectory the generated test writes failure evidence to, inside the directory the harness
# collects off the device (see the DIAGNOSTICS_DIR comment in the emitted preamble).
_DIAGNOSTICS_DIR = "codegen-diagnostics"

# The instrumentation argument naming that collected directory. The Android Gradle Plugin passes it
# and copies the directory into build/outputs/connected_android_test_additional_output after the run.
_ADDITIONAL_OUTPUT_ARG = "additionalTestOutputDir"

# logcat tag for the same evidence. The instrumentation's logcat is already collected per test by
# Gradle, so tagging the window list makes it readable even when the file pull off the device fails.
_LOG_TAG = "BajutsuCodegen"


def _s(text: str) -> str:
    """A Kotlin double-quoted string literal.

    Kotlin forbids a raw line break inside a `"…"` literal and reads `$` as a template expression,
    so both are escaped alongside `\\` and `"`.
    """
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _glob_to_regex(glob: str) -> str | None:
    """Render an `idMatches` fnmatch glob as a Java regex (prefix-optional), or None (→ TODO).

    `*` → `.*`, `?` → `.`, every other character escaped; a `[…]` class returns None because its
    fnmatch-vs-regex negation differs (`[!` vs `[^`) and a wrong regex would match the wrong ids.
    """
    if any(c in _GLOB_CLASS_CHARS for c in glob):
        return None
    body = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in glob)
    return _ID_PREFIX + body


def _by(sel: base.Selector) -> str | None:
    """A UI Automator `BySelector` expression for a single-field selector, or None (→ TODO).

    Only one primary field maps faithfully; a compound selector composes constraints UI Automator's
    `By` chaining cannot express the same way the driver resolves them, so it stays a TODO rather
    than a broadened match that drops a constraint.
    """
    keys = set(sel)
    # An `id` / `idMatches` list of OR candidates (BE-0221) matches *any* candidate. This emitter
    # targets either Android toolkit — Compose surfaces the dotted SPEC id, Views the underscore
    # form — so it emits an alternation over all candidates rather than picking one, and the
    # generated test resolves against whichever id the target build actually exposes.
    if keys == {"id"}:
        ids = base.id_candidates(sel["id"])
        if len(ids) == 1:
            return f"byId({_s(ids[0])})"
        return "byAnyId(" + ", ".join(_s(i) for i in ids) + ")"
    if keys == {"label"}:
        return f"By.text({_s(sel['label'])})"
    if keys == {"value"}:
        return f"By.desc({_s(sel['value'])})"
    if keys == {"idMatches"}:
        cands = base.id_candidates(sel["idMatches"])
        regexes = [r for g in cands if (r := _glob_to_regex(g)) is not None]
        if len(regexes) != len(cands):
            return None  # a `[…]` class in any candidate has no faithful regex form (→ TODO)
        pattern = regexes[0] if len(regexes) == 1 else "|".join(f"(?:{r})" for r in regexes)
        return f"By.res(Pattern.compile({_s(pattern)}))"
    if keys == {"labelMatches"}:
        # `By.text(Pattern)` is a full-string match, unlike `labelMatches`' `re.search`, so only a
        # metacharacter-free pattern (a plain substring) maps faithfully — via `By.textContains`. A
        # real regex has no faithful single-selector form, so it stays unsupported (→ TODO).
        pattern = sel["labelMatches"]
        if not is_plain_substring(pattern):
            return None
        return f"By.textContains({_s(pattern)})"
    return None


def _ui_selector(sel: base.Selector) -> str | None:
    """A `UiSelector` for `UiScrollable.scrollIntoView`, or None (→ TODO).

    `scrollIntoView` takes a `UiSelector`, not the `BySelector` the rest of this emitter uses, so
    `scroll` maps only the primary id / label forms; a compound selector stays a labeled TODO. An id
    matches whether or not the app namespaces it with a `<package>:id/` prefix, mirroring `byId`.
    """
    keys = set(sel)
    if keys == {"id"}:
        ids = base.id_candidates(sel["id"])
        if len(ids) == 1:
            inner = f"Pattern.quote({_s(ids[0])})"
        else:
            alt = ' + "|" + '.join(f"Pattern.quote({_s(i)})" for i in ids)
            inner = f'"(" + {alt} + ")"'
        return f'UiSelector().resourceIdMatches("{_ID_PREFIX}" + {inner})'
    if keys == {"label"}:
        return f"UiSelector().text({_s(sel['label'])})"
    return None


def _unsupported_selector_todo(sel: base.Selector) -> str:
    """A labeled `// TODO` naming the selector fields that have no single UI Automator selector."""
    fields = ", ".join(sorted(sel))
    return (
        f"// TODO: unsupported selector ({fields}) — only a single id / label / value / "
        "idMatches / labelMatches maps to a UI Automator BySelector"
    )


def _act(sel: base.Selector, call: str) -> list[str]:
    """An `act(<by>).<call>` line, or a TODO when the selector can't be rendered.

    `act` (below, in the file preamble) waits for the element before returning it — `findObject`
    alone is a single-shot query with no implicit wait — unlike the adb driver's `tap()`, whose
    resolve step retries with a timeout (`resolve_unique` itself is single-shot, no wait), so an
    action right after `launch()` or a UI transition can race the render.
    """
    by = _by(sel)
    if by is None:
        return [_unsupported_selector_todo(sel)]
    return [f"act({by}).{call}"]


# One branch per scenario step kind: the count tracks the schema's size, not tangled logic, and a
# split would leave no single place a new scenario step kind clearly belongs (BE-0386).
def _emit_step(step: Step) -> list[str]:  # noqa: PLR0911, PLR0912
    if step.tap is not None:
        return _act(step.tap.as_selector(), "click()")
    if step.double_tap is not None:
        return ["// TODO: doubleTap — UI Automator has no double-tap gesture; not generated"]
    if step.back is not None:
        # UI Automator has a native system back — the peer of the adb driver's `keyevent 4` — so
        # codegen emits it faithfully rather than an unlabeled TODO (BE-0210).
        return ["device.pressBack()"]
    if step.long_press is not None:
        # UiObject2.longClick() uses the platform long-press timeout; the scenario's duration has no
        # parameter here, so it is dropped (the honest closest gesture, not a wrong fixed sleep).
        return _act(step.long_press.sel.as_selector(), "longClick()")
    if step.type is not None:
        if step.type.into is not None:
            return _act(step.type.into.as_selector(), f"text = {_s(step.type.text)}")
        return [
            "// TODO: type without a target — UI Automator types into a resolved element; "
            "not generated"
        ]
    if step.clear is not None:
        # UiObject2.clear() empties the focused field — the faithful peer of the driver's clear (BE-0265).
        return _act(step.clear.into.as_selector(), "clear()")
    if step.delete is not None:
        by = _by(step.delete.into.as_selector())
        if by is None:
            return [_unsupported_selector_todo(step.delete.into.as_selector())]
        # Focus, then backspace `count` times (KEYCODE_DEL) — one key event per character (BE-0265).
        return [
            f"act({by}).click()",
            f"repeat({step.delete.count}) {{ device.pressKeyCode(KeyEvent.KEYCODE_DEL) }}",
        ]
    if step.select is not None:
        by = _by(step.select.into.as_selector())
        if by is None:
            return [_unsupported_selector_todo(step.select.into.as_selector())]
        # Focus, then Ctrl+A selects the whole field (BE-0265).
        return [
            f"act({by}).click()",
            "device.pressKeyCode(KeyEvent.KEYCODE_A, KeyEvent.META_CTRL_ON)",
        ]
    if step.copy_ is not None:
        return ["device.pressKeyCode(KeyEvent.KEYCODE_C, KeyEvent.META_CTRL_ON)"]
    if step.swipe is not None:
        sw = step.swipe
        if sw.on is not None and sw.direction is not None:
            return _act(
                sw.on.as_selector(),
                f"swipe(Direction.{_DIRECTION[sw.direction]}, {_SWIPE_PERCENT})",
            )
        return ["// TODO: coordinate swipe (from/to) is not generated"]
    if step.drag is not None:
        # UiObject2.swipe is a real drag, so an element-anchored `drag` (BE-0227) emits the same
        # primitive a directional `swipe` does — on Android a drag both scrolls and moves handles.
        return _act(
            step.drag.on.as_selector(),
            f"swipe(Direction.{_DIRECTION[step.drag.direction]}, {_SWIPE_PERCENT})",
        )
    if step.scroll is not None:
        # UI Automator has a native scroll-to-element: `UiScrollable.scrollIntoView` searches the
        # scrollable list, bounded by `setMaxSearchSwipes` — the peer of `maxScrolls` (BE-0326).
        # `direction` picks the list orientation; `within` has no faithful UiScrollable scope, so it
        # is left to the first scrollable container. `amount` has no faithful mapping either —
        # scrollIntoView does its own stepping (BE-0400).
        sc = step.scroll
        ui = _ui_selector(sc.to.as_selector())
        if ui is None:
            return [_unsupported_selector_todo(sc.to.as_selector())]
        orient = "setAsHorizontalList" if sc.direction in ("left", "right") else "setAsVerticalList"
        return [
            f"UiScrollable(UiSelector().scrollable(true)).{orient}()"
            f".setMaxSearchSwipes({sc.max_scrolls}).scrollIntoView({ui})"
        ]
    if step.pinch is not None:
        # UiObject2 pinchOpen / pinchClose take the gesture extent as a fraction; scale >= 1 zooms in.
        call = "pinchOpen(0.5f)" if step.pinch.scale >= 1 else "pinchClose(0.5f)"
        return _act(step.pinch.sel.as_selector(), call)
    if step.rotate is not None:
        return ["// TODO: rotate — UI Automator has no rotate gesture; not generated"]
    if step.wait is not None:
        return _emit_wait(step.wait)
    if step.relaunch is not None:
        return ["launch(extras)"]
    if step.assert_ is not None:
        return [line for a in step.assert_ for line in _emit_assertion(a)]
    return [_device_control_todo(step)]


def _device_control_todo(step: Step) -> str:
    """A labeled `// TODO` for a step the adb backend cannot drive (device control / helpers)."""
    if step.set_location is not None:
        loc = step.set_location
        return f"// TODO: setLocation(lat: {loc.lat}, lon: {loc.lon}) — no adb device control; not generated"
    if step.push is not None:
        return "// TODO: push — no adb device control; not generated"
    if step.set_clipboard is not None:
        return f"// TODO: setClipboard(text: {_s(step.set_clipboard.text)}) — no adb device control; not generated"
    if step.totp is not None:
        return f"// TODO: totp(into: {step.totp.into.var}) — RFC 6238 OTP; not generated"
    if step.email is not None:
        return f"// TODO: email(into: {step.email.extract.var}) — poll mailbox + extract; not generated"
    if step.generate is not None:
        # A value computed in the bajutsu runner, not on the device; no UI Automator form (BE-0377).
        kind = "random" if step.generate.random is not None else "datetime"
        return (
            f"// TODO: generate({kind}, into: {step.generate.into.var}) — runner-computed value; "
            "not generated"
        )
    if step.manual is not None:
        # A human takeover (BE-0185): no generated-test equivalent — a labeled TODO, not a silent skip.
        return f"// TODO: manual step — {manual_todo(step.manual.label, step.manual.bypass)}"
    if step.handle_system_alert is not None:
        # An iOS SpringBoard prompt (BE-0316); no Android equivalent — a system dialog is reached by
        # an ordinary tap there. A labeled TODO, consistent with the device-family fallbacks above.
        return (
            "// TODO: handleSystemAlert — iOS-only (tap the system dialog directly); not generated"
        )
    return "// TODO: unsupported step"


def _emit_wait(w: Wait) -> list[str]:
    """The lines for a `wait` step: an existence / gone poll to the step's timeout, or a comment.

    Both polls go through the `awaitPresent` / `awaitGone` helpers in the file preamble rather than
    a bare `device.wait(...)`: the helpers slice the budget so a pinned accessibility read cannot
    consume it whole, and they fail naming the selector and the timeout instead of raising an
    unlabeled `AssertionError`.
    """
    timeout = ms(w.timeout)
    if w.for_ is not None:
        by = _by(w.for_.as_selector())
        if by is None:
            return [_unsupported_selector_todo(w.for_.as_selector())]
        return [f"awaitPresent({by}, {timeout}L)"]
    if isinstance(w.until, Gone):
        by = _by(w.until.gone.as_selector())
        if by is None:
            return [_unsupported_selector_todo(w.until.gone.as_selector())]
        return [f"awaitGone({by}, {timeout}L)"]
    if isinstance(w.until, WaitRequest):
        return [f"// TODO: wait until request ({request_label(w.until.request)}) — {_NO_NETWORK}"]
    # "screenChanged" / "settled" — `findObject` does not auto-wait (unlike Playwright/XCUITest), so
    # a bare comment would let the next line run mid-transition. `waitForIdle` blocks until the UI
    # goes idle: the closest faithful condition wait, never a fixed sleep (prime directive #2).
    return [f"device.waitForIdle({timeout}L)"]


def _emit_text_assertion(m: TextMatch, prop: str) -> list[str]:
    """A label / value assertion reading `.text` / `.contentDescription` off the resolved element."""
    by = _by(m.sel.as_selector())
    if by is None:
        return [_unsupported_selector_todo(m.sel.as_selector())]
    actual = f"device.findObject({by}).{prop}"
    if m.equals is not None:
        return [f"assertEquals({_s(m.equals)}, {actual})"]
    if m.contains is not None:
        return [f"assertTrue({actual}.contains({_s(m.contains)}))"]
    return [f"assertTrue({actual}.contains(Regex({_s(m.matches or '')})))"]


def _emit_count(c: CountMatch) -> list[str]:
    by = _by(c.sel.as_selector())
    if by is None:
        return [_unsupported_selector_todo(c.sel.as_selector())]
    size = f"device.findObjects({by}).size"
    if c.equals is not None:
        return [f"assertEquals({c.equals}, {size})"]
    if c.at_least is not None:
        return [f"assertTrue({size} >= {c.at_least})"]
    return [f"assertTrue({size} <= {c.at_most})"]


def _emit_state(sel: base.Selector, prop: str, want: bool) -> list[str]:
    by = _by(sel)
    if by is None:
        return [_unsupported_selector_todo(sel)]
    check = "assertTrue" if want else "assertFalse"
    return [f"{check}(device.findObject({by}).{prop})"]


def _emit_assertion(a: Assertion) -> list[str]:
    if a.exists is not None:
        by = _by(a.exists.sel.as_selector())
        if by is None:
            return [_unsupported_selector_todo(a.exists.sel.as_selector())]
        check = "assertFalse" if a.exists.negate else "assertTrue"
        return [f"{check}(device.hasObject({by}))"]
    if a.value is not None:
        return _emit_text_assertion(a.value, "contentDescription")
    if a.label is not None:
        return _emit_text_assertion(a.label, "text")
    if a.enabled is not None:
        return _emit_state(a.enabled.as_selector(), "isEnabled", True)
    if a.disabled is not None:
        return _emit_state(a.disabled.as_selector(), "isEnabled", False)
    if a.selected is not None:
        return _emit_state(a.selected.as_selector(), "isSelected", True)
    if a.count is not None:
        return _emit_count(a.count)
    if a.request is not None:
        return [f"// TODO: request assertion ({request_label(a.request)}) — {_NO_NETWORK}"]
    if a.request_sequence is not None:
        seq = ", ".join(request_label(m, with_count=False) for m in a.request_sequence)
        return [f"// TODO: requestSequence assertion ({seq}) — {_NO_NETWORK}"]
    if a.response_schema is not None:
        return [
            f"// TODO: responseSchema assertion ({request_label(a.response_schema.request)}) — "
            f"{_NO_NETWORK}"
        ]
    return ["// TODO: unsupported assertion"]


class _UiAutomatorGen:
    """UI Automator target for the shared scenario walk (BE-0083): Kotlin/UiDevice line syntax."""

    def __init__(self, class_name: str, package: str) -> None:
        self._class_name = class_name
        self._package = package

    def file_preamble(self) -> list[str]:
        return [
            "// Generated by bajutsu — do not edit by hand. Re-generate with `bajutsu codegen`.",
            "import android.content.Context",
            "import android.content.Intent",
            "import android.os.Build",
            "import android.os.SystemClock",
            "import android.util.Log",
            "import android.view.KeyEvent",
            "import androidx.test.core.app.ApplicationProvider",
            "import androidx.test.ext.junit.runners.AndroidJUnit4",
            "import androidx.test.platform.app.InstrumentationRegistry",
            "import androidx.test.uiautomator.By",
            "import androidx.test.uiautomator.BySelector",
            "import androidx.test.uiautomator.Configurator",
            "import androidx.test.uiautomator.Direction",
            "import androidx.test.uiautomator.UiDevice",
            "import androidx.test.uiautomator.UiObject2",
            "import androidx.test.uiautomator.UiScrollable",
            "import androidx.test.uiautomator.UiSelector",
            "import androidx.test.uiautomator.Until",
            "import org.junit.Assert.assertEquals",
            "import org.junit.Assert.assertFalse",
            "import org.junit.Assert.assertTrue",
            "import org.junit.Rule",
            "import org.junit.Test",
            "import org.junit.rules.TestRule",
            "import org.junit.rules.TestWatcher",
            "import org.junit.runner.Description",
            "import org.junit.runner.RunWith",
            "import java.io.File",
            "import java.util.regex.Pattern",
            "",
            f"private const val PACKAGE = {_s(self._package)}",
            # One launch attempt's window wait, generous enough that a slow cold start is waited out
            # instead of restarted. A window that never arrives within the whole timeout is stuck
            # rather than slow, which waiting longer cannot fix — so `launch` re-issues the intent
            # instead, LAUNCH_ATTEMPTS times.
            "private const val LAUNCH_TIMEOUT_MS = 20000L",
            "private const val LAUNCH_ATTEMPTS = 2",
            # A CI emulator's first render after launch can outrun a short wait often enough to flake
            # (the same reasoning behind the on-device scenario lanes' BAJUTSU_MIN_WAIT_TIMEOUT floor,
            # demos/showcase/android/Makefile's E2E_WAIT_FLOOR): a wait returns the instant the
            # element appears, so a generous ceiling never slows a fast render — it only gives a slow
            # one room before the step is failed.
            "private const val ACT_TIMEOUT_MS = 15000L",
            # How many window changes to provoke while the window list reads empty, before
            # launching anyway. Each kick is bounded by the key press's own wait for the events it
            # produces, so the count is the whole budget — there is no interval to tune.
            "private const val TRACKING_KICK_ATTEMPTS = 3",
            # How long a single accessibility read may be believed before the cache behind it is
            # dropped and the condition re-read (`waitSliced` below). Not an interval anything
            # sleeps for: a slice ends the instant its condition holds, so this only bounds how
            # long a *pinned* read can keep a wait from seeing the screen — a cost paid on the
            # stale path alone. Small enough that a wedged read loses a fraction of a second,
            # large enough that a healthy wait pays only a couple of cache drops per second.
            "private const val CACHE_REREAD_SLICE_MS = 500L",
            # Where the on-device failure evidence lands, inside the directory the Android Gradle
            # Plugin names with the `additionalTestOutputDir` instrumentation argument and copies off
            # the device after the run (build/outputs/connected_android_test_additional_output),
            # whether the run passed or failed — so CI only has to upload it (android-e2e.yml).
            # Deliberately not the app's own external files directory: `adb` cannot read
            # /sdcard/Android/data/<package> from Android 11 on, so a dump written there is stranded.
            f"private const val DIAGNOSTICS_DIR = {_s(_DIAGNOSTICS_DIR)}",
            f"private const val ADDITIONAL_OUTPUT_ARG = {_s(_ADDITIONAL_OUTPUT_ARG)}",
            f"private const val LOG_TAG = {_s(_LOG_TAG)}",
            "",
            "@RunWith(AndroidJUnit4::class)",
            f"class {self._class_name} {{",
            "  private val device = UiDevice.getInstance("
            + "InstrumentationRegistry.getInstrumentation())",
            "",
            "  // A timed-out wait says nothing about *why* nothing matched, so capture the state"
            + " the",
            "  // assertion was read from: the window hierarchy, the screen, and the accessibility"
            + " window",
            "  // list. Without them a failure is indistinguishable from a slow render and the only"
            + " way",
            "  // to investigate is to re-run until it happens again.",
            "  @get:Rule",
            "  val diagnostics: TestRule = object : TestWatcher() {",
            "    override fun failed(error: Throwable, description: Description) {",
            "      // methodName is a platform type, so Kotlin would insert an invisible null check"
            + " here",
            "      // and lose the evidence to a NullPointerException on the one path that needs it."
            + " Name",
            "      // the fallback instead; displayName is populated for every description.",
            "      dumpDiagnostics(description.methodName ?: description.displayName)",
            "    }",
            "  }",
            "",
            "  // UiDevice reaches the accessibility connection through"
            + " Instrumentation.getUiAutomation(flags),",
            "  // using whatever flags Configurator carries — the usual reason a target sets one"
            + " is",
            "  // FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES. The flag-less overload instead"
            + " disconnects and",
            "  // reconnects that same UiAutomation on any target whose flags differ, so calling"
            + " it from the hot",
            "  // path this file adds below would turn a mere read into the very connection"
            + " churn it exists to",
            "  // diagnose. One accessor, so the callers below cannot drift onto different"
            + " flags.",
            "  //",
            "  // The flagged overload only exists from API 24 (N); UiDevice itself branches on"
            + " the same",
            "  // check, falling back to the flag-less read below it — mirrored here rather than"
            + " raising",
            "  // the API floor a UI Automator target can run on.",
            "  private fun uiAutomation() =",
            "    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {",
            "      InstrumentationRegistry.getInstrumentation()",
            "        .getUiAutomation(Configurator.getInstance().uiAutomationFlags)",
            "    } else {",
            "      // Custom flags reached Instrumentation only in N; UiDevice falls back the"
            + " same way.",
            "      InstrumentationRegistry.getInstrumentation().uiAutomation",
            "    }",
            "",
            "  // The window list, read through the one accessor above.",
            "  private fun accessibilityWindows() = uiAutomation().windows",
            "",
            "  // Every window the accessibility read channel reports, with the package its root"
            + " belongs",
            '  // to — the fact that separates "the element has not rendered yet" from "this'
            + " app's",
            '  // window is not in the tree at all".',
            "  //",
            "  // Never throws. Callers on the failure path build it into the message of the"
            + " AssertionError",
            "  // they are about to raise, and callers that are still retrying log it as evidence"
            + " — so a fault",
            "  // in reading the windows would cost the very fact each of them was collecting.",
            "  private fun windowSummary(): String = runCatching {",
            "    val windows = accessibilityWindows()",
            "    if (windows.isEmpty()) {",
            '      "<no accessibility windows>"',
            "    } else {",
            '      windows.joinToString("\\n") { window ->',
            '        "root=${window.root?.packageName ?: "<null>"} $window"',
            "      }",
            "    }",
            '  }.getOrElse { "<unavailable: $it>" }',
            "",
            "  // Every id `By.res` can currently match, read through the same matcher a selector"
            + " goes",
            "  // through — so it cannot disagree with what the failing wait searched. The hierarchy"
            + " dump",
            "  // does not substitute for it: dumpWindowHierarchy reports `resource-id` from the raw"
            + " node,",
            "  // which leaves a Compose testTag surfaced by testTagsAsResourceId blank there.",
            "  // Never throws either: this goes into the dump below, so a fault reading the ids would",
            "  // cost that evidence rather than be reported by it.",
            "  private fun matchableIds(): String = runCatching {",
            '    val ids = device.findObjects(By.res(Pattern.compile(".+")))',
            "      .mapNotNull { it.resourceName }.distinct().sorted()",
            '    if (ids.isEmpty()) "<none>" else ids.joinToString("\\n")',
            '  }.getOrElse { "<unavailable: $it>" }',
            "",
            "  // One piece of evidence, written on its own: a throw here must not cost the other"
            + " dumps,",
            "  // and it must not pass silently either — a swallowed failure leaves the artifact"
            + " simply",
            "  // absent, on the very path meant to explain why something failed. Name the file and"
            + " the",
            "  // reason in logcat instead. takeScreenshot reports failure by returning false rather"
            + " than",
            "  // throwing, so its caller below turns that into a throw to reach the same log.",
            "  private fun dump(file: File, write: (File) -> Unit) {",
            "    runCatching { write(file) }"
            + '.onFailure { Log.w(LOG_TAG, "could not write $file", it) }',
            "  }",
            "",
            "  // The window list is logged as well as written, so it survives even when the"
            + " directory is",
            "  // never collected off the device.",
            "  private fun dumpDiagnostics(stem: String) {",
            "    val summary = windowSummary()",
            '    Log.w(LOG_TAG, "$stem failed; accessibility windows:\\n$summary")',
            "    // A null parent would make File(parent, …) a *relative* path, so the dumps would"
            + " land",
            "    // somewhere the harness never collects. The window list above is already in"
            + " logcat.",
            "    val parent = diagnosticsParent()",
            "    if (parent == null) {",
            '      Log.w(LOG_TAG, "no directory to write evidence to; see the window list above")',
            "      return",
            "    }",
            "    val dir = File(parent, DIAGNOSTICS_DIR)",
            "    if (!dir.isDirectory && !dir.mkdirs()) {",
            '      Log.w(LOG_TAG, "could not create $dir; no evidence written")',
            "      return",
            "    }",
            '    dump(File(dir, "$stem-windows.txt")) {',
            "      it.writeText("
            + '"accessibility windows:\\n$summary\\n\\nmatchable ids:\\n${matchableIds()}\\n")',
            "    }",
            '    dump(File(dir, "$stem-hierarchy.xml")) { device.dumpWindowHierarchy(it) }',
            '    dump(File(dir, "$stem-screen.png")) {',
            '      if (!device.takeScreenshot(it)) error("takeScreenshot returned false")',
            "    }",
            "  }",
            "",
            "  // The harness-collected directory when there is one, else the app's own external"
            + " files",
            "  // directory — a plain `am instrument` run (no Gradle) passes no argument, and a dump"
            + " left",
            "  // on the device still beats none. A blank argument counts as absent rather than as"
            + " the",
            '  // current directory, which `File("")` would otherwise resolve it to.',
            "  private fun diagnosticsParent(): File? {",
            "    val collected = InstrumentationRegistry.getArguments()"
            + ".getString(ADDITIONAL_OUTPUT_ARG)",
            "    if (!collected.isNullOrBlank()) return File(collected)",
            "    return InstrumentationRegistry.getInstrumentation().targetContext"
            + ".getExternalFilesDir(null)",
            "  }",
            "",
            "  // Match the local id whether or not the app namespaces it with a `<package>:id/`"
            + " prefix —",
            "  // the reverse of the adb driver stripping that prefix (common/drivers/adb.py).",
            "  private fun byId(id: String) =",
            f'    By.res(Pattern.compile("{_ID_PREFIX}" + Pattern.quote(id)))',
            "",
            "  // Match any of several candidate ids (a cross-platform selector, BE-0221) — the id"
            + " form",
            "  // this target's build actually surfaces (Compose: dotted; Views: underscore).",
            "  private fun byAnyId(vararg ids: String) =",
            f'    By.res(Pattern.compile("{_ID_PREFIX}(" +'
            + ' ids.joinToString("|") { Pattern.quote(it) } + ")"))',
            "",
            "  // Provoke a window change so the accessibility framework has to re-report the"
            + " window list,",
            "  // and let the key press's own wait for the events it produces pace the recovery —"
            + " never a",
            "  // sleep. HOME is dispatched through the input pipeline rather than the accessibility"
            + " one, so",
            "  // it lands even while the accessibility view is stale, and it moves any foreground"
            + " app off",
            "  // screen. On the launcher it may produce only content changes, which the key press's"
            + " own",
            "  // wait accepts as well.",
            "  //",
            "  // Deliberately not wrapped in UiAutomation.executeAndWaitForEvent: pressHome already"
            + " waits",
            "  // through that same call, and the nested one would clear the event queue the outer"
            + " wait is",
            "  // watching, so the outer wait could only ever time out. The caller re-reads the"
            + " window list",
            "  // instead, which is the fact in question anyway.",
            "  private fun kickWindowTracking(reason: String) {",
            '    Log.w(LOG_TAG, "kicking accessibility window tracking with pressHome(): $reason")',
            "    // pressHome reports a window event that never arrived by returning false, not by"
            + " throwing,",
            "    // and no event is the wedge's own symptom — so neither outcome may pass"
            + " unrecorded.",
            "    runCatching {",
            '      if (!device.pressHome()) Log.w(LOG_TAG, "pressHome produced no window event")',
            '    }.onFailure { Log.w(LOG_TAG, "pressHome failed", it) }',
            "  }",
            "",
            "  // Never throws. getWindows() raises IllegalStateException when the connection is"
            + " not",
            "  // established, so an unguarded read would replace the caller's named AssertionError"
            + " with a",
            "  // raw framework exception. Returning false collapses that fault together with an"
            + " empty list.",
            "  // The two want opposite next steps — a kick recovers an empty list, an"
            + " unestablished connection",
            "  // it cannot — so the caller names which one it met by appending windowSummary()"
            + " (never",
            '  // throws either): "<no accessibility windows>" against "<unavailable: …>".',
            "  private fun reportsWindows(): Boolean = runCatching {",
            "    accessibilityWindows().isNotEmpty()",
            '  }.getOrElse { Log.w(LOG_TAG, "could not read the window list", it); false }',
            "",
            "  // One of two observed ways the app's window fails to reach the list, and the one that"
            + " can be",
            "  // caught before the launch wait spends its whole timeout: the list holds nothing at"
            + " all. A CI",
            '  // run logged exactly that — "no accessibility windows reported" — and the',
            "  // window change below recovered it.",
            "  //",
            "  // The other way is invisible here: the list is live and merely missing the app's"
            + " window, which",
            "  // reads as healthy to any is-it-empty check. launch() handles that one after its wait"
            + " fails.",
            "  // Both want the same remedy, because no timeout recovers a list that will not gain"
            + " the window",
            "  // on its own — only a window change can.",
            "  //",
            "  // Logs rather than throws when the list is still empty here: HOME against a"
            + " not-yet-started",
            "  // app (the launcher) is the weakest stimulus this file has, per"
            + " kickWindowTracking's own",
            '  // note above — "on the launcher it may produce only content changes". Starting'
            + " the activity",
            "  // is the strong one, since it adds a window outright, but throwing here would"
            + " spend the",
            "  // whole kick budget on the weak stimulus and abort before startActivity ever"
            + " runs — on the",
            "  // one device that most needs the strong one. launch()'s own retry loop already"
            + " reports this",
            "  // failure, naming the window list, if starting the activity does not help"
            + " either.",
            "  private fun ensureWindowTracking() {",
            "    for (attempt in 1..TRACKING_KICK_ATTEMPTS) {",
            "      if (reportsWindows()) return",
            '      kickWindowTracking("no accessibility windows reported '
            + '(pre-launch kick $attempt)")',
            "    }",
            "    // The last kick would otherwise go unchecked. This reads once more only so a"
            + " failure",
            "    // leaves a line — a recovery is silent, and shows as a kick with no failure"
            + " line after",
            "    // it. launch() is tried either way, since nothing here is reported back to it"
            + " and",
            "    // starting the activity is the stronger stimulus.",
            "    if (!reportsWindows()) {",
            "      Log.w(",
            "        LOG_TAG,",
            '        "no usable window list after $TRACKING_KICK_ATTEMPTS kick(s); trying launch"'
            + " +",
            '          " anyway; windows:\\n" + windowSummary()',
            "      )",
            "    }",
            "  }",
            "",
            "  // Launch (or relaunch) the app, forwarding launchEnv as intent extras (the reverse"
            + " of the",
            "  // adb backend's `am start --es`), and wait for its window to reach the accessibility"
            + " tree —",
            "  // a condition wait, never a sleep.",
            "  //",
            "  // Each attempt gets the whole LAUNCH_TIMEOUT_MS, so a merely slow cold start is"
            + " waited out",
            "  // rather than restarted: FLAG_ACTIVITY_CLEAR_TASK tears the activity down, so"
            + " relaunching a",
            "  // launch that was still on its way would send it back to the beginning and could"
            + " starve an app",
            "  // that needs more than one attempt's patience. Only a window that never arrives"
            + " within the",
            "  // full timeout is treated as stuck rather than slow, and only then is the intent"
            + " re-issued.",
            "  //",
            "  // The wait is checked at all because falling through silently mis-attributes the"
            + " failure to the",
            "  // first action, which then times out against a screen the app never reached.",
            "  private fun launch(extras: Map<String, String>) {",
            "    val context = ApplicationProvider.getApplicationContext<Context>()",
            "    for (attempt in 1..LAUNCH_ATTEMPTS) {",
            "      // The window the wait below looks for can only be seen through the"
            + " accessibility window",
            "      // list, so check that the list is live before launching into it rather than"
            + " reading the",
            "      // silence as a slow app.",
            "      ensureWindowTracking()",
            "      val intent = context.packageManager.getLaunchIntentForPackage(PACKAGE)!!",
            "        .apply { addFlags(Intent.FLAG_ACTIVITY_CLEAR_TASK) }",
            "      for ((k, v) in extras) intent.putExtra(k, v)",
            "      context.startActivity(intent)",
            "      val by = By.pkg(PACKAGE).depth(0)",
            "      if (waitPresent(by, LAUNCH_TIMEOUT_MS)) {",
            "        // The window wait proves a window from the package exists, not that it"
            + " finished",
            "        // drawing its first frame, so let the tree settle before any per-action clock"
            + " starts.",
            "        device.waitForIdle(LAUNCH_TIMEOUT_MS)",
            "        return",
            "      }",
            "      // The summary, not just the miss. This is the line that identified the failure:"
            + " a CI",
            "      // run logged a LIVE two-window list here — SystemUI's status bar and a focused"
            + ' "Pixel',
            "      // Launcher isn't responding\" dialog — with the app's own window absent, 19s"
            + " after",
            "      // ActivityTaskManager had reported it Displayed. A focused system window keeps"
            + " the app's",
            "      // window out of what UiAutomation reports, so the app is drawn and foreground"
            + " while every",
            "      // selector searches a list it is not in.",
            '      Log.w(LOG_TAG, "launch attempt $attempt saw no $PACKAGE window in "',
            '        + "${LAUNCH_TIMEOUT_MS}ms; windows:\\n" + windowSummary())',
            "      // Hence the kick, and why it does not read the list first: the list was neither"
            + " empty nor",
            "      // unreadable, so nothing above this point can detect the case. HOME dismisses"
            + " whatever",
            "      // holds focus, and the intent is re-issued into a screen the app can reach. In"
            + " the run",
            "      // above that recovery worked — attempt 2 came up and the test passed.",
            "      //",
            "      // Only while an attempt remains, though. HOME after the last one would leave"
            + " every piece",
            "      // of evidence below — the AssertionError's own window summary, the hierarchy"
            + " dump, the",
            "      // screenshot — describing the launcher, and a healthy launcher window list"
            + " argues the",
            "      // exact opposite of the failure it was collected to explain.",
            "      if (attempt < LAUNCH_ATTEMPTS) {",
            '        kickWindowTracking("launch attempt $attempt timed out")',
            "      }",
            "    }",
            "    throw AssertionError(",
            '      "launch: no $PACKAGE window in the accessibility tree after $LAUNCH_ATTEMPTS '
            + 'attempt(s) " +',
            '        "of ${LAUNCH_TIMEOUT_MS}ms; windows:\\n" + windowSummary()',
            "    )",
            "  }",
            "",
            "  // Wait for an element before acting on it. findObject alone is a single-shot query"
            + " with no",
            "  // implicit wait, unlike the adb driver's tap() (its resolve step retries with a"
            + " timeout),",
            "  // so acting right after launch() or a UI transition can race the render — a"
            + " condition",
            "  // wait, never a fixed sleep. The failure names the windows it searched, so a missing"
            + " id and",
            "  // a missing app window are told apart from the message alone.",
            "  private fun act(by: BySelector): UiObject2 {",
            "    if (!waitPresent(by, ACT_TIMEOUT_MS)) {",
            "      throw AssertionError(",
            '        "act: no element matched $by within ${ACT_TIMEOUT_MS}ms; windows:\\n" +'
            + " windowSummary()",
            "      )",
            "    }",
            "    return device.findObject(by)",
            "  }",
            "",
            "  // Drop the accessibility node cache every selector read above goes through, so the"
            + " next one",
            "  // re-fetches from the app instead of re-reading what the last event left behind.",
            "  //",
            "  // hasObject / findObject / every Until condition built on them resolve through"
            + " the platform's",
            "  // per-connection AccessibilityNodeInfo cache, and only an accessibility event"
            + " invalidates it. A",
            "  // dropped event therefore does not merely delay a read — it pins it, and no"
            + " timeout recovers a",
            "  // read that will not change on its own (the same shape as the wedged window list"
            + " kicked above).",
            "  //",
            "  // androidx.test.uiautomator 2.3.0 cannot clear that cache itself past API 32 —"
            + " it reaches",
            '  // AccessibilityInteractionClient#clearCache() by reflection and logs "clearCache()'
            + " reflection is",
            '  // not available on API >= 33" instead. UiAutomation.clearCache(), the supported'
            + " replacement it",
            "  // predates, arrived in API 34, so below that there is no lever at all and callers"
            + " skip the",
            "  // re-read rather than pay for a call that does nothing.",
            "  //",
            "  // Reads through the one accessor above, for the reason recorded there. Never"
            + " throws: this runs",
            "  // to rescue a wait, so a fault here must cost the rescue and not the wait's own"
            + " verdict.",
            "  private fun clearAccessibilityCache() {",
            "    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) return",
            "    try {",
            "      uiAutomation().clearCache()",
            "    } catch (e: RuntimeException) {",
            '      Log.w(LOG_TAG, "could not clear the accessibility cache", e)',
            "    }",
            "  }",
            "",
            "  // Spend one wait's budget across several reads, dropping the cache between them, so"
            + " a pinned",
            "  // read cannot spend the whole timeout and be reported as the app's own failure.",
            "  //",
            "  // Still one condition wait, never a fixed sleep (prime directive #2): a slice"
            + " returns the",
            "  // instant its condition holds, the slices share the caller's single timeout rather"
            + " than",
            "  // extending it, and CACHE_REREAD_SLICE_MS bounds only how long a stale read stays"
            + " believed. A",
            "  // healthy wait therefore finishes exactly when it did before, paying at most a few"
            + " cache drops.",
            "  //",
            "  // Below API 34 the cache cannot be dropped, so slicing would add re-reads that"
            + " change nothing:",
            "  // spend the whole budget in one wait there, exactly as this file did before.",
            "  private fun waitSliced(timeoutMs: Long, poll: (Long) -> Boolean): Boolean {",
            "    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {",
            "      return poll(timeoutMs)",
            "    }",
            "    val deadline = SystemClock.uptimeMillis() + timeoutMs",
            "    while (true) {",
            "      // Poll first, then test the deadline. device.wait evaluates its condition once"
            + " before",
            "      // consulting the clock, so a 0ms budget still reads the tree there — and a"
            + " scenario can",
            "      // ask for one, since `timeout` is an unconstrained float that `ms()` truncates."
            + " Testing",
            "      // the deadline first would fail such a step against a screen it never looked at"
            + " on API",
            "      // 34 while the branch above still passed it, splitting one scenario's verdict by"
            + " API",
            "      // level. Costs a read, never a sleep, so this stays a condition wait.",
            "      val remaining = deadline - SystemClock.uptimeMillis()",
            "      if (poll(remaining.coerceIn(0L, CACHE_REREAD_SLICE_MS))) return true",
            "      // Re-read the clock rather than reusing `remaining`: the poll just spent part of"
            + " the",
            "      // budget, and a slice that consumed the rest must end the wait here instead of"
            + " paying",
            "      // one more cache drop and an empty final poll.",
            "      if (SystemClock.uptimeMillis() >= deadline) return false",
            "      clearAccessibilityCache()",
            "    }",
            "  }",
            "",
            "  // The two condition waits every generated step is built from, both sliced.",
            "  private fun waitPresent(by: BySelector, timeoutMs: Long): Boolean =",
            "    waitSliced(timeoutMs) { device.wait(Until.hasObject(by), it) }",
            "",
            "  private fun waitGone(by: BySelector, timeoutMs: Long): Boolean =",
            "    waitSliced(timeoutMs) { device.wait(Until.gone(by), it) }",
            "",
            "  // A `wait` step, failing by what it searched for rather than as a bare"
            + " AssertionError.",
            "  //",
            "  // assertTrue(device.wait(...)) used to be emitted directly, and a CI run showed"
            + " what that",
            '  // costs: "java.lang.AssertionError" and a line number, with no selector, no'
            + " timeout, and",
            "  // nothing to separate an element that never appeared from a read that never"
            + " caught up. The",
            "  // window summary is the same evidence act() attaches, for the same reason.",
            "  private fun awaitPresent(by: BySelector, timeoutMs: Long) {",
            "    if (waitPresent(by, timeoutMs)) return",
            "    throw AssertionError(",
            '      "wait: no element matched $by within ${timeoutMs}ms; windows:\\n" +'
            + " windowSummary()",
            "    )",
            "  }",
            "",
            "  private fun awaitGone(by: BySelector, timeoutMs: Long) {",
            "    if (waitGone(by, timeoutMs)) return",
            "    throw AssertionError(",
            '      "wait: an element still matched $by after ${timeoutMs}ms; windows:\\n" +'
            + " windowSummary()",
            "    )",
            "  }",
            "",
        ]

    def scenario_open(self, name: str) -> str:
        return f"  @Test\n  fun {ident(name)}() {{"

    def after_lines(self, after: list[AfterRule]) -> AfterEmission:
        # JUnit4's `@After` is per class, and reading the test's outcome inside it needs a
        # `TestWatcher` rule the generated class does not have. The language construct is per test
        # and needs neither: an Espresso/UI Automator assertion throws, so a `catch` sees the very
        # failure the verdict would have been and `finally` runs the cleanup on both paths.
        if not after:
            return AfterEmission()
        branching = any(rule.on != "always" for rule in after)
        prologue = (["var bajutsuFailed = false"] if branching else []) + ["try {"]
        epilogue = (
            [
                "} catch (bajutsuError: Throwable) {",
                *indent_lines(["bajutsuFailed = true", "throw bajutsuError"]),
                "} finally {",
            ]
            if branching
            else ["} finally {"]
        )
        for rule in after:
            lines = [line for step in rule.steps for line in _emit_step(step)]
            if rule.on == "always":
                epilogue.extend(indent_lines(lines))
            else:
                cond = "!bajutsuFailed" if rule.on == "success" else "bajutsuFailed"
                epilogue.extend(indent_lines([f"if ({cond}) {{"]))
                epilogue.extend(indent_lines(lines, 2))
                epilogue.extend(indent_lines(["}"]))
        epilogue.append("}")
        return AfterEmission(prologue, epilogue, 1)

    def setup_lines(self, scenario: Scenario) -> list[str]:
        # The mutable extras map the launch-env lines fill and `launch(extras)` consumes; always
        # emitted so a relaunch step can re-launch with the same env even when there is none.
        return [
            "val extras = mutableMapOf<String, String>()",
            *permissions_setup_lines(scenario),
            *interrupts_setup_lines(scenario),
        ]

    def launch_env_line(self, key: str, value: str) -> str:
        return f"extras[{_s(key)}] = {_s(value)}"

    def launch_line(self) -> str:
        return "launch(extras)"

    def step_lines(self, step: Step) -> list[str]:
        return _emit_step(step)

    def assertion_lines(self, assertion: Assertion) -> list[str]:
        return _emit_assertion(assertion)

    def scenario_close(self) -> str:
        return "  }"

    def file_footer(self) -> list[str]:
        return ["}"]


def to_uiautomator(
    scenarios: list[Scenario],
    class_name: str,
    package: str,
    app_launch_env: dict[str, str] | None = None,
) -> str:
    """Render scenarios as one instrumented test class with a `@Test` method per scenario."""
    return render_test_file(scenarios, app_launch_env, _UiAutomatorGen(class_name, package))


def class_name_for(stem: str) -> str:
    """Derive the Kotlin test-class name from a file stem (`…UITest`)."""
    return class_name(stem, "UITest")
