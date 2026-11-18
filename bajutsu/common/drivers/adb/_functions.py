"""Normalize a UI Automator dump into elements, and drive the device-side actuation channel."""

from __future__ import annotations

import re
from collections.abc import Mapping
from xml.etree import ElementTree as ET

from bajutsu.common.drivers import base

from ._shared import NodeIdentity, logger

# uiautomator's bounds attribute, e.g. "[0,100][200,220]".
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


# `wm size` prints "Physical size: WxH" and, when the display has been resized, an "Override size:
# WxH" that is the effective resolution — the override wins when present (BE-0326).
_WM_SIZE = re.compile(r"^\s*(Physical|Override)\s+size:\s*(\d+)x(\d+)\s*$", re.MULTILINE)


def _strip_pkg(resource_id: str) -> str | None:
    """The local id from a UI Automator resource-id: `com.app:id/foo` → `foo`.

    Native `android:id`s carry the `<package>:id/` prefix; a Compose `testTag` surfaced via
    `testTagsAsResourceId` has none, so it passes through verbatim (`stable.refresh`). Matching is
    exact on the local name — no `.`↔`_` normalization, which could conflate distinct ids and break
    determinism (the Views `stable.refresh`→`stable_refresh` case is left to a scenario variant).
    """
    # `or None` maps both an absent resource-id and a malformed one with no local name
    # (`com.app:id/`) to None, so the identifier is never an empty string that no selector matches.
    return resource_id.rsplit("/", 1)[-1] or None


# The platform wheel `setPickerValue` drives on Android (BE-0480). `NumberPicker`'s own accessibility
# provider reports this class for the wheel, which is also how the wheels of a spinner-mode
# `DatePicker` / `TimePicker` dump.
_NUMBER_PICKER_CLASS = "android.widget.NumberPicker"


def _norm_class(class_name: str) -> str:
    """Widget class to a trait token: `android.widget.Button` → `button`."""
    simple = class_name.rsplit(".", 1)[-1]
    return simple[:1].lower() + simple[1:] if simple else simple


def _bounds(raw: str, malformed_count: list[int] | None = None) -> base.Frame:
    """The `(x, y, w, h)` frame from a node's `bounds` attribute, or the origin frame if absent/malformed.

    The origin-frame default is silent by design where it is expected — a genuinely bounds-less node
    is not a fault — but a *malformed* attribute (present, non-empty, yet unparseable) tallies into
    `malformed_count` when given, so the caller can warn once per parse rather than once per node: a
    single dump with many such nodes would otherwise flood the log with one line per node, each
    occurrence individually indistinguishable from the fine, silent default. A node whose bounds stay
    malformed across a `_settle` poll's repeated reads still warns once per read (up to ~80 reads per
    call) — this collapses the per-node flood within one read, not the per-read flood across a poll.
    """
    m = _BOUNDS.search(raw or "")
    if not m:
        if raw and malformed_count is not None:
            malformed_count[0] += 1
        return (0.0, 0.0, 0.0, 0.0)
    x1, y1, x2, y2 = (float(v) for v in m.groups())
    return (x1, y1, x2 - x1, y2 - y1)


def _parse_wm_size(out: str) -> base.Point:
    """Parse `adb shell wm size` output to the display `(w, h)` in pixels.

    Prefers an Override size over the Physical size; fails loudly (determinism first) rather than
    guessing a viewport if the output carries neither.
    """
    physical: base.Point | None = None
    override: base.Point | None = None
    for label, w, h in _WM_SIZE.findall(out or ""):
        if label == "Override":
            override = (float(w), float(h))
        else:
            physical = (float(w), float(h))
    size = override or physical
    if size is None:
        raise ValueError(f"could not parse `wm size` output: {out!r}")
    return size


