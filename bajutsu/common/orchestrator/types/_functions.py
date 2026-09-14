"""The neutral defaults a run falls back to when a capability is absent."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from bajutsu.common.drivers import base
from bajutsu.common.drivers.actuation import ActuationReporter, Drained
from bajutsu.common.evidence.network import NetworkExchange

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

    *buttons* are the labels blocking the screen that this call has not already answered —
    `probe_native`'s `"unhandled"` answer, or the leftover buttons a `"dismissed"`,
    `already_dismissed`, `"unhandled"`, or raced-`"absent"` round (`subtract_labels`) finds beyond
    the shapes that round accounted for (`AlertGuardConfig.__call__`, BE-0418) — a fresh dismissal
    computes it too, since the alert it just tapped is never the only thing `buttons` enumerates.
    Three of those four credit per *read* — every rule `identified_alert_rules` resolves on it, not
    only the shapes this call has tapped — so a second, *declared* prompt co-present on the same
    read is subtracted too rather than named as one no rule identifies; `already_dismissed` alone
    credits the tapped shapes only, since every rule identified there is already one of them.
    `_AlertGuardGate._observe_native`'s `"unhandled"` and raced-`"absent"` branches
    (`waits/_alert_guard_gate.py`) pass a leftover credited that same per-read way.
    Not-yet-answered is weaker than "no rule accounts for it": a label two rules both name can
    collide (the built-in `notifications` and `tracking` both grant `"Allow"`) and land here
    un-subtracted even though a rule does identify it, once a per-label collision keeps
    `matching_alert_rule` from resolving either
    (`test_the_end_of_step_guard_filters_the_unhandled_note_when_the_collision_never_resolves`).
    Empty means the
    block was inferred from the collapsed-tree proxy rather than enumerated — a surface
    `springboard.alerts` cannot see, or a backend with no native query at all — so the note hedges
    rather than naming buttons nobody read. A prompt the policy *did* name and a tap that did not
    take is a different story, and gets `uncleared_prompt_note` below instead — the in-tree
    dismiss's own `NotTappable`, or `__call__` spending its whole round bound with the shape it
    most recently tapped, native or in-tree, still reading back on the final round (BE-0418).
    """
    if buttons:
        return f"{_UNHANDLED_ALERT_NOTE} (buttons: {', '.join(buttons)})"
    return _BLOCKED_SCREEN_NOTE


