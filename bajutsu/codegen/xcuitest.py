"""Generate a native XCUITest (Swift) from a recorded scenario.

A passing scenario is the deterministic source of truth; emitting XCUITest lets a
team run the same flow in their existing Xcode / XCTest CI — no bajutsu runtime
or AI at test time, and XCUITest waits for hittability itself. The mapping is
purely structural (no AI).

Coverage: tap (by id or label) / doubleTap / type / longPress / swipe
(on+direction, or from/to as an XCUICoordinate drag) / pinch (withScale) /
rotate (radians) / wait (for, until gone) /
assertions (exists, notExists, value, label, enabled, disabled, selected, count).
Selectors map their compound forms too (BE-0026): a single id/label/idMatches/labelMatches
keeps its readable helper, while value / traits / index (or several fields together) compose
one NSPredicate query, a negative index counting from the live `count`. Only a `labelMatches`
regex, a `within` (geometric) scope, or an unknown trait stays unsupported.
A device-family or network construct with no on-device XCUITest form emits a labeled
`// TODO` rather than failing, so the output is always reviewable; a runtime-only
construct the shared walk cannot translate at all (`if` / `forEach` / `extract`) fails
loudly with a `CodegenError` instead of a silent no-op stub (BE-0297).
"""

from __future__ import annotations

from bajutsu.assertions import request_label
from bajutsu.codegen.common import (
    class_name,
    ident,
    is_plain_substring,
    manual_todo,
    network_unsupported,
    permissions_setup_lines,
    render_test_file,
)
from bajutsu.drivers import base
from bajutsu.scenario import Assertion, Gone, Scenario, Step, WaitRequest

# XCUITest drives the UI on-device and has no network-interception surface, so a network `request`
# assertion / `until: { request }` wait has no faithful translation — it stays a TODO, but a labeled
# one naming the endpoint and the reason (like the device-control steps), not a bare "unsupported".
# `request_label` (the same matcher description the runner / coverage use) names the endpoint.
_NO_NETWORK = network_unsupported("XCUITest")

_SWIFT_DIRECTION = {
    "up": "swipeUp",
    "down": "swipeDown",
    "left": "swipeLeft",
    "right": "swipeRight",
}


