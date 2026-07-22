"""Generate a native UI Automator test (Kotlin) from a recorded scenario (BE-0209).

A passing scenario is the deterministic source of truth; emitting a UI Automator test lets a
team run the same flow in their existing Android instrumentation CI — no bajutsu runtime, no
adb driver of ours, and no AI at test time. The mapping is purely structural (no AI).

UI Automator is the closer twin of the adb backend (`drivers/adb.py`): both take a
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
    class_name,
    ident,
    is_plain_substring,
    manual_todo,
    ms,
    network_unsupported,
    permissions_setup_lines,
    render_test_file,
)
from bajutsu.drivers import base
from bajutsu.scenario import Assertion, Gone, Scenario, Step, WaitRequest
from bajutsu.scenario.models.assertions import CountMatch, TextMatch, Wait

# The adb backend has no network-interception surface (drivers/adb.py CAPABILITIES), so a network
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


def _unsupported_selector_todo(sel: base.Selector) -> str:
    """A labeled `// TODO` naming the selector fields that have no single UI Automator selector."""
    fields = ", ".join(sorted(sel))
    return (
        f"// TODO: unsupported selector ({fields}) — only a single id / label / value / "
        "idMatches / labelMatches maps to a UI Automator BySelector"
    )


def _act(sel: base.Selector, call: str) -> list[str]:
    """A `device.findObject(<by>).<call>` line, or a TODO when the selector can't be rendered."""
    by = _by(sel)
    if by is None:
        return [_unsupported_selector_todo(sel)]
    return [f"device.findObject({by}).{call}"]


def _emit_step(step: Step) -> list[str]:
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
            f"device.findObject({by}).click()",
            f"repeat({step.delete.count}) {{ device.pressKeyCode(KeyEvent.KEYCODE_DEL) }}",
        ]
    if step.select is not None:
        by = _by(step.select.into.as_selector())
        if by is None:
            return [_unsupported_selector_todo(step.select.into.as_selector())]
        # Focus, then Ctrl+A selects the whole field (BE-0265).
        return [
            f"device.findObject({by}).click()",
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
    if step.manual is not None:
        # A human takeover (BE-0185): no generated-test equivalent — a labeled TODO, not a silent skip.
        return f"// TODO: manual step — {manual_todo(step.manual.label, step.manual.bypass)}"
    return "// TODO: unsupported step"


def _emit_wait(w: Wait) -> list[str]:
    """The lines for a `wait` step: an existence / gone poll to the step's timeout, or a comment."""
    timeout = ms(w.timeout)
    if w.for_ is not None:
        by = _by(w.for_.as_selector())
        if by is None:
            return [_unsupported_selector_todo(w.for_.as_selector())]
        return [f"assertTrue(device.wait(Until.hasObject({by}), {timeout}L))"]
    if isinstance(w.until, Gone):
        by = _by(w.until.gone.as_selector())
        if by is None:
            return [_unsupported_selector_todo(w.until.gone.as_selector())]
        return [f"assertTrue(device.wait(Until.gone({by}), {timeout}L))"]
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
            "import android.view.KeyEvent",
            "import androidx.test.core.app.ApplicationProvider",
            "import androidx.test.ext.junit.runners.AndroidJUnit4",
            "import androidx.test.platform.app.InstrumentationRegistry",
            "import androidx.test.uiautomator.By",
            "import androidx.test.uiautomator.Direction",
            "import androidx.test.uiautomator.UiDevice",
            "import androidx.test.uiautomator.Until",
            "import org.junit.Assert.assertEquals",
            "import org.junit.Assert.assertFalse",
            "import org.junit.Assert.assertTrue",
            "import org.junit.Test",
            "import org.junit.runner.RunWith",
            "import java.util.regex.Pattern",
            "",
            f"private const val PACKAGE = {_s(self._package)}",
            "private const val LAUNCH_TIMEOUT_MS = 5000L",
            "",
            "@RunWith(AndroidJUnit4::class)",
            f"class {self._class_name} {{",
            "  private val device = UiDevice.getInstance("
            + "InstrumentationRegistry.getInstrumentation())",
            "",
            "  // Match the local id whether or not the app namespaces it with a `<package>:id/`"
            + " prefix —",
            "  // the reverse of the adb driver stripping that prefix (drivers/adb.py).",
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
            "  // Launch (or relaunch) the app, forwarding launchEnv as intent extras (the reverse"
            + " of the",
            "  // adb backend's `am start --es`); waits for the app's first window, never sleeps.",
            "  private fun launch(extras: Map<String, String>) {",
            "    val context = ApplicationProvider.getApplicationContext<Context>()",
            "    val intent = context.packageManager.getLaunchIntentForPackage(PACKAGE)!!",
            "      .apply { addFlags(Intent.FLAG_ACTIVITY_CLEAR_TASK) }",
            "    for ((k, v) in extras) intent.putExtra(k, v)",
            "    context.startActivity(intent)",
            "    device.wait(Until.hasObject(By.pkg(PACKAGE).depth(0)), LAUNCH_TIMEOUT_MS)",
            "  }",
            "",
        ]

    def scenario_open(self, name: str) -> str:
        return f"  @Test\n  fun {ident(name)}() {{"

    def setup_lines(self, scenario: Scenario) -> list[str]:
        # The mutable extras map the launch-env lines fill and `launch(extras)` consumes; always
        # emitted so a relaunch step can re-launch with the same env even when there is none.
        return ["val extras = mutableMapOf<String, String>()", *permissions_setup_lines(scenario)]

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
