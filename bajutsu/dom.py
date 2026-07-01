"""DOM → Element normalization shared by the Playwright backend and the WebView bridge.

Walks a flat list of DOM-node records (the shape QUERY_JS produces) into the normalized
``Element`` the rest of the core consumes. Pure and unit-tested — no browser, no Simulator.
"""

from __future__ import annotations

from typing import Any

from bajutsu.drivers import base

QUERY_JS = """
() => {
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
      value: ('value' in el) ? el.value : null,
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      selected: el.getAttribute('aria-selected') === 'true'
                || el.getAttribute('aria-checked') === 'true',
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
    }


def parse_dom(records: list[dict[str, Any]]) -> list[base.Element]:
    """Map the QUERY_JS records to normalized Elements (the browser-free, unit-tested core)."""
    return [_to_element(r) for r in records if isinstance(r, dict)]
