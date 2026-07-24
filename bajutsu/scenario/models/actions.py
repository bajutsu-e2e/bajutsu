"""One-shot device actions.

Gestures, text entry, relaunch, location/push, http, and the device-control steps. Each is a step
payload validated on its own; the `Step` aggregator that selects exactly one lives in `steps.py`.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from bajutsu.scenario.models._base import Point, _Model
from bajutsu.scenario.models.selector import Selector


def _check_regex(pattern: str, field: str) -> None:
    """Reject an uncompilable regex at load time, so a typo is a scenario error, not a mid-run crash."""
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"{field} is not a valid regex: {e}") from e


class LongPress(_Model):
    """`longPress` action — press and hold a selector for `duration` seconds."""

    sel: Selector
    duration: float


class Pinch(_Model):
    """Two-finger magnify. scale > 1 zooms in, 0 < scale < 1 zooms out."""

    sel: Selector
    scale: float

    @model_validator(mode="after")
    def _positive(self) -> Self:
        if self.scale <= 0:
            raise ValueError("pinch scale must be positive (>1 zooms in, <1 zooms out) (§6.2)")
        return self


class Rotate(_Model):
    """Two-finger rotation. radians > 0 rotates clockwise."""

    sel: Selector
    radians: float


# The label-based Selector fields a SpringBoard alert button can actually carry (Python field
# names); every other field (id/idMatches/traits/value/within) could never match one — the alert
# lives outside the app's accessibility tree, so it has no app-assigned identifier, trait, or value.
_SYSTEM_ALERT_SEL_FIELDS = frozenset({"label", "label_matches", "index"})


class HandleSystemAlert(_Model):
    """`handleSystemAlert` action — tap a button on an iOS SpringBoard permission prompt (BE-0316).

    A permission alert lives outside the app's accessibility tree (SpringBoard owns it), so it
    carries no app-assigned identifier, trait set, or value — only its visible text. `sel` therefore
    accepts the label-based fields alone (`label` / `labelMatches` / `index`) and rejects the rest at
    parse time. `timeout` bounds the condition wait for the prompt, required exactly as `wait`'s is.
    """

    sel: Selector
    timeout: float

    @model_validator(mode="after")
    def _label_only_selector(self) -> Self:
        disallowed = sorted(
            f for f in self.sel.model_fields_set if f not in _SYSTEM_ALERT_SEL_FIELDS
        )
        if disallowed:
            # Name each rejected field by the alias the author writes (idMatches, not id_matches),
            # read from the Selector model itself so this can't drift from the real aliases.
            aliases = ", ".join(Selector.model_fields[f].alias or f for f in disallowed)
            raise ValueError(
                "handleSystemAlert sel accepts only label / labelMatches / index — "
                f"a SpringBoard alert button carries no {aliases} (§6.2)"
            )
        return self


class TapPoint(_Model):
    """`tapPoint` action — tap a screen location by normalized coordinates (0..1), not a selector.

    The bottom rung of the stability ladder (DESIGN §5), for a control the accessibility tree does
    not expose as an addressable element — most notably a tab-bar tab on an app with no accessibility
    ids, which iOS's accessibility tree collapses into one opaque group. `record`'s agent locates it in the screenshot
    and emits its center here; `run` replays it against the current screen size. `x`/`y` are fractions
    of the app window (top-left origin), so the tap survives a resolution change a raw-pixel tap would
    not — but it is still coordinate-based and unverifiable by selector, so prefer a real selector
    whenever the element is addressable.
    """

    x: float
    y: float

    @model_validator(mode="after")
    def _in_unit_square(self) -> Self:
        if not (0.0 <= self.x <= 1.0 and 0.0 <= self.y <= 1.0):
            raise ValueError("tapPoint x/y are normalized fractions and must be within 0..1 (§6.2)")
        return self


class TypeText(_Model):
    """`type` action — enter text, optionally into a selector and optionally submitting after."""

    text: str
    into: Selector | None = None
    submit: bool = False


class SelectOption(_Model):
    """`selectOption` action — set a native `<select>` to the option with the given value.

    Web-only: a `<select>` lives in the DOM but has no native counterpart on iOS / Android, so those
    backends refuse it (UnsupportedAction). `option` matches an option's *value* (not its visible
    label), mirroring the `value` assertion — which reads the `<select>`'s current value — so a
    picked option is directly assertable. A `<select>`'s dropdown is not in the DOM, so this is how a
    web `<select>` (e.g. the BE-0191 theme picker) is switched deterministically rather than by a
    coordinate click.
    """

    sel: Selector
    option: str


class SelectText(_Model):
    """`select` action — select a field's content before a consuming action (copy / type-over).

    `mode: all` (the only mode for now) selects the whole current content via the platform's
    select-all. Kept an action, not queryable state: no backend exposes a selection range through
    its element surface, so its outcome is only ever verified through what follows it — a `copy`
    (clipboard read-back) or a `type` that replaces the selection (`value` assertion) — never
    asserted directly (BE-0265).
    """

    into: Selector
    mode: Literal["all"] = "all"


class Clear(_Model):
    """`clear` action — clear the field's entire current content (backspace-equivalent; BE-0265).

    Realized as one backspace per character of the field's reported `value`, so it stays agnostic to
    what the field held. That count comes from the accessibility `value`, which equals the character
    count for a plain text field; a field whose `value` is masked or reformatted (a secure/password
    field, a currency mask) can report a length that differs from what is deletable, so `clear` may
    under- or over-delete there. Backend actuation fidelity for such fields is build-time triage
    (per *Detailed design*); verify the outcome with a `value` assertion when it matters.
    """

    into: Selector


class Delete(_Model):
    """`delete` action — remove `count` characters from the end of the field (backspace; BE-0265)."""

    into: Selector
    count: int = Field(gt=0)


class Copy(_Model):
    """`copy` action — copy the active selection to the clipboard (BE-0265).

    Acts on whatever a prior `select` left selected; it takes no target of its own. With no active
    selection it fails the step rather than silently copying nothing, and its result is verified
    through the existing `clipboard` read-back (BE-0052), never through any state this step holds.
    """


def bypass_hint(bypass: str | None) -> str:
    """The shared phrase describing a `manual` step's bypass, or its absence (BE-0185).

    Naming what deterministic bridge to wire (`bypass`) or that none exists — the one wording every
    surface that mentions a takeover's bypass reuses (the run-time FAIL message, the codegen `// TODO`,
    and the `record` marker's TODO), so a future tweak edits one place, not three. Each caller keeps
    its own prefix/suffix around this core; only the shared clause lives here.
    """
    return (
        f"wire a deterministic bypass: {bypass}"
        if bypass
        else "no deterministic run-time equivalent"
    )


class Manual(_Model):
    """`manual` action — a human-takeover marker recorded during `record` (BE-0185).

    Emitted when a blocker is an *operation* the AI cannot perform (a CAPTCHA, a biometric prompt,
    a repeatedly unresolvable gesture): the human operated the device live and `record` recorded
    this marker of the observed transition, not the raw gesture. It has no deterministic run-time
    equivalent, so at `run` time it fails loudly with `label` — never a silent pass, never a hang
    (directives 1 and 2). `bypass`, when set, names the deterministic bridge an author can wire to
    make the step replayable (a test-build flag, a device-control / device-state primitive,
    BE-0035 / BE-0052); None means no such bridge exists (a real CAPTCHA).
    """

    label: str = Field(min_length=1)
    bypass: str | None = None


class Swipe(_Model):
    """`swipe` action — by `direction` on an element (`on`), or between two points (`from`/`to`).

    `amount` (only with `on`/`direction`) sets how far to travel as a fraction of the screen
    (0 < amount ≤ 1): ~0.2 nudges, ~0.5 scrolls half a screen, ~0.9 nearly a full one. Omitted, a
    small default distance is used — so the caller can dial the scroll to the instruction.
    """

    on: Selector | None = None
    direction: Literal["up", "down", "left", "right"] | None = None
    amount: float | None = None
    from_: Point | None = Field(default=None, alias="from")
    to: Point | None = None

    @model_validator(mode="after")
    def _form(self) -> Self:
        sel_fields = self.on is not None or self.direction is not None
        pt_fields = self.from_ is not None or self.to is not None
        if sel_fields and pt_fields:
            raise ValueError("swipe cannot mix {on,direction} with {from,to} (§6.2)")
        if self.amount is not None and not (0.0 < self.amount <= 1.0):
            raise ValueError(
                "swipe amount is a fraction of the screen and must be within 0..1 (§6.2)"
            )
        if self.on is not None and self.direction is not None:
            return self
        if self.amount is not None:
            raise ValueError("swipe amount applies only to the {on,direction} form (§6.2)")
        if self.from_ is not None and self.to is not None:
            return self
        raise ValueError("swipe requires either {on,direction} or {from,to} completely (§6.2)")


class Drag(_Model):
    """`drag` action — a real pointer drag of an element (`on`) in a `direction` (BE-0227).

    Where `swipe`'s directional form *scrolls* (revealing off-screen content), `drag` grabs the
    element and moves it — a resize divider, a slider thumb, a reorder handle, a map inside a canvas:
    any control you drag rather than scroll. `amount` sets how far to travel as a fraction of the
    screen (0 < amount ≤ 1); omitted, a small default distance is used. It matters on the web
    backend, where a directional `swipe` is a wheel scroll (which does not move a grabbed element)
    but a `drag` is a genuine pointer drag (`move → down → move → up`). On iOS / Android a real OS
    drag both scrolls and moves handles, so `swipe`'s directional form and `drag` coincide there.
    """

    on: Selector
    direction: Literal["up", "down", "left", "right"]
    amount: float | None = None

    @model_validator(mode="after")
    def _amount_range(self) -> Self:
        if self.amount is not None and not (0.0 < self.amount <= 1.0):
            raise ValueError(
                "drag amount is a fraction of the screen and must be within 0..1 (§6.2)"
            )
        return self


class Back(_Model):
    """`back` action — navigate back one level, each backend using its platform-correct primitive.

    Android has a true system back (a key event); iOS has no hardware back, so navigating back means
    tapping the OS-provided navigation back button, and the web goes back in history. The step is the
    one cross-backend expression of "go back" (BE-0210).
    """


class Relaunch(_Model):
    """`relaunch` action — restart the app process, optionally overriding its launch env/args."""

    env: dict[str, str] | None = None
    args: list[str] | None = None


class SetLocation(_Model):
    """Override the simulated device's GPS location (simctl location set)."""

    lat: float
    lon: float