def _derived_label(node: ET.Element) -> str | None:
    """The accessible name of a labelless control, joined from its descendants' visible text.

    A Compose `NavigationBarItem` (and any icon-plus-caption control) dumps as a clickable node
    with no own `text`/`content-desc`; its visible caption lives in a child `TextView`. Mirroring
    how an accessibility service names a focusable container, the control's label is its
    descendants' text in document order — so a tab is addressable by its caption ("Log"), the same
    way the XCUITest backend exposes each tab as a label-bearing button (BE-0107).

    A nested clickable descendant is its own control (it independently gains the `button` trait and
    derives its own label), so its subtree is skipped rather than folded into this label — which
    also keeps two nested clickables from both deriving the same joined text (BE-0223).

    Only `text` is folded in, not `content-desc`: `content-desc` is this driver's *value* channel
    (SPEC §2.1 mirrors assertion state into it), so pulling it into the label would risk a mirrored
    value bleeding into the name. This is a deliberate limit — an icon-only caption carried solely
    in `content-desc` (no `TextView`) is not a showcase pattern, and would need the value/label
    split reconciled first.
    """
    parts: list[str] = []

    def collect(parent: ET.Element) -> None:
        for child in parent:
            if child.get("clickable") == "true":
                continue  # a separate control; its text belongs to its own element
            if text := child.get("text"):
                parts.append(text)
            collect(child)

    collect(node)
    return " ".join(parts) or None


def _traits(node: ET.Element) -> list[str]:
    out: list[str] = []
    cls = node.get("class") or ""
    if cls:
        out.append(_norm_class(cls))
    # A clickable node is tappable, so it carries the button trait — the shared cross-backend tab
    # selector `{ label, traits: [button] }` (BE-0107) resolves on adb because a Compose
    # NavigationBarItem dumps as a clickable `android.view.View`, whose class alone ("view") never
    # yields it (BE-0223). Note this `button` means "tappable", broader than a `button` derived from
    # the widget type — so a bare `traits: [button]` matches any tappable node; pair it with a label.
    # Guarded so a widget already mapped to `button` by class (a Views Button) is not tagged twice.
    # The class-derived `numberPicker` stays for existing selectors; the shared token is what lets the
    # iOS wheel selector `{ within, traits: [pickerWheel], index }` resolve here too (BE-0480).
    if cls == _NUMBER_PICKER_CLASS:
        out.append(base.Trait.PICKER_WHEEL)
    if node.get("clickable") == "true" and base.Trait.BUTTON not in out:
        out.append(base.Trait.BUTTON)
    # UI Automator dumps a masked input as `password="true"`, whatever widget class backs it, so the
    # normalized trait comes from the flag rather than from `class` (BE-0331).
    if node.get("password") == "true":
        out.append(base.Trait.SECURE_TEXT_FIELD)
    if node.get("enabled") == "false":
        out.append(base.Trait.NOT_ENABLED)
    # A UI Automator checkbox/switch reports its state as `checked`; a list selection as `selected`.
    if node.get("selected") == "true" or node.get("checked") == "true":
        out.append(base.Trait.SELECTED)
    return out


#: The accessibility classes an editable text node reports (#2388). A Views `EditText` and the
#: subclasses that inherit its `getAccessibilityClassName` (AppCompat's `AppCompatEditText`, Material's
#: `TextInputEditText`) report `EditText`, and so does a Compose text field; `AutoCompleteTextView`
#: and `MultiAutoCompleteTextView` (a `SearchView`'s inner field, a Material exposed dropdown) override
#: the method with their own names.
_EDITABLE_CLASSES = frozenset(
    {
        "android.widget.EditText",
        "android.widget.AutoCompleteTextView",
        "android.widget.MultiAutoCompleteTextView",
    }
)


