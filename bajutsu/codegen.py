"""Generate a native XCUITest (Swift) from a recorded scenario.

A passing scenario is the deterministic source of truth; emitting XCUITest lets a
team run the same flow in their existing Xcode / XCTest CI — no bajutsu runtime,
idb, or AI at test time, and XCUITest waits for hittability itself. The mapping is
purely structural (no AI).

Coverage: tap (by id or label) / type / longPress / swipe (on+direction) /
wait (for, until gone) / assertions (exists, notExists, value, label, enabled,
disabled, selected, count). Unsupported constructs emit a `// TODO` line rather
than failing, so the output is always reviewable.
"""

from __future__ import annotations

import re

from bajutsu.drivers import base
from bajutsu.scenario import Assertion, Gone, Scenario, Step

_SWIFT_DIRECTION = {"up": "swipeUp", "down": "swipeDown", "left": "swipeLeft", "right": "swipeRight"}


def _s(text: str) -> str:
    """A Swift double-quoted string literal."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _ident(name: str) -> str:
    """Turn a scenario name into a Swift test-method identifier."""
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_")
    if not cleaned:
        cleaned = "scenario"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return f"test_{cleaned}"


def _class_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z]+", " ", name).title().replace(" ", "")
    if not cleaned:
        cleaned = "Generated"
    return f"{cleaned}UITests"


def _element(sel: base.Selector) -> str:
    """A Swift expression resolving to a single XCUIElement."""
    if "id" in sel:
        return f"el({_s(sel['id'])})"
    if "label" in sel:
        return f"byLabel({_s(sel['label'])})"
    if "idMatches" in sel:
        return f"matchingId({_s(sel['idMatches'])}).firstMatch"
    if "labelMatches" in sel:
        pattern = f"*{sel['labelMatches']}*"
        return (
            "app.descendants(matching: .any)"
            f".matching(NSPredicate(format: \"label LIKE %@\", {_s(pattern)})).firstMatch"
        )
    return 'el("UNSUPPORTED_SELECTOR")'


def _count_expr(sel: base.Selector) -> str:
    if "idMatches" in sel:
        return f"matchingId({_s(sel['idMatches'])}).count"
    if "id" in sel:
        return f"({_element(sel)}.exists ? 1 : 0)"
    return "0"


def _selector_of_step(step: Step) -> base.Selector | None:
    if step.tap is not None:
        return step.tap.as_selector()
    return None


def _emit_step(step: Step) -> list[str]:  # noqa: C901 — a flat dispatch over step kinds
    if step.tap is not None:
        return [f"{_element(step.tap.as_selector())}.tap()"]
    if step.long_press is not None:
        return [f"{_element(step.long_press.sel.as_selector())}.press(forDuration: {step.long_press.duration})"]
    if step.type is not None:
        lines = []
        if step.type.into is not None:
            target = _element(step.type.into.as_selector())
            lines.append(f"{target}.tap()")
            lines.append(f"{target}.typeText({_s(step.type.text)})")
        else:
            lines.append(f"app.typeText({_s(step.type.text)})")
        return lines
    if step.swipe is not None:
        sw = step.swipe
        if sw.on is not None and sw.direction is not None:
            return [f"{_element(sw.on.as_selector())}.{_SWIFT_DIRECTION[sw.direction]}()"]
        return ["// TODO: coordinate swipe (from/to) is not generated"]
    if step.wait is not None:
        w = step.wait
        if w.for_ is not None:
            return [
                f"XCTAssertTrue({_element(w.for_.as_selector())}"
                f".waitForExistence(timeout: {w.timeout}), \"wait for element\")"
            ]
        if isinstance(w.until, Gone):
            return [
                f"XCTAssertTrue({_element(w.until.gone.as_selector())}"
                f".waitForNonExistence(timeout: {w.timeout}), \"wait until gone\")"
            ]
        return [f"// settle wait ({w.until}) — XCUITest auto-waits for hittability"]
    if step.assert_ is not None:
        return [line for a in step.assert_ for line in _emit_assertion(a)]
    if step.relaunch is not None:
        return ["app.terminate()", "app.launch()"]
    return ["// TODO: unsupported step"]


def _emit_assertion(a: Assertion) -> list[str]:  # noqa: C901 — flat dispatch over assertion kinds
    if a.exists is not None:
        element = _element(a.exists.sel.as_selector())
        check = "XCTAssertFalse" if a.exists.negate else "XCTAssertTrue"
        return [f"{check}({element}.exists)"]
    if a.value is not None:
        element = _element(a.value.sel.as_selector())
        actual = f"(({element}.value as? String) ?? \"\")"
        if a.value.equals is not None:
            return [f"XCTAssertEqual({element}.value as? String, {_s(a.value.equals)})"]
        if a.value.contains is not None:
            return [f"XCTAssertTrue({actual}.contains({_s(a.value.contains)}))"]
        return [f"XCTAssertNotNil({actual}.range(of: {_s(a.value.matches or '')}, options: .regularExpression))"]
    if a.label is not None:
        element = _element(a.label.sel.as_selector())
        if a.label.equals is not None:
            return [f"XCTAssertEqual({element}.label, {_s(a.label.equals)})"]
        if a.label.contains is not None:
            return [f"XCTAssertTrue({element}.label.contains({_s(a.label.contains)}))"]
        return [f"XCTAssertNotNil({element}.label.range(of: {_s(a.label.matches or '')}, options: .regularExpression))"]
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
    return ["// TODO: unsupported assertion"]


def _emit_scenario(scenario: Scenario, app_launch_env: dict[str, str]) -> list[str]:
    env = {**app_launch_env, **scenario.preconditions.launch_env}
    # `app` is the instance property; XCTest makes a fresh test-case instance per method.
    lines = [f"  func {_ident(scenario.name)}() {{"]
    for key, value in env.items():
        lines.append(f"    app.launchEnvironment[{_s(key)}] = {_s(value)}")
    lines.append("    app.launch()")
    lines.append("")
    for step in scenario.steps:
        for line in _emit_step(step):
            lines.append(f"    {line}")
    if scenario.expect:
        lines.append("")
        lines.append("    // expect")
        for assertion in scenario.expect:
            for line in _emit_assertion(assertion):
                lines.append(f"    {line}")
    lines.append("  }")
    return lines


def to_xcuitest(
    scenarios: list[Scenario], class_name: str, app_launch_env: dict[str, str] | None = None
) -> str:
    """Render scenarios as one XCTestCase with a test method per scenario."""
    env = app_launch_env or {}
    body: list[str] = [
        "// Generated by bajutsu — do not edit by hand. Re-generate with `bajutsu codegen`.",
        "import XCTest",
        "",
        f"final class {class_name}: XCTestCase {{",
        "  private let app = XCUIApplication()",
        "  private func el(_ id: String) -> XCUIElement {",
        "    app.descendants(matching: .any)[id]",
        "  }",
        "  private func byLabel(_ label: String) -> XCUIElement {",
        "    app.descendants(matching: .any).matching("
        "NSPredicate(format: \"label == %@\", label)).firstMatch",
        "  }",
        "  private func matchingId(_ glob: String) -> XCUIElementQuery {",
        "    app.descendants(matching: .any).matching("
        "NSPredicate(format: \"identifier LIKE %@\", glob))",
        "  }",
        "",
    ]
    for scenario in scenarios:
        body.extend(_emit_scenario(scenario, env))
        body.append("")
    body.append("}")
    return "\n".join(body) + "\n"


def class_name_for(stem: str) -> str:
    return _class_name(stem)
