"""DOM → Element normalization shared by the Playwright backend and the WebView bridge.

Walks a flat list of DOM-node records (the shape QUERY_JS produces) into the normalized
``Element`` the rest of the core consumes. Pure and unit-tested — no browser, no Simulator.
"""

from __future__ import annotations

from typing import Any

from bajutsu.common.drivers import base

# One pass over the document, in document order, so an `index` selector counts positions the same
# way whichever kind of node each candidate is. Two kinds of node come back:
#
# - a control or tagged node (`sel`): `data-testid` is its identifier, its role or tag its trait;
# - a text node's own element (`<p>`, `<span>`, a heading) that `sel` does not match: flagged `text`
#   and normalized to the `staticText` trait iOS reports for a label, so a scenario can find and
#   assert on plain copy. Text inside an interactive control is skipped, since the control's own
#   label already carries it, as is text that only repeats its nearest tagged ancestor's label —
#   either would turn a selector that resolved to one element into an ambiguous one.
QUERY_JS = """
() => {
  // A link, image, or <time> keeps its meaningful value in an attribute, not the `value`
  // property. A custom ARIA range widget (role=slider/progressbar/…) reports through aria-value*,
  // which wins even when its host element has a `value` of its own (`<button>`'s "", `<li>`'s 0).
  // A native control (form field, determinate <progress>, <meter>) keeps its live `value`: its
  // aria-valuenow can lag what was typed or set. An indeterminate <progress> publishes its ARIA
  // value or nothing — never the 0 its `value` property reports, which would read as a
  // determinate 0%. An empty aria-valuetext falls through to aria-valuenow. (<area> needs no arm:
  // the UA stylesheet hides it, so the walk below never collects one.)
  const valueOf = (el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return el.getAttribute('href');
    if (tag === 'img') return el.getAttribute('src');
    if (tag === 'time') return el.getAttribute('datetime');
    const aria = el.getAttribute('aria-valuetext') || el.getAttribute('aria-valuenow');
    if (tag === 'progress' && !el.hasAttribute('value')) return aria;
    const native = tag === 'input' || tag === 'select' || tag === 'textarea'
                   || tag === 'progress' || tag === 'meter';
    if (aria !== null && !native && el.hasAttribute('role')) return aria;
    if ('value' in el) return el.value;
    return aria;
  };
  const out = [];
  const sel = '[data-testid], button, a, input, select, textarea, [role]';
  const controls = 'button, a, input, select, textarea, [role=button], [role=link], '
    + '[role=tab], [role=menuitem], [role=menuitemcheckbox], [role=menuitemradio], [role=option], '
    + '[role=checkbox], [role=radio], [role=switch], [role=combobox], [role=listbox], '
    + '[role=slider], [role=spinbutton], [role=textbox], [role=searchbox]';
  // A replaced element's children are fallback content no user sees while the element renders.
  const skip = 'head, script, style, noscript, template, select, datalist, '
    + 'video, audio, canvas, object, iframe';
  const labels = new Map();
  const ownsText = (el) => {
    for (const n of el.childNodes) {
      if (n.nodeType === Node.TEXT_NODE && n.data.trim()) return true;
    }
    return false;
  };
  for (const el of document.querySelectorAll('*')) {
    const tagged = el.matches(sel);
    if (!tagged && (!ownsText(el) || el.closest(skip))) continue;
    // A stray text node directly under <body> would otherwise make the whole page one label.
    if (!tagged && (el === document.body || el === document.documentElement)) continue;
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    if (r.width === 0 && r.height === 0) continue;
    const text = (el.innerText || el.textContent || '').trim();
    const label = el.getAttribute('aria-label') || (text ? text.slice(0, 200) : null);
    if (!tagged) {
      const parent = el.parentElement;
      if (parent && parent.closest(controls)) continue;
      const host = parent && parent.closest(sel);
      if (host && labels.get(host) === label) continue;
      out.push({
        identifier: null, role: null, label: label, value: null, text: true,
        secure: false, disabled: false, selected: false,
        frame: [r.x, r.y, r.width, r.height],
      });
      continue;
    }
    labels.set(el, label);
    out.push({
      identifier: el.getAttribute('data-testid'),
      role: el.getAttribute('role') || el.tagName.toLowerCase(),
      label: label,
      value: valueOf(el),
      secure: el.type === 'password',
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      selected: el.getAttribute('aria-selected') === 'true'
                || el.getAttribute('aria-checked') === 'true'
                || el.checked === true,
      frame: [r.x, r.y, r.width, r.height],
    });
  }
  return out;
}
"""

