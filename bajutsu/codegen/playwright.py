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

A construct with no faithful Playwright form emits a `// TODO` line rather than failing, so the
output is always reviewable; a runtime-only construct the shared walk cannot translate at all
(`if` / `forEach` / `extract`) fails loudly with a `CodegenError` instead of a silent no-op stub
(BE-0297).
"""

from __future__ import annotations

import re

from bajutsu.assertions import request_label
from bajutsu.codegen.common import (
    interrupts_setup_lines,
    manual_todo,
    ms,
    permissions_setup_lines,
    render_test_file,
)
from bajutsu.drivers import base
from bajutsu.scenario import Assertion, Gone, RequestMatch, Scenario, Step
from bajutsu.scenario.models.assertions import CountMatch, TextMatch, Wait, WaitRequest

# Wheel-scroll distance (px) a directional swipe emits — intrinsic to the gesture (BE-0227). The
# delta is the physical scroll direction: an `up` swipe pushes the surface up, so the page scrolls
# *down* (positive delta_y), mirroring `page.mouse.wheel` and how the driver realizes the same step.
_SWIPE_PX = 100
_WHEEL_DELTA = {
    "up": (0, _SWIPE_PX),
    "down": (0, -_SWIPE_PX),
    "left": (_SWIPE_PX, 0),
    "right": (-_SWIPE_PX, 0),
}
# Element-center drag offset (px) a `drag` emits (BE-0227). Unlike the wheel above, this is the
# travel direction itself — the pointer moves the way the drag points — so `right` drags right.
_DRAG_DELTA = {
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


# Playwright observes network natively, but the runner evaluates a request matcher over the
# exchanges collected *so far* (its collector records each finished exchange), not over future
# traffic. `page.waitForResponse` only sees the future, so it would miss requests made during the
# preceding steps and could hang where the runtime passes. To match the runtime, the generated test
# installs an exchange recorder up front (see `_RECORDER_SETUP`) and the assertions read that list:
# `request` is a point-in-time `filter`/`some`, `requestSequence` a forward scan, and `until:{request}`
# an `expect.poll` (an already-observed exchange passes at once; otherwise it waits). `bodyMatches`
# is a regex over the *request* body (`match_request` tests `ex.request_body`, failing when it is
# None), so the predicate reads `e.body` and guards `!== null` first.

# Installed before navigation so it captures the whole flow. Captured on 'requestfinished' — the same
# event the runtime web collector uses (`bajutsu/web_network.py`), so `status` is null when a request
# has no response, matching the collector's finished-exchange list (an earlier 'response' hook could
# not represent that case). The explicit `+` joins keep each list item one string (no implicit
# adjacent-literal concatenation, which reads as a missing comma).
_RECORDER_SETUP = [
    "// Record finished exchanges so the request assertions below read the traffic observed so far",
    "// (like the runner's collector), not only future traffic.",
    "const exchanges: { method: string; url: string; status: number | null; "
    + "body: string | null }[] = [];",
    "page.on('requestfinished', async req => {",
    "  const res = await req.response();",
    "  exchanges.push({ method: req.method(), url: req.url(), "
    + "status: res ? res.status() : null, body: req.postData() });",
    "});",
]


def _re_test_js(pattern: str, subject: str) -> str:
    """A JS `regexp.test(subject)` for a `re.search`-style matcher (substring or regex)."""
    return f"{_re_raw(pattern)}.test({subject})"


def _request_predicate(req: RequestMatch) -> str:
    """The JS boolean predicate for a request matcher, over a recorded exchange `e`.

    `e` carries `method` / `url` / `status` / `body`. Only set matcher fields are emitted, AND-ed,
    mirroring `match_request`. `body_matches` guards `e.body !== null` first: the runner fails a body
    matcher when the request has no body, so without the guard a pattern like `.*` would match a
    body-less request and pass incorrectly.
    """
    pathname = "new URL(e.url).pathname"  # `path` is path-only in the runner, so drop the query
    clauses: list[str] = []
    if req.method is not None:
        clauses.append(f"e.method === {_ts(req.method.upper())}")
    if req.url is not None:
        clauses.append(f"e.url === {_ts(req.url)}")
    if req.url_matches is not None:
        clauses.append(_re_test_js(req.url_matches, "e.url"))
    if req.path is not None:
        clauses.append(f"{pathname} === {_ts(req.path)}")
    if req.path_matches is not None:
        clauses.append(_re_test_js(req.path_matches, pathname))
    if req.status is not None:
        clauses.append(f"e.status === {req.status}")
    if req.body_matches is not None:
        clauses.append(f"e.body !== null && {_re_test_js(req.body_matches, 'e.body')}")
    return " && ".join(clauses) if clauses else "true"


def _emit_request_assertion(req: RequestMatch) -> list[str]:
    """A `request` assertion as a check over the recorded exchanges.

    `count` (when set) is exact, mirroring the runtime `request` check; otherwise at least one match
    is required. Point-in-time over what was observed so far — never a `waitForResponse`.
    """
    pred = _request_predicate(req)
    label = f"// request {request_label(req)}"
    if req.count is not None:
        return [label, f"expect(exchanges.filter(e => {pred}).length).toBe({req.count});"]
    return [label, f"expect(exchanges.some(e => {pred})).toBeTruthy();"]


def _emit_until_request(req: RequestMatch, timeout_ms: int) -> list[str]:
    """An `until: { request }` wait: poll the growing recorder to the step's timeout.

    `count` is a lower bound for an `until` wait (the runner keeps polling until it is reached); an
    already-observed exchange satisfies `expect.poll` immediately, matching the runtime which checks
    the collected exchanges before continuing to poll.
    """
    pred = _request_predicate(req)
    need = req.count if req.count is not None else 1
    return [
        f"// wait until request {request_label(req)}",
        f"await expect.poll(() => exchanges.filter(e => {pred}).length, "
        f"{{ timeout: {timeout_ms} }}).toBeGreaterThanOrEqual({need});",
    ]


def _emit_request_sequence(seq: list[RequestMatch]) -> list[str]:
    """A `requestSequence` as an in-order forward scan over the recorded exchanges.

    Mirrors `_eval_request_sequence`: advance through the matchers as exchanges are seen in order and
    require every matcher to be reached. Order is the check, so `count` is left off the labels.
    """
    header = "// requestSequence " + " → ".join(request_label(m, with_count=False) for m in seq)
    return [
        header,
        "{",
        "  const seq = [",
        *[f"    (e) => {_request_predicate(m)}," for m in seq],
        "  ];",
        "  let i = 0;",
        "  for (const e of exchanges) if (i < seq.length && seq[i](e)) i++;",
        "  expect(i).toBe(seq.length);",
        "}",
    ]


def _scenario_uses_network(scenario: Scenario) -> bool:
    """Whether any step or `expect` reads the network (request / requestSequence / until request)."""

    def asserts_network(assertions: list[Assertion]) -> bool:
        return any(a.request is not None or a.request_sequence is not None for a in assertions)

    for step in scenario.steps:
        if step.wait is not None and isinstance(step.wait.until, WaitRequest):
            return True
        if step.assert_ is not None and asserts_network(step.assert_):
            return True
    return asserts_network(scenario.expect)


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
    # A generated test targets one platform, so an `id` / `idMatches` list of cross-platform OR
    # candidates (BE-0221) emits its primary (first) form — the id the web build actually surfaces.
    if "id" in sel:
        return f"page.getByTestId({_ts(base.id_candidates(sel['id'])[0])})"
    traits = sel.get("traits")
    if "label" in sel:
        if traits:
            return f"page.getByRole({_ts(traits[0])}, {{ name: {_ts(sel['label'])}, exact: true }})"
        return f"page.getByText({_ts(sel['label'])}, {{ exact: true }})"
    if traits:
        return f"page.getByRole({_ts(traits[0])})"
    if "idMatches" in sel:
        css = _glob_to_css(base.id_candidates(sel["idMatches"])[0])
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


def _emit_swipe_direction(loc: str, direction: str) -> list[str]:
    # A directional swipe scrolls, so wheel over the element — matching the web driver (BE-0227). The
    # old mouse drag left the page unscrolled.
    dx, dy = _WHEEL_DELTA[direction]
    # A block scope keeps `const box` re-declarable across multiple swipes in one test.
    return [
        "{",
        f"  const box = await {loc}.boundingBox();",
        "  if (box) {",
        "    const cx = box.x + box.width / 2;",
        "    const cy = box.y + box.height / 2;",
        "    await page.mouse.move(cx, cy);",
        f"    await page.mouse.wheel({dx}, {dy});",
        "  }",
        "}",
    ]


def _emit_drag_direction(loc: str, direction: str) -> list[str]:
    # A `drag` is a real pointer drag of the element (BE-0227) — matching the web driver, which drags
    # (move → down → move → up) for `drag` where it wheels for a directional `swipe`.
    dx, dy = _DRAG_DELTA[direction]
    # A block scope keeps `const box` re-declarable across multiple drags in one test.
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
    if step.back is not None:
        # The web's `back` is browser history — the same primitive the driver's `back()` uses
        # (`page.go_back()`), so codegen emits it faithfully rather than an unlabeled TODO (BE-0210).
        return ["await page.goBack();"]
    if step.long_press is not None:
        return _act(
            step.long_press.sel.as_selector(),
            f"click({{ delay: {ms(step.long_press.duration)} }})",
        )
    if step.type is not None:
        if step.type.into is not None:
            return _act(step.type.into.as_selector(), f"fill({_ts(step.type.text)})")
        return [f"await page.keyboard.type({_ts(step.type.text)});"]
    if step.clear is not None:
        # Playwright's Locator.clear() focuses and empties the field — the faithful peer of the
        # driver's focus-then-backspace clear (BE-0265).
        return _act(step.clear.into.as_selector(), "clear()")
    if step.delete is not None:
        sel = step.delete.into.as_selector()
        loc = _locator(sel)
        if loc is None:
            return [_unsupported_selector_todo(sel)]
        # Focus, then backspace `count` times from the end — no repeat-count on a single press.
        return [f"await {loc}.focus();"] + [
            "await page.keyboard.press('Backspace');" for _ in range(step.delete.count)
        ]
    if step.select is not None:
        # Locator.selectText() selects the whole content — the web peer of select-all (BE-0265).
        return _act(step.select.into.as_selector(), "selectText()")
    if step.copy_ is not None:
        return ["await page.keyboard.press('Control+c');"]
    if step.swipe is not None:
        sw = step.swipe
        if sw.on is not None and sw.direction is not None:
            sel = sw.on.as_selector()
            loc = _locator(sel)
            if loc is None:
                return [_unsupported_selector_todo(sel)]
            return _emit_swipe_direction(loc, sw.direction)
        return ["// TODO: coordinate swipe (from/to) is not generated"]
    if step.drag is not None:
        sel = step.drag.on.as_selector()
        loc = _locator(sel)
        if loc is None:
            return [_unsupported_selector_todo(sel)]
        return _emit_drag_direction(loc, step.drag.direction)
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
    if step.manual is not None:
        # A human takeover (BE-0185): an operation only a human can perform, rendered as a labeled
        # TODO rather than a silent skip — the same honest boundary the device-control TODOs keep.
        return [f"// TODO: manual step — {manual_todo(step.manual.label, step.manual.bypass)}"]
    return ["// TODO: unsupported step"]


def _emit_wait(w: Wait) -> list[str]:
    timeout = ms(w.timeout)
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
        # Poll the recorded exchanges to the step's timeout, so an already-observed request passes at
        # once and a future one is awaited — matching the runtime (BE-0085).
        return _emit_until_request(w.until.request, timeout)
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
        return _emit_request_assertion(a.request)
    if a.request_sequence is not None:
        return _emit_request_sequence(a.request_sequence)
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

    def setup_lines(self, scenario: Scenario) -> list[str]:
        # Install the network-exchange recorder before navigation, but only when the scenario asserts
        # over the network — otherwise the scaffold stays free of unused plumbing. `permissions`
        # (BE-0276) has no browser equivalent (no TCC/pm-style OS permission model), so it is always
        # a TODO when present, regardless of the target this scenario also runs on.
        lines = list(_RECORDER_SETUP) if _scenario_uses_network(scenario) else []
        return lines + permissions_setup_lines(scenario) + interrupts_setup_lines(scenario)

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
