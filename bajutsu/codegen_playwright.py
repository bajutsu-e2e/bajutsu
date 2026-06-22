"""Generate a native Playwright test (TypeScript) from a recorded scenario.

A passing scenario is the deterministic source of truth; emitting a Playwright test lets a
team run the same flow in their existing Playwright CI — no bajutsu runtime, no browser
driver of ours, and no AI at test time. The mapping is purely structural (no AI).

The emitted test uses Playwright's *semantic* locators (`getByTestId` / `getByRole`) and
web-first assertions (`expect(...).toBeVisible()`), which auto-wait and retry — the opposite
of the `run` driver, which coordinate-clicks through the shared resolver. That is correct:
the destination framework is the runtime, so the test must speak its idiom, and determinism
in the handoff artifact is owned by Playwright's auto-waiting (the same split the XCUITest
emitter makes). The only fixed timings emitted are gesture durations.

Unsupported constructs emit a `// TODO` line rather than failing, so the output is always
reviewable.
"""

from __future__ import annotations

import re

from bajutsu.drivers import base
from bajutsu.scenario import Assertion, Gone, Scenario, Step
from bajutsu.scenario.models.assertions import CountMatch, TextMatch, Wait, WaitRequest

# Element-center drag distance (px) for a directional swipe — intrinsic to the gesture.
_SWIPE_PX = 100
_SWIPE_DELTA = {
    "up": (0, -_SWIPE_PX),
    "down": (0, _SWIPE_PX),
    "left": (-_SWIPE_PX, 0),
    "right": (_SWIPE_PX, 0),
}

# Glob metacharacters a CSS attribute operator cannot express (single-char `?`, char classes,
# and interior `*`); a glob carrying any of these falls back to `// TODO`.
_GLOB_UNSUPPORTED = set("?[]")
# Regex metacharacters to escape when turning a `contains` substring into a JS RegExp literal.
_RE_SPECIAL = re.compile(r"[.*+?^${}()|[\]\\/]")
# Selector fields the emitter cannot represent as an AND-constraint on a Playwright locator.
# A selector carrying either (beside its primary field) is rendered as `// TODO` rather than
# silently dropped, which would target a broader element set than the scenario means.
_UNSUPPORTED_FIELDS = ("within", "value")


def _ts(text: str) -> str:
    """A TypeScript single-quoted string literal."""
    escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return f"'{escaped}'"


def _css_attr_value(value: str) -> str:
    """Escape a string for use inside a double-quoted CSS attribute-selector value."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _re_contains(text: str) -> str:
    """A JS RegExp literal matching `text` as a literal substring."""
    return "/" + _RE_SPECIAL.sub(lambda m: "\\" + m.group(0), text) + "/"


def _re_raw(pattern: str) -> str:
    """A JS RegExp literal from an already-regex pattern (only `/` needs escaping)."""
    return "/" + pattern.replace("/", "\\/") + "/"


def _glob_to_css(glob: str) -> str | None:
    """Render an `idMatches` fnmatch glob as a `data-testid` CSS attribute selector.

    A leading/trailing `*` maps to the `$=` / `^=` / `*=` operators; an exact glob to `=`. A glob
    with an interior `*`, a `?`, or a `[…]` class has no CSS equivalent and returns None (→ TODO).
    """
    if any(c in _GLOB_UNSUPPORTED for c in glob):
        return None
    lead, trail = glob.startswith("*"), glob.endswith("*")
    core = glob.strip("*")
    if not core or "*" in core:  # empty, or an interior `*` a CSS operator cannot express
        return None
    op = {(True, True): "*=", (False, True): "^=", (True, False): "$="}.get((lead, trail), "=")
    return f'[data-testid{op}"{_css_attr_value(core)}"]'


def describe_name_for(stem: str) -> str:
    """Humanize a file stem into a `test.describe(...)` group name."""
    cleaned = re.sub(r"[^0-9a-zA-Z]+", " ", stem).strip().title()
    return cleaned or "Generated"


def _primary_locator(sel: base.Selector) -> str | None:
    """The base Playwright locator for a selector's primary field, before `index` narrowing."""
    if "id" in sel:
        return f"page.getByTestId({_ts(sel['id'])})"
    traits = sel.get("traits")
    if "label" in sel:
        if traits:
            return f"page.getByRole({_ts(traits[0])}, {{ name: {_ts(sel['label'])}, exact: true }})"
        return f"page.getByText({_ts(sel['label'])}, {{ exact: true }})"
    if traits:
        return f"page.getByRole({_ts(traits[0])})"
    if "idMatches" in sel:
        css = _glob_to_css(sel["idMatches"])
        return f"page.locator('{css}')" if css is not None else None
    if "labelMatches" in sel:
        # `labelMatches` is a regex (re.search); a JS RegExp preserves that, unlike a plain string.
        return f"page.getByText({_re_raw(sel['labelMatches'])})"
    return None


