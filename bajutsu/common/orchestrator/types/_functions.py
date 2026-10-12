"""The neutral defaults a run falls back to when a capability is absent."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from bajutsu.common.drivers import base
from bajutsu.common.drivers.actuation import ActuationReporter, Drained
from bajutsu.common.evidence.network import NetworkExchange
from bajutsu.common.scenario.system_alerts import (
    TITLE_MARKER,
    SystemAlertChoice,
    SystemAlertPrompt,
    alert_title_marker,
    labels_cover,
    system_alert_shapes,
)

from .alert_event import AlertEvent
from .drained_interruption_events import DrainedInterruptionEvents
from .resolved_alert_rule import ResolvedAlertRule
from .undeclared_interruption import UndeclaredInterruption

if TYPE_CHECKING:
    from .alert_guard_config import AlertGuardConfig

# The notes a blocked step or wait appends to its own failure reason when the guard saw something it
# could not clear (BE-0402). Without it, a `tap` or `wait` stuck behind an
# unanticipated prompt reads as a bare "element not found" — the reading BE-0402 exists to remove.
_UNHANDLED_ALERT_NOTE = "an unhandled system alert is blocking the screen"
_BLOCKED_SCREEN_NOTE = "the screen appears blocked, possibly by a system alert or another overlay outside the app's view"
_UNCLEARED_PROMPT_NOTE = "a system prompt the guard could not clear is still up"


_UNDECLARED_INTERRUPTION_NOTE = "an undeclared system alert interrupted the run"


def _no_network() -> list[NetworkExchange]:
    return []


def alert_block_note(buttons: Sequence[str]) -> str:
    """What the guard saw blocking the screen, for a failure reason to name (BE-0402).

    *buttons* are a SpringBoard alert's buttons that no rule identifies — the *unidentified* record
    a resolving read returns (BE-0461). Empty means the block was inferred from the collapsed-tree
    proxy rather than enumerated, so the note hedges rather than naming buttons nobody read. A
    prompt a rule *did* name, whose press did not clear it, gets `uncleared_prompt_note` instead.
    """
    if buttons:
        return f"{_UNHANDLED_ALERT_NOTE} (buttons: {', '.join(buttons)})"
    return _BLOCKED_SCREEN_NOTE


def collapsed_tree_note(seconds: float) -> str:
    """A screen a read has positively ruled a SpringBoard alert out of, yet whose tree still fails
    `shows_app_ui` after *seconds* — no element other than the application itself carries a label or
    an identifier — for a wait to stop on rather than spend its whole timeout.

    Distinct from `alert_block_note`'s hedged form, which reads a collapsed tree as *possibly* an
    alert: here the read's own SpringBoard check just found none, so what holds the screen is
    something outside the tree no answer path reaches. Measured on an iOS 26.5 Simulator, iOS's Save
    Password alert can be left mid-presentation this way when a notification request lands at the
    same moment — never drawn, absent from every process's accessibility tree, yet modal over the app
    — and neither backgrounding the app, a hardware Escape, nor a tap on a control that is still
    enumerable cleared it.
    """
    return (
        f"no element below the application has carried a label or identifier for {seconds:.0f}s "
        "while no system alert is up — something outside the tree, such as a system prompt left "
        "mid-presentation, is holding the screen and nothing the guard can reach clears it"
    )


def uncleared_prompt_note(label: str) -> str:
    """A give-up on a prompt a rule named but could not clear (BE-0402): the *gave up* record the
    backend returns once a showing reaches its press ceiling, or stays unhittable past its bound
    (BE-0461).

    Deliberately not `alert_block_note`: "unhandled" would tell the author no rule identified the
    alert, when their rule did identify it — and send them to write a rule they already have.
    """
    return f"{_UNCLEARED_PROMPT_NOTE} (button: {label})"


def undeclared_interruption_note(events: Sequence[UndeclaredInterruption]) -> str:
    """What one or more alerts offered that interrupted the run and matched no `rules` entry.

    XCUITest answers such an alert with its own default button regardless of anything in this
    codebase — a monitor that merely declines cannot replace that handler, since XCUITest re-invokes
    a monitor that leaves the alert up on every following interaction (BE-0399). What this note
    changes is that the step or `expect` that met the interruption fails, naming the buttons, instead
    of continuing as if nothing had answered on the scenario's behalf (BE-0406 Unit 2b).

    Takes every event drained this poll, not just the first: a step or `expect` can meet more than
    one undeclared alert (a permission prompt and a save-password prompt inside one long wait), and
    naming only the first would cost the author a whole extra run to learn about the second.
    """
    groups = "; ".join(", ".join(event.buttons) for event in events)
    return f"{_UNDECLARED_INTERRUPTION_NOTE} (buttons: {groups})"


def _alert_button(label: str) -> base.Element:
    """One alert button as an element, so a selector can be matched against a bare label list.

    The native presence query reports labels, not elements; a button on it carries no identifier
    (SpringBoard names them by visible text alone) and no frame this side ever reads.
    """
    return {
        "identifier": None,
        "label": label,
        "traits": [base.Trait.BUTTON],
        "value": None,
        "frame": (0.0, 0.0, 0.0, 0.0),
        "nativeZ": None,
    }


def selector_names_button(sel: base.Selector, buttons: Sequence[str]) -> bool:
    """Whether a waiting `handleSystemAlert` step's selector names a button this alert offers.

    The reservation the reactive guard honors (BE-0406): a scenario may hold a `rules` entry for the
    very prompt a step is placed to answer, and with the opposite choice, so whichever party read
    the alert first would decide it. Matched through `base.matches` rather than a private label
    comparison, so `label` / `labelMatches` / `value` / `traits` mean here what they mean in every
    other selector. An `id` selector reserves nothing, which is the honest answer: no button on
    this surface carries one.

    `base.matches` ignores `index` and `within` by contract, so a selector carrying either reserves
    an alert its own `resolve_unique` may then reject. Erring that way is deliberate: the cost is
    that the step spends its timeout on an alert it could not have tapped anyway, where the
    opposite error would let the guard answer, with the opposite choice, the prompt the step exists
    to decide.
    """
    return any(base.matches(_alert_button(label), sel) for label in buttons)


def observed_alert_labels(driver: base.Driver) -> list[str]:
    """What a `handleSystemAlert` step's title check reads: the alert's buttons, then its title's marker.

    The title joins as one more label (`system_alerts.alert_title_marker`), so a prompt whose
    buttons another prompt shares — Local Network and notifications both offer "Allow" / "Don't
    Allow" — is identified by naming that marker, or ruled out by excluding it. A backend with no titles to report (`system_alert_titles` absent) contributes
    its buttons alone, as before.
    """
    buttons = driver.system_alert_labels()
    titles = getattr(driver, "system_alert_titles", None)
    if not buttons or not callable(titles):
        return buttons
    return [*buttons, *(alert_title_marker(title) for title in titles())]


AlertTitleCheck = Callable[[Sequence[str]], bool]


def prompt_title_check(
    prompt: SystemAlertPrompt, choice: SystemAlertChoice, locale: str | None
) -> AlertTitleCheck | None:
    """Whether a read (`observed_alert_labels`) can be the prompt a `handleSystemAlert` step names.

    The step taps by one button label, which Local Network and notifications share; the title tells
    them apart. A read passes when some shape of the prompt has every title marker it identifies by
    and no label it excludes. Buttons are left to the step's own selector, so a backend reporting no
    titles passes every prompt but `localNetwork`, whose shape names its title. None where the
    label table does not cover `locale`, since then the step has no shape to check.
    """
    if locale is None or not labels_cover(prompt, locale):
        return None
    shapes = system_alert_shapes(prompt, choice, locale)

    def check(observed: Sequence[str]) -> bool:
        present = set(observed)
        return any(
            not (shape.excluded_labels & present)
            and all(
                label in present
                for label in shape.identifying_labels
                if label.startswith(TITLE_MARKER)
            )
            for shape in shapes
        )

    return check


def push_interruption_policy(
    driver: base.Driver, guard: AlertGuardConfig | None, *, scenario_start: bool = False
) -> None:
    """Hand the backend the buttons it may press on an alert that interrupts its own interactions.

    XCUITest resolves such an alert *before* it synthesizes the interaction, and with nothing
    installed answers with the alert's own default button — granting a permission the scenario may
    have refused, with nothing in the report. Pushing the guard's already-resolved labels keeps that
    decision here: the backend applies `rules`, and an alert none of them identifies is declined and
    reported rather than guessed at (BE-0406).

    A rule the monitor can never meet is dropped rather than pushed: this surface exists for an
    alert in another process interrupting an XCUITest interaction, and one raised into the
    application's own process never reaches it. Each rule carries its `excluded_labels` too, so the
    monitor's accept test is this side's: `notifications` excludes the Local Network title's marker,
    and without the exclusion the monitor would answer that prompt by the notification rule.

    `governs` is true for any scenario whose guard is on, independent of whether any rule survived
    the drop above: a real declaration filtered down to nothing this surface can act on is not the
    same as no declaration at all, and only the latter should still leave a declined alert
    unreported. An absent guard (`systemAlertHandling: false`) pushes an empty, non-governing policy
    rather than skipping the call, so a scenario that switched the guard off does not inherit the
    previous scenario's policy from the resident runner. A backend that does not implement
    `InterruptionPolicyTarget` is simply never asked.

    The in-tree rules travel separately, in `tree_dedup_rules` order, together with the guard's
    pacing, for a backend that answers an application-owned prompt inside a request (BE-0461 Unit 2).
    *scenario_start* marks the push at scenario start, which also makes the backend forget every
    showing a previous scenario answered.

    Raises:
        ValueError: A rule claims both surfaces. No declared prompt does, and pushing it on one
            surface only would silently drop the other.
    """
    if not isinstance(driver, base.InterruptionPolicyTarget):
        return
    rules: list[tuple[frozenset[str], str, frozenset[str]]] = []
    in_tree_rules: list[tuple[frozenset[str], str, frozenset[str]]] = []
    pacing: base.AlertPacing | None = None
    if guard is not None:
        for rule in guard.rules:
            if rule.native and rule.in_tree:
                raise ValueError(
                    f"alert rule {sorted(rule.identifying_labels)} claims both the native and the "
                    "in-tree surface; a declared prompt shows on one"
                )
        rules = [_policy_rule(rule) for rule in guard.rules if rule.native]
        in_tree_rules = [_policy_rule(rule) for rule in guard.tree_dedup_rules]
        pacing = guard.pacing
    driver.set_interruption_policy(
        rules,
        guard is not None,
        in_tree_rules=in_tree_rules,
        pacing=pacing,
        scenario_start=scenario_start,
    )


def _policy_rule(rule: ResolvedAlertRule) -> tuple[frozenset[str], str, frozenset[str]]:
    """A resolved rule in the shape `set_interruption_policy` takes."""
    return (rule.identifying_labels, rule.tap_label, rule.excluded_labels)


def drain_interruptions(driver: base.Driver) -> DrainedInterruptionEvents:
    """The prompts the backend answered, declined and cleared at interruption time since the last drain.

    A tapped label is reported as an ordinary `AlertEvent` so a dismissal that happened inside the
    backend's own interruption handling is not missing from the run's report. A declined alert is
    reported as an `UndeclaredInterruption` instead — nothing answered it on the scenario's behalf,
    so it is not a dismissal, but its buttons are what lets a caller fail by name rather than let the
    interruption pass in silence (BE-0406 Unit 2b). A backend without the opt-in contributes nothing.

    A cleared notification banner is a dismissal like the first, so it joins `alerts` rather than
    `undeclared` — under its own kind, since nothing was tapped and the label is the notification's
    text (BE-0416). Cleared, not necessarily swiped away: the XCUITest runner also reports a banner
    that went by its own auto-dismissal, and logs which path cleared it to its own output. It is
    deliberately *not* an undeclared interruption: no scenario could have declared it, because a
    banner offers no button for a rule to identify, so failing the step over one would fail every
    run a notification happened to land in.
    """
    if not isinstance(driver, base.InterruptionPolicyTarget):
        return DrainedInterruptionEvents(alerts=[], undeclared=[])
    drained = driver.drain_interruptions()
    tapped = [AlertEvent(label=label) for label in drained.tapped]
    swiped = [AlertEvent(label=text, kind="notificationBanner") for text in drained.banners]
    return DrainedInterruptionEvents(
        alerts=tapped + swiped,
        undeclared=[UndeclaredInterruption(buttons=buttons) for buttons in drained.declined],
    )


def drain_actuations(driver: base.Driver, wall_offset_s: float) -> Drained:
    """The actuations `driver` has performed since the last drain, or an empty drain if it reports none.

    The one place the `ActuationReporter` opt-in is read, so a backend that does not implement it
    simply contributes nothing rather than needing a stub.

    Args:
        driver: The driver whose log to empty.
        wall_offset_s: The scenario's monotonic-to-epoch offset. Each record's `at` leaves the
            driver as a monotonic instant and leaves here on the same absolute footing as
            `StepOutcome.started_at` (BE-0348), so a report can subtract the video anchor from both.
    """
    if not isinstance(driver, ActuationReporter):
        return Drained(records=[], dropped=0)
    drained = driver.drain_actuations()
    return replace(
        drained,
        records=[
            a if a.at is None else replace(a, at=a.at + wall_offset_s) for a in drained.records
        ],
    )


# The budget both evidence-dir slugs share (BE-0420). Counted in characters, not bytes: a
# fullwidth/Japanese scenario name should not be cut shorter than an equally long ASCII one just
# because its characters encode to more bytes.
_MAX_SLUG_CHARS = 60


def _cap_chars(slug: str) -> str:
    """Cut `slug` to `_MAX_SLUG_CHARS` characters (BE-0420).

    Python string slicing is always at a codepoint boundary, so this can never split a character
    the way a byte-oriented cut could.
    """
    return slug[:_MAX_SLUG_CHARS]


def scenario_slug(name: str) -> str:
    """A filesystem-safe id derived from a scenario name (for its evidence dir).

    Capped at `_MAX_SLUG_CHARS` (BE-0420). `rstrip` drops a hyphen the cut can leave dangling. Two
    long names can now collide here; `_evidence_sid` still tells them apart by its own `NN-`
    prefix, and BE-0420's *Not doing* accepts the collision for the two callers that build a bare
    slug without one.
    """
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", name).strip("-").lower()
    return _cap_chars(slug).rstrip("-") or "scenario"


def sanitize_source_stem(stem: str) -> str:
    """Make a scenario file's stem safe as an evidence-dir `sid` component (BE-0417).

    Unlike `scenario_slug`, leaves Unicode letters/digits, `_`, and `.` alone (fullwidth/Japanese
    characters are ordinary in this codebase's own scenario names), so a plain stem like
    `login_flow` or `決済フロー` passes through unchanged; only a character unsafe in an unescaped
    HTML attribute / URL path segment (`#`, `?`, `/`, whitespace, …) is replaced.

    Capped at `_MAX_SLUG_CHARS` (BE-0420), which keeps a long file name from producing an evidence
    directory the filesystem refuses. No fallback is needed for an empty result: `re.sub` cannot
    turn a non-empty `stem` into an empty string, and slicing a non-empty string to a positive
    length always keeps at least its first character.
    """
    return _cap_chars(re.sub(r"[^\w.-]", "_", stem))
