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

from bajutsu.assertions import request_label
from bajutsu.codegen_common import render_test_file
from bajutsu.drivers import base
from bajutsu.scenario import Assertion, Gone, RequestMatch, Scenario, Step
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


# Playwright observes network natively, so a request matcher maps to a `waitForRequest` /
# `waitForResponse` predicate (BE-0085) rather than a labeled TODO — the opposite of the XCUITest
# emitter, which has no interception surface. `status` is the only response-only field, so a matcher
# carrying it must wait for the *response* (`waitForResponse`, predicate over a `Response`); otherwise
# the request side alone suffices (`waitForRequest`, predicate over a `Request`). `bodyMatches` is a
# regex over the *request* body in the runner (`match_request` tests `ex.request_body`), so it always
# maps to the request's `postData()` — never `response.text()` — regardless of which call we emit.


def _re_test_js(pattern: str, subject: str) -> str:
    """A JS `regexp.test(subject)` for a `re.search`-style matcher (substring or regex)."""
    return f"{_re_raw(pattern)}.test({subject})"


def _request_predicate(req: RequestMatch, *, on_response: bool) -> str:
    """The JS boolean predicate body for a request matcher, over `r` (a Request or Response).

    `on_response` selects the accessor shape: a `Response` reaches the request via `r.request()`,
    a `Request` is `r` itself. Only set matcher fields are emitted, AND-ed, mirroring `match_request`.
    """
    req_ref = "r.request()" if on_response else "r"
    method = f"{req_ref}.method()"
    post_data = f"{req_ref}.postData() ?? ''"  # null when no body; an empty string matches nothing
    pathname = "new URL(r.url()).pathname"  # `path` is path-only in the runner, so drop the query
    clauses: list[str] = []
    if req.method is not None:
        clauses.append(f"{method} === {_ts(req.method.upper())}")
    if req.url is not None:
        clauses.append(f"r.url() === {_ts(req.url)}")
    if req.url_matches is not None:
        clauses.append(_re_test_js(req.url_matches, "r.url()"))
    if req.path is not None:
        clauses.append(f"{pathname} === {_ts(req.path)}")
    if req.path_matches is not None:
        clauses.append(_re_test_js(req.path_matches, pathname))
    if req.status is not None:
        clauses.append(f"r.status() === {req.status}")
    if req.body_matches is not None:
        clauses.append(_re_test_js(req.body_matches, post_data))
    return " && ".join(clauses)


def _emit_request_wait(req: RequestMatch, timeout_ms: int | None = None) -> list[str]:
    """A `waitForResponse` / `waitForRequest` over one request matcher, optionally timeout-bounded.

    A labeled comment (the shared `request_label`) precedes it so the generated test reads the same
    way across backends and a reviewer sees the endpoint at a glance.
    """
    on_response = req.status is not None
    call = "waitForResponse" if on_response else "waitForRequest"
    predicate = _request_predicate(req, on_response=on_response)
    timeout = f", {{ timeout: {timeout_ms} }}" if timeout_ms is not None else ""
    return [
        f"// request {request_label(req)}",
        f"await page.{call}(r => {predicate}{timeout});",
    ]


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


# Why a selector field has no faithful Playwright locator (BE-0085) — named in the labeled TODO so a
# reviewer sees *which* field blocked the locator and *why*, rather than a bare "unsupported selector".
_WITHIN_REASON = "geometric frame containment, not a Playwright locator scope"
_VALUE_REASON = "no Playwright locator constrains an element's current value"
_GLOB_REASON = "fnmatch glob has no CSS attribute-selector equivalent (interior `*`, `?`, or `[…]`)"


def _unsupported_selector_todo(sel: base.Selector) -> str:
    """A labeled `// TODO` naming which selector field has no Playwright locator, and why."""
    if "within" in sel:
        field, reason = "within", _WITHIN_REASON
    elif "value" in sel:
        field, reason = "value", _VALUE_REASON
    else:  # an `idMatches` glob the CSS attribute operators cannot express
        field, reason = "idMatches", _GLOB_REASON
    return f"// TODO: unsupported selector ('{field}': {reason})"


def _act(sel: base.Selector, call: str) -> list[str]:
    """A `await <locator>.<call>;` line, or a TODO when the selector can't be rendered."""
    loc = _locator(sel)
    if loc is None:
        return [_unsupported_selector_todo(sel)]
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
            sel = sw.on.as_selector()
            loc = _locator(sel)
            if loc is None:
                return [_unsupported_selector_todo(sel)]
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
        sel = w.for_.as_selector()
        loc = _locator(sel)
        if loc is None:
            return [_unsupported_selector_todo(sel)]
        return [f"await expect({loc}).toBeVisible({{ timeout: {timeout} }});"]
    if isinstance(w.until, Gone):
        sel = w.until.gone.as_selector()
        loc = _locator(sel)
        if loc is None:
            return [_unsupported_selector_todo(sel)]
        return [f"await expect({loc}).toBeHidden({{ timeout: {timeout} }});"]
    if isinstance(w.until, WaitRequest):
        # Playwright observes network natively, so the wait maps to the same request predicate,
        # bounded by the step's timeout (BE-0085).
        return _emit_request_wait(w.until.request, timeout)
    # "screenChanged" / "settled" — Playwright auto-waits, so a comment suffices.
    return [f"// {w.until} — Playwright auto-waits"]