def _locator(sel: base.Selector) -> str | None:
    """A Playwright locator expression for one element, or None if not faithfully renderable.

    Selector fields are AND-ed. `index` narrows with `.nth()`; `within` / `value` have no faithful
    Playwright equivalent here, so a selector carrying either is left unsupported (→ TODO) rather
    than rendered as a broader match that drops the constraint.
    """
    if any(field in sel for field in _UNSUPPORTED_FIELDS):
        return None
    loc = _primary_locator(sel)
    if loc is None:
        return None
    if "index" in sel:
        loc += f".nth({sel['index']})"
    return loc


def _act(sel: base.Selector, call: str) -> list[str]:
    """A `await <locator>.<call>;` line, or a TODO when the selector can't be rendered."""
    loc = _locator(sel)
    if loc is None:
        return ["// TODO: unsupported selector"]
    return [f"await {loc}.{call};"]


def _ms(seconds: float) -> int:
    return int(seconds * 1000)


def _emit_swipe_direction(loc: str, direction: str) -> list[str]:
    dx, dy = _SWIPE_DELTA[direction]
    # A block scope keeps `const box` re-declarable across multiple swipes in one test.
    return [
        "{",
        f"  const box = await {loc}.boundingBox();",
        "  if (box) {",
        "    const cx = box.x + box.width / 2;",
        "    const cy = box.y + box.height / 2;",
        "    await page.mouse.move(cx, cy);",
        "    await page.mouse.down();",
        f"    await page.mouse.move(cx + {dx}, cy + {dy}, {{ steps: 10 }});",
        "    await page.mouse.up();",
        "  }",
        "}",
    ]


def _emit_step(step: Step) -> list[str]:
    if step.tap is not None:
        return _act(step.tap.as_selector(), "click()")
    if step.double_tap is not None:
        return _act(step.double_tap.as_selector(), "dblclick()")
    if step.long_press is not None:
        return _act(
            step.long_press.sel.as_selector(),
            f"click({{ delay: {_ms(step.long_press.duration)} }})",
        )
    if step.type is not None:
        if step.type.into is not None:
            return _act(step.type.into.as_selector(), f"fill({_ts(step.type.text)})")
        return [f"await page.keyboard.type({_ts(step.type.text)});"]
    if step.swipe is not None:
        sw = step.swipe
        if sw.on is not None and sw.direction is not None:
            loc = _locator(sw.on.as_selector())
            if loc is None:
                return ["// TODO: unsupported selector"]
            return _emit_swipe_direction(loc, sw.direction)
        return ["// TODO: coordinate swipe (from/to) is not generated"]
    if step.wait is not None:
        return _emit_wait(step.wait)
    if step.pinch is not None:
        return ["// TODO: multi-touch (pinch) is not generated for web"]
    if step.rotate is not None:
        return ["// TODO: multi-touch (rotate) is not generated for web"]
    if step.relaunch is not None:
        return ["await page.goto(BASE_URL);"]
    if step.assert_ is not None:
        return [line for a in step.assert_ for line in _emit_assertion(a)]
    return ["// TODO: unsupported step"]


def _emit_wait(w: Wait) -> list[str]:
    timeout = _ms(w.timeout)
    if w.for_ is not None:
        loc = _locator(w.for_.as_selector())
        if loc is None:
            return ["// TODO: unsupported selector"]
        return [f"await expect({loc}).toBeVisible({{ timeout: {timeout} }});"]
    if isinstance(w.until, Gone):
        loc = _locator(w.until.gone.as_selector())
        if loc is None:
            return ["// TODO: unsupported selector"]
        return [f"await expect({loc}).toBeHidden({{ timeout: {timeout} }});"]
    if isinstance(w.until, WaitRequest):
        return ["// TODO: wait until network request (no bajutsu runtime in the emitted test)"]
    # "screenChanged" / "settled" — Playwright auto-waits, so a comment suffices.
    return [f"// {w.until} — Playwright auto-waits"]