def _s(text: str) -> str:
    """A Swift double-quoted string literal."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


_UNSUPPORTED = 'el("UNSUPPORTED_SELECTOR")'
# Traits that map to an XCUIElement.ElementType (queryable as `elementType == <case>.rawValue`).
_TRAIT_ELEMENT_TYPE = {base.Trait.BUTTON: "button", base.Trait.LINK: "link"}


def _predicate(sel: base.Selector) -> tuple[str, list[str]] | None:
    """Build the NSPredicate (format, Swift args) ANDing every set field.

    None if any field has no faithful structural mapping (so the caller falls back to a
    TODO/unsupported marker).
    """
    clauses: list[str] = []
    args: list[str] = []
    # A generated test targets one platform, so an `id` / `idMatches` list of cross-platform OR
    # candidates (BE-0221) emits its primary (first) form — the id this platform actually surfaces.
    if "id" in sel:
        clauses.append("identifier == %@")
        args.append(_s(base.id_candidates(sel["id"])[0]))
    if "idMatches" in sel:
        clauses.append("identifier LIKE %@")
        args.append(_s(base.id_candidates(sel["idMatches"])[0]))
    if "label" in sel:
        clauses.append("label == %@")
        args.append(_s(sel["label"]))
    if "labelMatches" in sel:
        pattern = sel["labelMatches"]
        if not is_plain_substring(pattern):  # a real regex — no faithful NSPredicate form
            return None
        clauses.append("label CONTAINS %@")  # metacharacter-free: a plain substring (re.search)
        args.append(_s(pattern))
    if "value" in sel:
        clauses.append("value == %@")
        args.append(_s(sel["value"]))
    for trait in sel.get("traits", []):
        if trait in _TRAIT_ELEMENT_TYPE:
            clauses.append("elementType == %ld")
            args.append(f"XCUIElement.ElementType.{_TRAIT_ELEMENT_TYPE[trait]}.rawValue")
        elif trait == base.Trait.NOT_ENABLED:
            clauses.append("enabled == NO")
        elif trait == base.Trait.SELECTED:
            clauses.append("selected == YES")
        else:  # an unknown trait has no faithful query — don't broaden the match
            return None
    if not clauses:
        return None
    return " AND ".join(clauses), args


def _query(sel: base.Selector) -> str | None:
    """A Swift XCUIElementQuery expression for the selector.

    Scopes into a `within` container's subtree when present. None when the selector (or its
    container) can't be mapped faithfully.
    """
    if "within" in sel:
        # `within` is a *geometric* frame-containment constraint (the candidate's frame must sit
        # inside the container's; see drivers/base.py). XCUITest queries are tree-based, not
        # geometric, so there is no faithful structural form — it stays unsupported.
        return None
    base_query = "app.descendants(matching: .any)"
    pred = _predicate(sel)
    if pred is None:
        return None
    fmt, args = pred
    # Some clauses are self-contained (enabled == NO / selected == YES) and take no arg, so a
    # traits-only selector has an empty arg list — omit the trailing `, ` then (NSPredicate(format:)).
    predicate = (
        f"NSPredicate(format: {_s(fmt)}, {', '.join(args)})"
        if args
        else f"NSPredicate(format: {_s(fmt)})"
    )
    return f"{base_query}.matching({predicate})"


def _element(sel: base.Selector) -> str:
    """A Swift expression resolving to a single XCUIElement.

    Single addressing fields keep their readable helper; compound selectors (value / traits /
    `index`, or several fields) compose an NSPredicate query, picking the element by `index`
    (`element(boundBy:)`, a negative index offset from `count`) or `firstMatch`.
    """
    keys = set(sel)
    if keys == {"id"}:
        return f"el({_s(base.id_candidates(sel['id'])[0])})"
    if keys == {"label"}:
        return f"byLabel({_s(sel['label'])})"
    if keys == {"idMatches"}:
        return f"matchingId({_s(base.id_candidates(sel['idMatches'])[0])}).firstMatch"
    query = _query(sel)
    if query is None:
        return _UNSUPPORTED
    index = sel.get("index")
    if index is None:
        return f"{query}.firstMatch"
    if index < 0:
        # A negative index counts from the end (`candidates[i]` in drivers/base.py); `boundBy:`
        # takes no negative literal, so offset from the live `count` — `count - 1` is the last.
        return f"{query}.element(boundBy: {query}.count - {-index})"
    return f"{query}.element(boundBy: {index})"


def _count_expr(sel: base.Selector) -> str:
    if set(sel) == {"idMatches"}:
        return f"matchingId({_s(base.id_candidates(sel['idMatches'])[0])}).count"
    query = _query(sel)
    return f"{query}.count" if query is not None else "0"


def _emit_step(step: Step) -> list[str]:
    if step.tap is not None:
        return [f"{_element(step.tap.as_selector())}.tap()"]
    if step.double_tap is not None:
        return [f"{_element(step.double_tap.as_selector())}.doubleTap()"]
    if step.pinch is not None:
        # velocity sign must match the scale: positive zooms in, negative zooms out.
        velocity = 1.0 if step.pinch.scale >= 1 else -1.0
        return [
            f"{_element(step.pinch.sel.as_selector())}.pinch(withScale: {step.pinch.scale}, "
            f"velocity: {velocity})"
        ]
    if step.rotate is not None:
        return [
            f"{_element(step.rotate.sel.as_selector())}.rotate({step.rotate.radians}, "
            f"withVelocity: 1.0)"
        ]
    if step.long_press is not None:
        return [
            f"{_element(step.long_press.sel.as_selector())}.press(forDuration: {step.long_press.duration})"
        ]
    if step.type is not None:
        lines = []
        if step.type.into is not None:
            target = _element(step.type.into.as_selector())
            lines.append(f"{target}.tap()")
            lines.append(f"{target}.typeText({_s(step.type.text)})")
        else:
            lines.append(f"app.typeText({_s(step.type.text)})")
        return lines
    if step.clear is not None:
        # No XCUIElement "clear" primitive: focus, select-all, then delete the whole selection —
        # the faithful peer of the runner's focus-then-backspace clear (BE-0265).
        target = _element(step.clear.into.as_selector())
        return [
            f"{target}.tap()",
            f'{target}.typeKey("a", modifierFlags: .command)',
            f"{target}.typeText(XCUIKeyboardKey.delete.rawValue)",
        ]
    if step.delete is not None:
        # Focus, then type the delete key `count` times — one native backspace per key (BE-0265).
        target = _element(step.delete.into.as_selector())
        return [
            f"{target}.tap()",
            f"{target}.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, "
            f"count: {step.delete.count}))",
        ]
    if step.select is not None:
        # Focus, then Cmd+A selects the whole field (BE-0265).
        target = _element(step.select.into.as_selector())
        return [f"{target}.tap()", f'{target}.typeKey("a", modifierFlags: .command)']
    if step.copy_ is not None:
        return ['app.typeKey("c", modifierFlags: .command)']
    if step.back is not None:
        # iOS has no hardware back; the generated XCUITest taps the OS navigation back button, the
        # same element the XCUITest driver taps at runtime. Reuse the shared constant so codegen
        # cannot drift from the driver if the id ever changes (BE-0210).
        return [f"{_element({'id': base.OS_BACK_BUTTON})}.tap()"]
    if step.swipe is not None:
        sw = step.swipe
        if sw.on is not None and sw.direction is not None:
            return [f"{_element(sw.on.as_selector())}.{_SWIFT_DIRECTION[sw.direction]}()"]
        if sw.from_ is not None and sw.to is not None:
            # A coordinate drag via XCUICoordinate (BE-0025), using a short press duration so
            # SwiftUI reads it as a pan, not an instantaneous flick.
            (fx, fy), (tx, ty) = sw.from_, sw.to
            return [f"coord({fx}, {fy}).press(forDuration: 0.1, thenDragTo: coord({tx}, {ty}))"]
        return ["// TODO: coordinate swipe (from/to) is not generated"]
    if step.drag is not None:
        # XCUITest's `swipeX()` is a real drag, so an element-anchored `drag` (BE-0227) emits the
        # same primitive a directional `swipe` does — on iOS a drag both scrolls and moves handles.
        return [f"{_element(step.drag.on.as_selector())}.{_SWIFT_DIRECTION[step.drag.direction]}()"]
    if step.wait is not None:
        w = step.wait
        if w.for_ is not None:
            return [
                f"XCTAssertTrue({_element(w.for_.as_selector())}"
                f'.waitForExistence(timeout: {w.timeout}), "wait for element")'
            ]
        if isinstance(w.until, Gone):
            return [
                f"XCTAssertTrue({_element(w.until.gone.as_selector())}"
                f'.waitForNonExistence(timeout: {w.timeout}), "wait until gone")'
            ]
        if isinstance(w.until, WaitRequest):
            return [
                f"// TODO: wait until request ({request_label(w.until.request)}) — {_NO_NETWORK}"
            ]
        return [f"// settle wait ({w.until}) — XCUITest auto-waits for hittability"]
    if step.assert_ is not None:
        return [line for a in step.assert_ for line in _emit_assertion(a)]
    if step.relaunch is not None:
        return ["app.terminate()", "app.launch()"]
    if step.set_clipboard is not None:
        # simctl-backed in bajutsu; not currently generated for XCUITest (BE-0052).
        return [
            f"// TODO: setClipboard(text: {_s(step.set_clipboard.text)}) — simctl pbcopy; not generated"
        ]
    if step.foreground is not None:
        return ["// TODO: foreground() — simctl launch (resume); not generated"]
    if step.set_location is not None:
        # simctl-backed device control; no app-level XCUITest equivalent (BE-0026).
        loc = step.set_location
        return [
            f"// TODO: setLocation(lat: {loc.lat}, lon: {loc.lon}) — simctl location; not generated"
        ]
    if step.push is not None:
        return ["// TODO: push — simctl push (APNs payload); not generated"]
    if step.totp is not None:
        # A locally-computed RFC 6238 OTP into vars.*; no XCUITest equivalent (BE-0046).
        return [f"// TODO: totp(into: {step.totp.into.var}) — RFC 6238 OTP; not generated"]
    if step.email is not None:
        # Polls an HTTP mailbox in the bajutsu runner; no XCUITest equivalent (BE-0046).
        return [
            f"// TODO: email(into: {step.email.extract.var}) — poll mailbox + extract; not generated"
        ]
    if step.manual is not None:
        # A human takeover (BE-0185): an operation only a human can perform. No generated-test
        # equivalent — a labeled TODO the author wires (a bypass) or performs, never a silent skip.
        return [f"// TODO: manual step — {manual_todo(step.manual.label, step.manual.bypass)}"]
    return ["// TODO: unsupported step"]


def _emit_assertion(a: Assertion) -> list[str]:
    if a.exists is not None:
        element = _element(a.exists.sel.as_selector())
        check = "XCTAssertFalse" if a.exists.negate else "XCTAssertTrue"
        return [f"{check}({element}.exists)"]
    if a.value is not None:
        element = _element(a.value.sel.as_selector())
        actual = f'(({element}.value as? String) ?? "")'
        if a.value.equals is not None:
            return [f"XCTAssertEqual({element}.value as? String, {_s(a.value.equals)})"]
        if a.value.contains is not None:
            return [f"XCTAssertTrue({actual}.contains({_s(a.value.contains)}))"]
        return [
            f"XCTAssertNotNil({actual}.range(of: {_s(a.value.matches or '')}, options: .regularExpression))"
        ]
    if a.label is not None:
        element = _element(a.label.sel.as_selector())
        if a.label.equals is not None:
            return [f"XCTAssertEqual({element}.label, {_s(a.label.equals)})"]
        if a.label.contains is not None:
            return [f"XCTAssertTrue({element}.label.contains({_s(a.label.contains)}))"]
        return [
            f"XCTAssertNotNil({element}.label.range(of: {_s(a.label.matches or '')}, options: .regularExpression))"
        ]
    if a.enabled is not None:
        return [f"XCTAssertTrue({_element(a.enabled.as_selector())}.isEnabled)"]
    if a.disabled is not None:
        return [f"XCTAssertFalse({_element(a.disabled.as_selector())}.isEnabled)"]
    if a.selected is not None:
        return [f"XCTAssertTrue({_element(a.selected.as_selector())}.isSelected)"]
    if a.count is not None:
        expr = _count_expr(a.count.sel.as_selector())
        if a.count.equals is not None:
            return [f"XCTAssertEqual({expr}, {a.count.equals})"]
        if a.count.at_least is not None:
            return [f"XCTAssertGreaterThanOrEqual({expr}, {a.count.at_least})"]
        return [f"XCTAssertLessThanOrEqual({expr}, {a.count.at_most})"]
    if a.clipboard is not None:
        # simctl-level (pbpaste); no XCUITest equivalent, so emit a labeled TODO like the
        # device-state steps (BE-0052), naming the value asserted.
        op = "equals" if a.clipboard.equals is not None else "matches"
        want = a.clipboard.equals if a.clipboard.equals is not None else a.clipboard.matches
        return [f"// TODO: clipboard({op}: {_s(want or '')}) — simctl pbpaste; not generated"]
    if a.request is not None:
        # Match the runtime/coverage detail (`request_label` with count) so the TODO reads identically;
        # `count` is part of the assertion, so keep it.
        return [f"// TODO: request assertion ({request_label(a.request)}) — {_NO_NETWORK}"]
    if a.request_sequence is not None:
        # The sequence check is about order, not count — `with_count=False`, mirroring the runtime detail.
        seq = ", ".join(request_label(m, with_count=False) for m in a.request_sequence)
        return [f"// TODO: requestSequence assertion ({seq}) — {_NO_NETWORK}"]
    if a.response_schema is not None:
        return [
            f"// TODO: responseSchema assertion ({request_label(a.response_schema.request)}) — "
            f"{_NO_NETWORK}"
        ]
    return ["// TODO: unsupported assertion"]


class _XcuitestGen:
    """XCUITest target for the shared scenario walk (BE-0083): Swift/XCTest line syntax."""

    def __init__(self, class_name: str) -> None:
        self._class_name = class_name

    def file_preamble(self) -> list[str]:
        return [
            "// Generated by bajutsu — do not edit by hand. Re-generate with `bajutsu codegen`.",
            "import XCTest",
            "",
            f"final class {self._class_name}: XCTestCase {{",
            "  private let app = XCUIApplication()",
            "  private func el(_ id: String) -> XCUIElement {",
            "    app.descendants(matching: .any)[id]",
            "  }",
            "  private func byLabel(_ label: String) -> XCUIElement {",
            "    app.descendants(matching: .any).matching("
            + 'NSPredicate(format: "label == %@", label)).firstMatch',
            "  }",
            "  private func matchingId(_ glob: String) -> XCUIElementQuery {",
            "    app.descendants(matching: .any).matching("
            + 'NSPredicate(format: "identifier LIKE %@", glob))',
            "  }",
            "  private func coord(_ x: CGFloat, _ y: CGFloat) -> XCUICoordinate {",
            "    app.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))"
            + ".withOffset(CGVector(dx: x, dy: y))",
            "  }",
            "",
        ]

    # `app` is the instance property; XCTest makes a fresh test-case instance per method.
    def scenario_open(self, name: str) -> str:
        return f"  func {ident(name)}() {{"

    def setup_lines(self, scenario: Scenario) -> list[str]:
        # XCUITest has no network-interception surface, so there is no pre-launch observer to
        # install beyond the `permissions` TODO (BE-0276) below.
        return permissions_setup_lines(scenario)

    def launch_env_line(self, key: str, value: str) -> str:
        return f"app.launchEnvironment[{_s(key)}] = {_s(value)}"

    def launch_line(self) -> str:
        return "app.launch()"

    def step_lines(self, step: Step) -> list[str]:
        return _emit_step(step)

    def assertion_lines(self, assertion: Assertion) -> list[str]:
        return _emit_assertion(assertion)

    def scenario_close(self) -> str:
        return "  }"

    def file_footer(self) -> list[str]:
        return ["}"]


def to_xcuitest(
    scenarios: list[Scenario], class_name: str, app_launch_env: dict[str, str] | None = None
) -> str:
    """Render scenarios as one XCTestCase with a test method per scenario."""
    return render_test_file(scenarios, app_launch_env, _XcuitestGen(class_name))


def class_name_for(stem: str) -> str:
    """Derive the XCTestCase class name from a file stem (`…UITests`)."""
    return class_name(stem, "UITests")