def _emit_assertion(a: Assertion) -> list[str]:
    if a.exists is not None:
        sel = a.exists.sel.as_selector()
        loc = _locator(sel)
        if loc is None:
            return [_unsupported_selector_todo(sel)]
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
        return _emit_request_wait(a.request)
    if a.request_sequence is not None:
        # The sequence check is about order, so emit one awaited matcher per element in order,
        # mirroring the runtime forward scan (BE-0085).
        seq = " → ".join(request_label(m, with_count=False) for m in a.request_sequence)
        return [f"// requestSequence {seq}"] + [
            line
            for m in a.request_sequence
            # The per-element label is redundant under the sequence header, so drop it.
            for line in _emit_request_wait(m)[1:]
        ]
    if a.response_schema is not None:
        # Validating a body against a JSON Schema needs a schema library in the emitted test (an
        # external dependency the generated file shouldn't assume), so this stays a labeled TODO
        # naming the endpoint and the schema file (BE-0085), like the XCUITest network TODOs.
        m = a.response_schema
        return [
            f"// TODO: responseSchema assertion ({request_label(m.request)} ~ {m.schema_path}) — "
            "validating a JSON Schema needs a schema library in the test; not generated"
        ]
    if a.visual is not None:
        return ["// TODO: visual assertion is not generated"]
    return ["// TODO: unsupported assertion"]


def _expect(sel: base.Selector, matcher: str) -> list[str]:
    loc = _locator(sel)
    if loc is None:
        return [_unsupported_selector_todo(sel)]
    return [f"await expect({loc}).{matcher};"]


def _emit_text_match(m: TextMatch, equals_matcher: str, contains_matcher: str) -> list[str]:
    sel = m.sel.as_selector()
    loc = _locator(sel)
    if loc is None:
        return [_unsupported_selector_todo(sel)]
    if m.equals is not None:
        return [f"await expect({loc}).{equals_matcher}({_ts(m.equals)});"]
    if m.contains is not None:
        if contains_matcher == "toContainText":
            return [f"await expect({loc}).toContainText({_ts(m.contains)});"]
        return [f"await expect({loc}).{equals_matcher}({_re_contains(m.contains)});"]
    return [f"await expect({loc}).{equals_matcher}({_re_raw(m.matches or '')});"]


def _emit_count(c: CountMatch) -> list[str]:
    sel = c.sel.as_selector()
    loc = _locator(sel)
    if loc is None:
        return [_unsupported_selector_todo(sel)]
    if c.equals is not None:
        return [f"await expect({loc}).toHaveCount({c.equals});"]
    if c.at_least is not None:
        return [f"expect(await {loc}.count()).toBeGreaterThanOrEqual({c.at_least});"]
    return [f"expect(await {loc}.count()).toBeLessThanOrEqual({c.at_most});"]


class _PlaywrightGen:
    """Playwright target for the shared scenario walk (BE-0083): TypeScript/Playwright line syntax."""

    def __init__(self, describe_name: str, base_url: str) -> None:
        self._describe_name = describe_name
        self._base_url = base_url

    def file_preamble(self) -> list[str]:
        return [
            "// Generated by bajutsu — do not edit by hand. Re-generate with `bajutsu codegen`.",
            "import { test, expect } from '@playwright/test';",
            "",
            f"const BASE_URL = {_ts(self._base_url)};",
            "",
            f"test.describe({_ts(self._describe_name)}, () => {{",
        ]

    def scenario_open(self, name: str) -> str:
        return f"  test({_ts(name)}, async ({{ page }}) => {{"

    def launch_env_line(self, key: str, value: str) -> str:
        return f"await page.addInitScript(() => localStorage.setItem({_ts(key)}, {_ts(value)}));"

    def launch_line(self) -> str:
        return "await page.goto(BASE_URL);"

    def step_lines(self, step: Step) -> list[str]:
        return _emit_step(step)

    def assertion_lines(self, assertion: Assertion) -> list[str]:
        return _emit_assertion(assertion)

    def scenario_close(self) -> str:
        return "  });"

    def file_footer(self) -> list[str]:
        return ["});"]


def to_playwright(
    scenarios: list[Scenario],
    describe_name: str,
    base_url: str,
    app_launch_env: dict[str, str] | None = None,
) -> str:
    """Render scenarios as one `test.describe` block with a `test(...)` per scenario."""
    return render_test_file(scenarios, app_launch_env, _PlaywrightGen(describe_name, base_url))