class Push(_Model):
    """Deliver a simulated push notification (simctl push) to the app under test.

    Carries this APNs payload, e.g. `{"aps": {"alert": "..."}}`.
    """

    payload: dict[str, Any]


class HttpRequest(_Model):
    """Issue an HTTP request (for test-data setup, webhook triggers, API calls).

    The response status is checked against ``status`` (if given); a mismatch
    fails the step. ``saveBody`` stores the response body text as
    ``vars.<saveBody>`` for subsequent ``${vars.*}`` interpolation.
    """

    method: str = "GET"
    url: str
    headers: dict[str, str] | None = None
    body: str | None = None
    status: int | None = None
    save_body: str | None = Field(default=None, alias="saveBody")


class VarTarget(_Model):
    """`into: { var: <name> }` — the `${vars.<name>}` slot a step writes its produced value to."""

    var: str


class Totp(_Model):
    """`totp` — generate an RFC 6238 time-based one-time password into `${vars.*}` (BE-0046).

    `secret` is the shared base32 key (commonly `${secrets.*}`); the current code is written to
    `into.var` for a later `type` / `assert` to consume. Local and deterministic — no LLM, no
    network, no scripting escape hatch.
    """

    secret: str
    into: VarTarget


class EmailMatch(_Model):
    """Which message `email` waits for: recipient and/or subject, AND-ed. At least one is required."""

    to: str | None = None
    subject: str | None = None
    subject_matches: str | None = Field(default=None, alias="subjectMatches")

    @model_validator(mode="after")
    def _has_criterion(self) -> Self:
        if self.to is None and self.subject is None and self.subject_matches is None:
            raise ValueError("email.match needs at least one of: to / subject / subjectMatches")
        if self.subject_matches is not None:
            _check_regex(self.subject_matches, "email.match.subjectMatches")
        return self