def _editable_element_fields(node: ET.Element) -> tuple[str | None, str | None]:
    """The `(label, value)` of an editable text node, so its typed text reads as `value` (#2388).

    `label` stays an accessible name (`content-desc`, else the hint) rather than the typed text, so an
    `into: { label: … }` selector keeps its meaning as the user types — the split iOS already has. An
    empty field reports its hint as `text`, told apart only when the dump carries `hint` (the resident
    server's does); on the `uiautomator dump` fallback, which may omit it, an empty field's hint reads
    as its value, and `text` stays the label there — the only name that path has for the field, so a
    label selector resolves on both read paths. The dump has no `isShowingHintText`, so typed text
    equal to the hint reads as no value. A masked field's text is withheld from both, whatever the
    dump carries in it.
    """
    desc = node.get("content-desc") or ""
    text = node.get("text") or ""
    hint = node.get("hint") or ""
    showing_hint = bool(hint) and text == hint
    masked = node.get("password") == "true"
    value = None if masked or showing_hint else text
    label = desc or hint or ("" if masked or "hint" in node.attrib else text)
    return label or None, value or None


def _to_element(node: ET.Element, malformed_bounds: list[int] | None = None) -> base.Element:
    if node.get("class") in _EDITABLE_CLASSES:
        return _element(node, *_editable_element_fields(node), malformed_bounds)
    desc = node.get("content-desc") or ""
    text = node.get("text") or ""
    # `text` is the visible label; `content-desc` is where the showcase mirrors the assertion value
    # (SPEC §2.1). `label` falls back to `content-desc` for an element that carries only a content
    # description (an icon-only control). A clickable control with neither derives its label from
    # its descendants' text (BE-0223); derivation is scoped to clickable nodes so non-interactive
    # layout containers stay label-less rather than flooding the tree with synthetic labels.
    label: str | None = text or desc
    if not label and node.get("clickable") == "true":
        label = _derived_label(node)
    return _element(node, label or None, desc or None, malformed_bounds)


def _element(
    node: ET.Element, label: str | None, value: str | None, malformed_bounds: list[int] | None
) -> base.Element:
    """Assemble one `Element` from a node and its already-chosen label and value."""
    return {
        "identifier": _strip_pkg(node.get("resource-id") or ""),
        "label": label,
        "value": value,
        "traits": _traits(node),
        "frame": _bounds(node.get("bounds") or "", malformed_bounds),
        # `dumpWindowHierarchy`'s XML has no z attribute, so a measured position arrives beside the
        # body and is matched in by `_elements_from_nodes` (BE-0355). Absent until then.
        "nativeZ": None,
    }


def _warn_malformed_bounds(count: int) -> None:
    """Log once per parse for however many nodes carried a malformed `bounds` attribute (never zero)."""
    logger.warning(
        "%d node(s) had a bounds attribute that did not match the expected format; "
        "their frames defaulted to (0,0,0,0)",
        count,
    )


def _identity(node: ET.Element) -> NodeIdentity:
    """The accessibility fields that name a node to the device, verbatim from the dump.

    Verbatim — not `_to_element`'s derived `identifier` / `label` — because the resident server matches
    these against its own dump's raw attributes. Deriving on one side and matching on the other is the
    kind of drift that turns a resolvable element into a permanent `stale`.
    """
    return (
        node.get("resource-id") or "",
        node.get("content-desc") or "",
        node.get("text") or "",
        node.get("class") or "",
    )


def _native_z_key(node: ET.Element, occurrence: int) -> str:
    """What names a node to the device's own second walk, recomputed from the dumped `<node>`.

    Bounds, class, and package, plus how many nodes agreeing on all three came before it. The device
    cannot key its readings by document-order position — it measures them in a walk over the active
    window while the body spans every window — and cannot key them by identity either, since the
    four accessibility fields `_identity` uses are deliberately not unique. Both sides walk the same
    accessibility tree depth-first, so the occurrence count agrees, *scoped to the active window*:
    `narrowed_root` drops SystemUI's own windows from the tree before this key is computed,
    but not a second window of the app under test itself (a dialog over its own main window). Two
    opted-in nodes sharing bounds, class, and package across the app's own windows would shift each
    other's occurrence count and could match onto the wrong one — narrower than the false-authority
    failure mode `nativeZ` exists to avoid overall (BE-0355), since it needs bounds- and
    class-identical nodes across the same app's own windows, but not yet closed. Kept in sync with
    `ResidentServerTest.kt`'s `nativeZHeader`.
    """
    # The verbatim `[l,t][r,b]` corners, not `_bounds`' (x, y, width, height) `Frame`: the device
    # keys by the screen rect it read, so deriving anything here would only be a second chance to
    # disagree.
    match = _BOUNDS.match(node.get("bounds") or "")
    corners = ",".join(match.groups()) if match else ""
    return f"{corners}|{node.get('class') or ''}|{node.get('package') or ''}|{occurrence}"