# `QUERY_JS` with each record also carrying `getAttribute(attr)` (BE-0466's `read_attribute`), `null`
# for a missing attribute. Derived rather than written out, so the walk — and with it what
# `resolve_unique` sees — cannot drift from the query's; `QUERY_JS` itself stays the string the
# BajutsuKit WebView bridge mirrors (`tests/test_dom_query_js_sync.py`), which reads no attribute.
_FRAME_FIELD = "frame: [r.x, r.y, r.width, r.height],"
assert QUERY_JS.count(_FRAME_FIELD) == 2 and QUERY_JS.startswith("\n() => {")
ATTRIBUTE_QUERY_JS = QUERY_JS.replace("() => {", "(attr) => {", 1).replace(
    _FRAME_FIELD, f"{_FRAME_FIELD} attribute: el.getAttribute(attr),"
)

# The trait a plain text element carries — the same name iOS reports for a static label.
STATIC_TEXT = "staticText"

# Map HTML tags and ARIA roles to the platform-neutral trait names that doctor's
# ACTIONABLE_TRAITS recognises.  Unmapped roles pass through as-is (so a developer-set
# ARIA role like "slider" still lands in traits even without an explicit entry here).
_ROLE_MAP: dict[str, str] = {
    # links
    "a": base.Trait.LINK,
    "link": base.Trait.LINK,
    # buttons / tappable openers
    "button": base.Trait.BUTTON,
    "select": base.Trait.BUTTON,
    "combobox": base.Trait.BUTTON,
    "listbox": base.Trait.BUTTON,
    # text inputs
    "input": "textField",
    "textbox": "textField",
    "spinbutton": "textField",
    "searchbox": "searchField",
    "textarea": "textView",
    # toggles
    "checkbox": "switch",
    "radio": "switch",
    "switch": "switch",
    # selectable items
    "option": "cell",
    "menuitem": "cell",
    "menuitemcheckbox": "cell",
    "menuitemradio": "cell",
    # slider and tab pass through as-is (they already match ACTIONABLE_TRAITS)
}


def _str_or_none(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


def _norm_role(role: str | None) -> str | None:
    if not role:
        return None
    return _ROLE_MAP.get(role, role)


def _to_element(rec: dict[str, Any]) -> base.Element:
    traits: list[str] = []
    role = _norm_role(_str_or_none(rec.get("role")))
    if role:
        traits.append(role)
    if rec.get("text"):
        traits.append(STATIC_TEXT)
    # `input[type=password]` carries no ARIA role, so it reaches `_ROLE_MAP` as a bare `input` and
    # normalizes to `textField` indistinguishably from a plain one. The trait is added alongside
    # that role rather than replacing it, so the field stays a text input to selectors and the crawl.
    if rec.get("secure"):
        traits.append(base.Trait.SECURE_TEXT_FIELD)
    if rec.get("disabled"):
        traits.append(base.Trait.NOT_ENABLED)
    if rec.get("selected"):
        traits.append(base.Trait.SELECTED)
    f = rec.get("frame") or [0, 0, 0, 0]
    return {
        "identifier": _str_or_none(rec.get("identifier")),
        "label": _str_or_none(rec.get("label")),
        "value": _str_or_none(rec.get("value")),
        "traits": traits,
        "frame": (float(f[0]), float(f[1]), float(f[2]), float(f[3])),
        "nativeZ": None,
    }


def parse_dom(records: list[dict[str, Any]]) -> list[base.Element]:
    """Map the QUERY_JS records to normalized Elements (the browser-free, unit-tested core)."""
    return [_to_element(r) for r in records if isinstance(r, dict)]


def parse_dom_with_attribute(
    records: list[dict[str, Any]],
) -> list[tuple[base.Element, str | None]]:
    """`parse_dom`, each Element paired with the attribute QUERY_JS read for it (BE-0466).

    Unlike `value`, an empty string is kept: an attribute present with no value reads as `""`, which
    an `attribute` assertion tells apart from a missing one (`None`).
    """
    out: list[tuple[base.Element, str | None]] = []
    for r in records:
        if isinstance(r, dict):
            v = r.get("attribute")
            out.append((_to_element(r), None if v is None else str(v)))
    return out