class EmailExtract(_Model):
    """Pull a value from the matched message body into `${vars.<var>}` via a regex.

    `bodyMatches` is a regex; its first capturing group (or the whole match, if it has none) is the
    value written to `var`. A matched message whose body the regex does not hit fails the step.
    """

    var: str
    body_matches: str = Field(alias="bodyMatches")

    @model_validator(mode="after")
    def _valid_regex(self) -> Self:
        _check_regex(self.body_matches, "email.extract.bodyMatches")
        return self


class Email(_Model):
    """`email` — poll a mailbox until a matching message arrives, extract a value into `${vars.*}`.

    `match` selects the awaited message, `extract` pulls the value, and `timeout` (seconds, required)
    bounds the poll — a condition wait, never a fixed sleep (BE-0046). The mailbox endpoint lives in
    config (`targets.<name>.mailbox`), so the scenario stays app-agnostic and credential-free.
    """

    match: EmailMatch
    extract: EmailExtract
    timeout: float = Field(gt=0)


class ClearKeychain(_Model):
    """Reset the Simulator's keychain (saved passwords, certificates)."""


class ClearClipboard(_Model):
    """Clear the Simulator's pasteboard."""


class SetClipboard(_Model):
    """Seed the Simulator's pasteboard with text (simctl pbcopy), for paste flows."""

    text: str


class Background(_Model):
    """Send the app to the background, as pressing the Home button does.

    Backgrounds without terminating (SpringBoard is brought to the front), so the app's state
    survives for a later `foreground`.
    """


class Foreground(_Model):
    """Resume a backgrounded app to the foreground (simctl launch, without terminating it).

    The other half of `background`. It adds no settle sleep — any wait after resuming is the
    scenario's own condition wait.
    """


class OverrideStatusBar(_Model):
    """Override the Simulator's status bar for deterministic screenshots.

    All fields are optional; only the provided fields are overridden.
    """

    time: str | None = None
    battery_level: int | None = Field(default=None, alias="batteryLevel")
    battery_state: str | None = Field(default=None, alias="batteryState")
    cellular_bars: int | None = Field(default=None, alias="cellularBars")
    wifi_bars: int | None = Field(default=None, alias="wifiBars")


class ClearStatusBar(_Model):
    """Remove any status bar overrides (restore the live status bar)."""