def _elements_from_nodes(
    nodes: list[ET.Element], native_z: Mapping[str, float] | None = None
) -> list[base.Element]:
    """`_to_element` over every node, warning once for the parse if any `bounds` was malformed.

    The one place both `parse_hierarchy` and `elements_with_identities` build their `Element`
    list, so the malformed-bounds tally and its warning are counted and logged once, not duplicated at
    each call site — and the one place a device-measured `nativeZ` is matched onto the node it belongs
    to (BE-0355).
    """
    malformed_bounds = [0]
    els = [_to_element(n, malformed_bounds) for n in nodes]
    if malformed_bounds[0]:
        _warn_malformed_bounds(malformed_bounds[0])
    if native_z:
        seen: dict[str, int] = {}
        for node, el in zip(nodes, els, strict=True):
            stem = _native_z_key(node, 0).rsplit("|", 1)[0]
            occurrence = seen.get(stem, 0)
            seen[stem] = occurrence + 1
            el["nativeZ"] = native_z.get(f"{stem}|{occurrence}")
    return els


def elements_with_identities(
    root: ET.Element | None, native_z: Mapping[str, float] | None = None
) -> tuple[list[base.Element], list[NodeIdentity]]:
    """Every `<node>` under `root` as an Element, plus its device-addressable identity, index-aligned.

    Both lists walk the same `<node>` sequence in document order, so element *i* is named by identity
    *i*. Produced together rather than by two passes so the alignment cannot drift.

    Takes an already-parsed tree rather than text because the resident channel has one by the time it
    calls here — it had to parse to strip the SystemUI decor windows (BE-0407 unit 23).
    """
    if root is None:
        return [], []
    nodes = list(root.iter("node"))
    return _elements_from_nodes(nodes, native_z), [_identity(n) for n in nodes]


def slice_hierarchy_root(text: str) -> ET.Element | None:
    """Slice the `<hierarchy>` XML out of a UI Automator dump and parse its root, or `None`.

    UI Automator output — over the adb subprocess or the resident channel — can be wrapped in a
    status line ("UI hierarchy dumped to: …") or replaced by "null root node returned by
    UiTestAutomationBridge" mid-transition. The XML is located by its `<hierarchy>` tags so the
    surrounding chatter is ignored; a missing or unparseable tree yields `None`, letting each caller
    apply its own degrade (`parse_hierarchy` an empty list, the resident path the original text).
    """
    start = text.find("<hierarchy")
    end = text.rfind("</hierarchy>")
    if start == -1 or end == -1:
        return None
    try:
        # The dump is UI Automator's own output over our channel — a DTD/entity-free tree of
        # attribute-only <node>s — not attacker-supplied XML, so the stdlib parser is safe here.
        return ET.fromstring(text[start : end + len("</hierarchy>")])  # noqa: S314
    except ET.ParseError:
        return None


def parse_hierarchy(text: str) -> list[base.Element]:
    """Parse `uiautomator dump` output into Elements (empty on a null-root/garbled dump).

    `exec-out uiautomator dump /dev/tty` prints the `<hierarchy>` XML; a missing/unparseable tree
    yields `[]`, which the transient-empty retry rides over.
    """
    root = slice_hierarchy_root(text)
    if root is None:
        return []
    # Every `<node>` is an element; the `<hierarchy>` root itself is not a UI node.
    return _elements_from_nodes(list(root.iter("node")))