def _emit_assertion(a: Assertion) -> list[str]:
    if a.exists is not None:
        loc = _locator(a.exists.sel.as_selector())
        if loc is None:
            return ["// TODO: unsupported selector"]
        check = "toBeHidden()" if a.exists.negate else "toBeVisible()"
        return [f"await expect({loc}).{check};"]
    if a.value is not None:
        return _emit_text_match(a.value, "toHaveValue", "toHaveValue")
    if a.label is not None:
        return _emit_text_match(a.label, "toHaveText", "toContainText")
    if a.enabled is not None:
        return _expect(a.enabled.as_selector(), "toBeEnabled()")
    if a.disabled is not None:
        return _expect(a.disabled.as_selector(), "toBeDisabled()")
    if a.selected is not None:
        return _expect(a.selected.as_selector(), "toBeChecked()")
    if a.count is not None:
        return _emit_count(a.count)
    if a.request is not None:
        return ["// TODO: network 'request' assertion (no bajutsu runtime in the emitted test)"]
    if a.visual is not None:
        return ["// TODO: visual assertion is not generated"]
    return ["// TODO: unsupported assertion"]


def _expect(sel: base.Selector, matcher: str) -> list[str]:
    loc = _locator(sel)
    if loc is None:
        return ["// TODO: unsupported selector"]
    return [f"await expect({loc}).{matcher};"]


def _emit_text_match(m: TextMatch, equals_matcher: str, contains_matcher: str) -> list[str]:
    loc = _locator(m.sel.as_selector())
    if loc is None:
        return ["// TODO: unsupported selector"]
    if m.equals is not None:
        return [f"await expect({loc}).{equals_matcher}({_ts(m.equals)});"]
    if m.contains is not None:
        if contains_matcher == "toContainText":
            return [f"await expect({loc}).toContainText({_ts(m.contains)});"]
        return [f"await expect({loc}).{equals_matcher}({_re_contains(m.contains)});"]
    return [f"await expect({loc}).{equals_matcher}({_re_raw(m.matches or '')});"]


def _emit_count(c: CountMatch) -> list[str]:
    loc = _locator(c.sel.as_selector())
    if loc is None:
        return ["// TODO: unsupported selector"]
    if c.equals is not None:
        return [f"await expect({loc}).toHaveCount({c.equals});"]
    if c.at_least is not None:
        return [f"expect(await {loc}.count()).toBeGreaterThanOrEqual({c.at_least});"]
    return [f"expect(await {loc}.count()).toBeLessThanOrEqual({c.at_most});"]


def _emit_scenario(scenario: Scenario, app_launch_env: dict[str, str]) -> list[str]:
    env = {**app_launch_env, **scenario.preconditions.launch_env}
    lines = [f"  test({_ts(scenario.name)}, async ({{ page }}) => {{"]
    for key, value in env.items():
        lines.append(
            f"    await page.addInitScript(() => localStorage.setItem({_ts(key)}, {_ts(value)}));"
        )
    lines.append("    await page.goto(BASE_URL);")
    lines.append("")
    for step in scenario.steps:
        lines.extend(f"    {line}" for line in _emit_step(step))
    if scenario.expect:
        lines.append("")
        lines.append("    // expect")
        for assertion in scenario.expect:
            lines.extend(f"    {line}" for line in _emit_assertion(assertion))
    lines.append("  });")
    return lines


def to_playwright(
    scenarios: list[Scenario],
    describe_name: str,
    base_url: str,
    app_launch_env: dict[str, str] | None = None,
) -> str:
    """Render scenarios as one `test.describe` block with a `test(...)` per scenario."""
    env = app_launch_env or {}
    body: list[str] = [
        "// Generated by bajutsu — do not edit by hand. Re-generate with `bajutsu codegen`.",
        "import { test, expect } from '@playwright/test';",
        "",
        f"const BASE_URL = {_ts(base_url)};",
        "",
        f"test.describe({_ts(describe_name)}, () => {{",
    ]
    for scenario in scenarios:
        body.extend(_emit_scenario(scenario, env))
        body.append("")
    body.append("});")
    return "\n".join(body) + "\n"