def uncleared_prompt_note(label: str) -> str:
    """A give-up on a prompt a rule named but could not clear (BE-0402): the in-tree dismiss's own
    `NotTappable`, `AlertGuardConfig.__call__` spending its whole round bound with the shape it
    most recently tapped — native or in-tree — still reading back on the final round, or a native
    tap `_AlertGuardGate._observe_native` resolved and then found the label twice
    (`AmbiguousSelector`, which `probe_native` reports as `"unhandled"`) — BE-0418.

    Deliberately not `alert_block_note`: "unhandled" would tell the author no rule identified the
    alert, when their rule did identify it and only the tap failed — it did not take, or never
    became deliverable — sending them to write a rule they already have instead of to the stuck
    prompt.
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


def subtract_labels(buttons: Sequence[str], shapes: Iterable[frozenset[str]]) -> list[str]:
    """The buttons left over once every one of *shapes*' own labels has been accounted for.

    Shared by `AlertGuardConfig.__call__`'s `_leftover_note` (`alert_guard_config.py`) and
    `_AlertGuardGate._observe_native`'s own race branch (`waits/_alert_guard_gate.py`, BE-0418), so
    the two do not each carry an independent copy of the same subtraction — the one-shot call's
    `dismissed` shapes and the mid-wait gate's own `identified_alert_rules` result are both, after
    all, "shapes whose labels this read should not still name".

    Subtracted with multiplicity, not as a set: `answered = {label for labels in shapes for label
    in labels}` followed by `[b for b in buttons if b not in answered]` would treat one shape as
    consuming *every* occurrence of its labels at once, so a second, genuinely live alert rendering
    the identical label pair — two permission prompts both offering "Allow" / "Don't Allow", say —
    would vanish from the leftover along with the one already accounted for. Removing one
    occurrence per label instead leaves that second alert's own copy behind to name, while two
    shapes sharing a label (BE-0418's own `notifications` / `tracking` pair, both granting "Allow")
    still cancel out to nothing between them.

    Subtracts each shape's own `identifying_labels`, deliberately not the *whole* `buttons` a round
    actually read: `buttons` is `system_alert_labels()`'s enumeration of every alert SpringBoard
    currently holds, not one alert's own button set, so a second, different, unidentified alert
    already up alongside one already accounted for — the ordinary shape of a stacked pair queued by
    one action, not a corner case, per
    `test_the_end_of_step_guard_still_names_a_co_present_alert_no_rule_identifies`
    (`tests/orchestrator/test_native_alert_guard.py`) — would have its own buttons permanently
    credited to the other alert and never surfaced (review finding: recording the whole read this
    way was tried and reverted). The trade-off this leaves stands the other way: a rule whose
    `identifying_labels` deliberately names only *some* of its alert's buttons
    (`ResolvedAlertRule`'s own docstring — "not a demand that the shape's labels be the alert's
    whole button set") leaves the rest stranded here, reported as if a second, different, unhandled
    alert had joined the one already accounted for. No currently declared native shape does this
    (every entry in `_LABELS` names its prompt's whole button set), and the two failure directions
    are irreconcilable from a flat button list alone — nothing here can tell "this alert's own
    unlisted button" apart from "a different alert's button that happens to be enumerable at the
    same moment" — so this side stays the accepted gap rather than the swallowed-stranger one,
    which a passing scenario hits today.
    """
    leftover = list(buttons)
    for shape in shapes:
        for label in shape:
            if label in leftover:
                leftover.remove(label)
    return leftover


def identified_alert_rules(
    rules: Sequence[ResolvedAlertRule], buttons: Sequence[str]
) -> list[ResolvedAlertRule]:
    """Every rule whose shape is uniquely identified on `buttons`: no excluded label present, and
    each identifying label present exactly once.

    The shared accept test behind `matching_alert_rule` below (its first element), so a caller
    crediting a whole read against every rule that could act on it — the mid-wait gate's own race
    branch, subtracting every declared prompt's own labels from a leftover (`waits/_alert_guard_gate.py`,
    BE-0418) — cannot drift from what a probe itself would actually resolve. A hand-rolled copy of
    this predicate elsewhere would credit, or refuse, a shape this function disagrees with the
    moment either changes, and the caller would find out only as a `wait` blocked by an alert
    nothing clears, or one a rule does identify silently going unreported.
    """
    present = list(buttons)
    return [
        rule
        for rule in rules
        if not any(label in present for label in rule.excluded_labels)
        and all(present.count(label) == 1 for label in rule.identifying_labels)
    ]


def matching_alert_rule(
    rules: Sequence[ResolvedAlertRule], buttons: Sequence[str]
) -> ResolvedAlertRule | None:
    """The first rule whose shape is uniquely identified on `buttons` (see `match_alert_rule`).

    Returns the rule itself, not just its tap label, for a caller that needs the shape a match
    resolved to — `AlertGuardConfig.__call__`'s native dedup (BE-0418) keys on a matched rule's
    `identifying_labels` rather than the raw `buttons` read, since `buttons` enumerates every
    alert SpringBoard currently holds and changes whenever a *different* alert joins or leaves the
    surface, while the rule that answers one already-dismissed alert does not.
    """
    identified = identified_alert_rules(rules, buttons)
    return identified[0] if identified else None


def match_alert_rule(rules: Sequence[ResolvedAlertRule], buttons: Sequence[str]) -> str | None:
    """The tap label of the first rule whose shape is uniquely identified on `buttons`.

    A rule matches when each of its identifying labels is present exactly once — the full set, not
    only the label it taps, since a single shared label cannot by itself distinguish one covered
    prompt from another — and no excluded label is present at all. None means no rule's prompt is
    identified, so the caller leaves the alert alone and reports it (BE-0406).
    """
    rule = matching_alert_rule(rules, buttons)
    return rule.tap_label if rule is not None else None


def push_interruption_policy(driver: base.Driver, guard: AlertGuardConfig | None) -> None:
    """Hand the backend the buttons it may press on an alert that interrupts its own interactions.

    XCUITest resolves such an alert *before* it synthesizes the interaction, and with nothing
    installed answers with the alert's own default button — granting a permission the scenario may
    have refused, with nothing in the report. Pushing the guard's already-resolved labels keeps that
    decision here: the backend applies `rules`, and an alert none of them identifies is declined and
    reported rather than guessed at — a fallback `probe_native` itself no longer has either
    (BE-0406).

    A rule the monitor can never meet is dropped rather than pushed: this surface exists for an
    alert in another process interrupting an XCUITest interaction, and one raised into the
    application's own process never reaches it. Dropping it is not merely tidy — the Swift side
    matches a rule by subset, so pushing an in-tree-only shape would re-open there the collision an
    `excluded_labels` set closes here (BE-0406).

    `governs` is true for any scenario whose guard is on, independent of whether any rule survived
    the drop above: a real declaration filtered down to nothing this surface can act on is not the
    same as no declaration at all, and only the latter should still leave a declined alert
    unreported. An absent guard (`systemAlertHandling: false`) pushes an empty, non-governing policy
    rather than skipping the call, so a scenario that switched the guard off does not inherit the
    previous scenario's policy from the resident runner. A backend that does not implement
    `InterruptionPolicyTarget` is simply never asked.

    Raises:
        ValueError: a rule this surface *can* meet carries an exclusion set. No such shape exists
            today — by construction, since every excluded shape is in-tree-only and dropped above —
            and one added later must fail loudly here rather than reach the monitor with its
            exclusion silently discarded, which is the subset-match collision this drop avoids.
    """
    if not isinstance(driver, base.InterruptionPolicyTarget):
        return
    rules: list[tuple[frozenset[str], str]] = []
    if guard is not None:
        reachable = [rule for rule in guard.rules if rule.native]
        excluding = [rule.tap_label for rule in reachable if rule.excluded_labels]
        if excluding:
            raise ValueError(
                "interruption policy cannot carry an exclusion set; rules tapping "
                f"{', '.join(sorted(excluding))} would be matched by subset on the runner"
            )
        rules = [(rule.identifying_labels, rule.tap_label) for rule in reachable]
    driver.set_interruption_policy(rules, guard is not None)


def drain_interruptions(driver: base.Driver) -> DrainedInterruptionEvents:
    """The prompts the backend answered, declined and swiped away at interruption time since the last drain.

    A tapped label is reported as an ordinary `AlertEvent` so a dismissal that happened inside the
    backend's own interruption handling is not missing from the run's report. A declined alert is
    reported as an `UndeclaredInterruption` instead — nothing answered it on the scenario's behalf,
    so it is not a dismissal, but its buttons are what lets a caller fail by name rather than let the
    interruption pass in silence (BE-0406 Unit 2b). A backend without the opt-in contributes nothing.

    A swiped-away notification banner is a dismissal like the first, so it joins `alerts` rather than
    `undeclared` — under its own kind, since nothing was tapped and the label is the notification's
    text (BE-0416). It is deliberately *not* an undeclared interruption: no scenario could have
    declared it, because a banner offers no button for a rule to identify, so failing the step over
    one would fail every run a notification happened to land in.
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


def drain_actuations(driver: base.Driver) -> Drained:
    """The actuations `driver` has performed since the last drain, or an empty drain if it reports none.

    The one place the `ActuationReporter` opt-in is read, so a backend that does not implement it
    simply contributes nothing rather than needing a stub.
    """
    if isinstance(driver, ActuationReporter):
        return driver.drain_actuations()
    return Drained(records=[], dropped=0)


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
