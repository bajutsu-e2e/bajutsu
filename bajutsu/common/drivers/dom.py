"""DOM → Element normalization shared by the Playwright backend and the WebView bridge.

Walks a flat list of DOM-node records (the shape QUERY_JS produces) into the normalized
``Element`` the rest of the core consumes. Pure and unit-tested — no browser, no Simulator.
"""

from __future__ import annotations

from typing import Any

from bajutsu.common.drivers import base

QUERY_JS = """
() => {
  // A link, image, or <time> keeps its meaningful value in an attribute, not the `value`
  // property. A custom ARIA range widget (role=slider/progressbar/…) reports through aria-value*,
  // which wins even when its host element has a `value` of its own (`<button>`'s "", `<li>`'s 0).
  // A native control (form field, determinate <progress>, <meter>) keeps its live `value`: its
  // aria-valuenow can lag what was typed or set. An indeterminate <progress> has no value of its
  // own (`el.value` is 0), so its ARIA text wins even without an explicit role (its progressbar
  // role is implicit). An empty aria-valuetext falls through to aria-valuenow.
  const valueOf = (el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === 'a' || tag === 'area') return el.getAttribute('href');
    if (tag === 'img') return el.getAttribute('src');
    if (tag === 'time') return el.getAttribute('datetime');
    const aria = el.getAttribute('aria-valuetext') || el.getAttribute('aria-valuenow');
    const native = tag === 'input' || tag === 'select' || tag === 'textarea'
                   || (tag === 'progress' && el.hasAttribute('value')) || tag === 'meter';
    if (aria !== null && !native && (el.hasAttribute('role') || tag === 'progress')) return aria;
    if ('value' in el) return el.value;
    return aria;
  };
  const out = [];
  const sel = '[data-testid], button, a, input, select, textarea, [role]';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    if (r.width === 0 && r.height === 0) continue;
    const text = (el.innerText || el.textContent || '').trim();
    out.push({
      identifier: el.getAttribute('data-testid'),
      role: el.getAttribute('role') || el.tagName.toLowerCase(),
      label: el.getAttribute('aria-label') || (text ? text.slice(0, 200) : null),
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
