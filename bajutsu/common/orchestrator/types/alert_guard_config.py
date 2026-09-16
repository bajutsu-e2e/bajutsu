"""The reactive system-alert guard's per-scenario configuration (BE-0315)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from bajutsu.common.drivers import base
from bajutsu.common.drivers.elements import tree_signature

from ._functions import (
    alert_block_note,
    identified_alert_rules,
    matching_alert_rule,
    selector_names_button,
    subtract_labels,
    uncleared_prompt_note,
)
from .alert_event import AlertEvent
from .resolved_alert_rule import ResolvedAlertRule

# The reactive guard's default native presence-query cadence (seconds), overridable per scenario /
# target / flag via `systemAlertHandling.pollInterval` (BE-0315, riding the BE-0177 precedence).
DEFAULT_ALERT_POLL_INTERVAL = 1.0

# The timeout the reactive guard passes `handle_system_alert` for its tap (BE-0315): 0 means "query
# SpringBoard once and tap if the button is present, else fail fast" — the guard has already observed
# the alert via `system_alert_labels`, so it never waits for one to appear (that is the proactive
# `handleSystemAlert` step's job), and a vanish-between-query-and-tap race fails fast rather than
# blocking the mid-wait poll.
_NATIVE_TAP_TIMEOUT = 0.0

# The end-of-step / expect call's own round bound (BE-0418). Unlike the mid-wait gate, this call
# gets no poll cycle of its own to loop on, so `__call__` loops internally instead. Numerically the
# same 3 as `_TREE_DISMISS_MAX_TAPS` (`_alert_guard_gate.py`), but not the same bound: that one
# ceilings retaps of a tap that *landed* and left the prompt showing, while the mid-wait path's own
# landing-race retry — the `ElementNotTappable` branch of `_dismiss_from_tree`, which Unit 2 mirrors
# here — gives up on time instead (`_decline_giveup(poll_interval)`), which a call with no poll
# cycle of its own has no equivalent of (BE-0418 Unit 2).
_GUARD_CALL_MAX_ROUNDS = 3

# What a native probe found: "incapable" (backend has no native path), "absent" (no alert — a
# deterministic fact), "dismissed" (a policy-named button was tapped), "unhandled" (an alert is up
# but no rule identifies it, so nothing clears it and the caller reports it instead),
# "reserved" (an alert is up and a waiting `handleSystemAlert` step named it, so this probe leaves
# it for the step's own tap — BE-0406), "already_dismissed" (the alert this round would tap is one
# `__call__` already dismissed earlier this call, still enumerable because its fade outlasted
# `settle` — declined without tapping, since a repeat tap under this dedup's own premise can only
# land on nothing (the alert genuinely gone) or on whatever the closing alert has by then revealed
# underneath it — BE-0418).
NativeAlertState = Literal[
    "incapable", "absent", "dismissed", "unhandled", "reserved", "already_dismissed"
]


def _widest_first(rules: Iterable[ResolvedAlertRule]) -> list[ResolvedAlertRule]:
    """*rules*, with a nested shape's wider sibling moved ahead of it — the only reordering this makes
    deliberately. It is not otherwise order-preserving: the wider rule is inserted directly ahead
    of the first rule it strictly contains, so every rule declaration order put between the two
    moves back a place with it, whether or not it nests with either (`narrower`, `unrelated`,
    `widest` — this file's own test fixture order — yields `widest`, `narrower`, `unrelated`).
    Subset is only a partial order, so no placement can leave every non-nesting pair where
    declaration put it. What BE-0177 needs is narrower than that and does hold: two layers
    declaring the same prompt resolve to the same shapes, so the scenario's copy of a shape stays
    ahead of the target's copy of it.

    `_resolve_alert_rule`'s own containment test excludes a candidate nesting with an
    already-dismissed shape either way (review finding), so which of a nested pair gets dismissed
    first no longer decides whether the other can re-tap — but it still decides *which* rule the
    dismissal is recorded under, and BE-0177 needs that to be the same rule the mid-wait gate's own
    `_dismiss_from_tree` resolves for the identical screen, not whichever one a scenario happened to
    list first. Reordering the nested pair, rather than
    sorting every rule by raw label count, is what a plain width sort cannot do for BE-0177's own
    precedence — a stable sort only preserves scenario-before-target precedence *among same-shape
    rules* (review finding: sorting by width promoted a wider target rule ahead of a narrower
    scenario rule the two do not otherwise relate). Implemented as a stable insertion pass: each
    rule is placed ahead of the first already-placed rule its own shape strictly contains, and
    appended otherwise. Swapping only *adjacent* pairs would not reach a nested pair separated by
    an unrelated one: subset is a partial order, so one non-nesting rule between a narrow shape and
    its wider sibling blocks every adjacent swap and leaves the narrow shape matching first (review
    finding — a savePassword-style catalogue declaring its three shapes narrower, unrelated, widest
    would still double-tap the wide sheet under the adjacent-swap version).

    Without reordering `tree_rules` itself: `_AlertGuardGate._dismiss_from_tree`
    (`waits/_alert_guard_gate.py`) and `dismiss_from_tree_once` below both sort a *copy* of that
    property to match through, so it stays the shared, declaration-ordered base both derive from,
    rather than something either consumer's own call reorders in place.
    """
    result: list[ResolvedAlertRule] = []
    for rule in rules:
        for i, placed in enumerate(result):
            if placed.identifying_labels < rule.identifying_labels:
                # `placed` is a proper subset of `rule` — the wider shape is tried first whenever
                # both already match, so a scenario declaring the pair in either order still
                # resolves the same rule the mid-wait gate's own `tree_dedup_rules` copy would.
                result.insert(i, rule)
                break
        else:
            result.append(rule)
    return result


def _resolve_alert_rule(
    rules: Sequence[ResolvedAlertRule],
    buttons: Sequence[str],
    dismissed: frozenset[frozenset[str]],
) -> ResolvedAlertRule | None:
    """The rule a round should act on, honoring the same-shape retry `probe_native`,
    `dismiss_from_tree_once`, and `AlertGuardConfig.__call__` (BE-0418) must all agree on bit for
    bit — shared by the native and tree paths alike, since both face the same lingering-fade race.

    The plain match, unless its shape nests with one `dismissed` already names — a lingering fade
    of an already-answered alert, read with a subset of the buttons the dismissing round itself
    matched on, *or* the same alert now rendering a label it had not yet drawn when a narrower
    reading of it was dismissed — in which case the search retries among the rules whose shape does
    not nest with any dismissed shape, so a real, not-yet-answered alert enumerable alongside that
    fade (the stacked case this loop exists to clear) is still found rather than declined along with
    the fade. Containment either way, not equality, is what keeps a `savePassword`-style policy
    safe: `choice: deny` there resolves to three rules, two of whose shapes nest (the web-form shape
    naming "Save Password", "Never for This Website", and "Not Now"; the iOS 18.6 in-app shape
    naming only "Save Password" and "Not Now" — the 26.5 shape, "Save" and "Not Now", nests with
    neither, sharing only "Not Now" with the widest), both tapping the same button. A fade that
    still enumerates the wider shape's buttons would otherwise match the narrower sibling and tap it
    a second time, the case a one-directional test alone still closes — but a shape that renders its
    widest label a frame late reaches the opposite order: the narrower reading matches and is
    dismissed first, and only a *reverse* containment check keeps the same alert's now-fully-rendered
    wider reading from matching afresh and re-tapping it a round later (review finding). `__call__`
    calls this again, over the same `buttons` a dismissing round just read, to learn which shape it
    tapped without either probe growing a return member only one caller needs.
    """

    def _nests_with_a_dismissed_shape(candidate: ResolvedAlertRule) -> bool:
        return any(
            candidate.identifying_labels <= shape or shape <= candidate.identifying_labels
            for shape in dismissed
        )

    rule = matching_alert_rule(rules, buttons)
    if rule is not None and _nests_with_a_dismissed_shape(rule):
        rule = matching_alert_rule(
            [r for r in rules if not _nests_with_a_dismissed_shape(r)], buttons
        )
    return rule


def _leftover_note(
    buttons: Sequence[str], dismissed: frozenset[frozenset[str]], fallback: str
) -> str:
    """`alert_block_note` over `subtract_labels`, or *fallback* when nothing is left over.

    The one computation `AlertGuardConfig.__call__` (BE-0418) repeats at every round kind that can
    end the call with a native leftover still live: a co-present alert this call has not already
    answered always takes precedence over whatever diagnosis *fallback* would otherwise report,
    since something else is demonstrably still up regardless of what that other diagnosis found.
    """
    leftover = subtract_labels(buttons, dismissed)
    return alert_block_note(leftover) if leftover else fallback


def _read_tree(
    driver: base.Driver,
) -> tuple[list[base.Element], list[str], tuple[tuple[str | None, str | None], ...]]:
    """A fresh read of *driver*'s current tree: its elements, the identifier-less labelled buttons
    among them, and that read's own `tree_signature`.

    Factored out of `dismiss_from_tree_once` so `AlertGuardConfig.__call__`'s own final tree check
    (BE-0418 review finding) can take an equally fresh read without duplicating the filter or
    re-deriving a stale signature from whatever round last happened to touch the tree.
    """
    elements = driver.query()
    buttons = [
        el["label"]
        for el in elements
        if el["label"] and not el["identifier"] and base.Trait.BUTTON in el["traits"]
    ]
    return elements, buttons, tree_signature(elements)


def _bound_exhaustion_note(
    *,
    dismiss_shape: frozenset[str] | None,
    dismiss_label: str | None,
    buttons: Sequence[str],
    round_index: int,
) -> str:
    """Shared by `AlertGuardConfig.__call__`'s native and tree paths (BE-0418): the note diagnosing
    that the shape most recently tapped on either surface never actually cleared, once the round
    bound is spent with it still enumerable on this, the final round's own read.

    A direct containment check against `dismiss_shape` on the final round alone, not a streak
    counted across every round since the tap: an earlier version required every round in between to
    have re-observed the same shape, which broke the moment any *other* kind of round — an
    "unhandled" collision on the native side, a stuck tree prompt on the other, a fresh dismissal of
    a *different* shape — sat between the tap and the bound, since nothing advanced the streak for a
    round that never reached this check at all (review finding: the streak count and `round_index`
    then permanently disagreed by the width of that gap, for the rest of the call). The final round
    is the only one this call still has left to act on regardless of what happened in between, so
    checking it alone both answers the only question that still matters and cannot be thrown off by
    a gap of any width or kind.

    Only the native caller (`__call__`'s `already_dismissed` branch) passes the result to
    `_leftover_note` as its *fallback*, so a native leftover this call has not already answered can
    outrank it there — a co-present, unhandled button the same read enumerated alongside the fade.
    The tree caller (the lingering-fade branch) calls this directly instead: that branch runs only
    inside `if not buttons:`, where the native surface is provably empty, so a native leftover could
    never be live there regardless of how the result were wrapped — the tree side simply has no
    leftover of its own to defer *from*. Whether to apply either result at all is each caller's own
    to decide first — a still-open tree diagnosis, `stuck_tree_label`, takes precedence over either
    surface's own note.
    """
    if (
        round_index == _GUARD_CALL_MAX_ROUNDS - 1
        and dismiss_shape is not None
        and dismiss_shape <= set(buttons)
    ):
        assert dismiss_label is not None  # a shape can only be `dismiss_shape` once tapped
        return uncleared_prompt_note(dismiss_label)
    return ""


def _raced_exhaustion_note(
    rules: Sequence[ResolvedAlertRule],
    buttons: Sequence[str],
    dismissed: frozenset[frozenset[str]],
    round_index: int,
    fallback_shape: frozenset[str] | None,
    fallback_label: str | None,
    *,
    require_corroboration: frozenset[str] | None | Literal[False] = False,
) -> tuple[str, bool]:
    """The `_bound_exhaustion_note` earned by the rule this round's own read resolves — shared by
    the race and `"unhandled"` branches of `__call__` (BE-0418 review finding): both face a round
    that matched or raced a rule without ever *tapping* it, so a possibly-stale
    `fallback_shape`/`fallback_label` from an earlier round's own tap would go silent on the final
    round precisely when this call never tapped anything at all. Neither caller has a further use
    for the rule itself: each already credits every rule `identified_alert_rules` finds on the same
    read for its own leftover, rather than only the one this resolves.

    `require_corroboration`, passed only by the race branch (BE-0418 review finding): a freshly
    resolved rule's own `shape <= set(buttons)` is tautological there, since `buttons` on that path
    is the very read `matching_alert_rule` matched the rule against *before* the tap raced away —
    the shape being "still enumerable" proves nothing about whether the race was a genuine,
    benign self-resolve (`probe_native`'s own `ElementNotFound` branch) rather than a shape truly
    stuck across rounds. A freshly resolved rule counts as exhausting evidence there only when its
    shape equals `require_corroboration` — the shape the *most recent* race round resolved, not
    necessarily the immediately preceding one: `__call__` updates it from its race branch alone,
    so a round of any other kind in between neither corroborates nor clears it. Two race rounds
    on the identical shape is real repetition, not a coincidence of pre-tap timing; `None` means
    no earlier round of this call raced at all, and can never equal a real shape, so a first-ever
    race is never enough on its own. Left at the default
    `False` for the `"unhandled"` caller, where the containment check is already sound and needs no
    such gate: an `AmbiguousSelector` tap failure means the label matched *twice*, positive evidence
    the button is still there, not merely enumerable at query time.

    Returns the note alongside whether it fell back to *fallback_shape*/*fallback_label* rather than
    a freshly resolved (and, for the race branch, corroborated) rule — the caller's own signal for
    whether a non-empty note here means the fallback's own `AlertEvent`, if any, is now known to
    have never actually cleared and should be withdrawn (BE-0418 review finding), the same way a
    fresh resolution's own note never implicates an earlier tap this round had nothing to do with.
    """
    rule = _resolve_alert_rule(rules, buttons, dismissed)
    if (
        rule is not None
        and require_corroboration is not False
        and rule.identifying_labels != require_corroboration
    ):
        rule = None
    shape, label = (
        (rule.identifying_labels, rule.tap_label)
        if rule is not None
        else (fallback_shape, fallback_label)
    )
    note = _bound_exhaustion_note(
        dismiss_shape=shape, dismiss_label=label, buttons=buttons, round_index=round_index
    )
    return note, rule is None


def _withdraw(alerts: list[AlertEvent], event: AlertEvent | None) -> None:
    """Un-record *event* from *alerts*, by identity, if it is there (BE-0418 review finding) — the
    one-shot twin of `_AlertGuardGate._withdraw_tree_event` (`waits/_alert_guard_gate.py`). A tap is
    recorded the moment it lands, which is the only moment it *can* be: nothing then distinguishes a
    tap that lands from one the app never acts on. Reaching the round bound with the same shape
    still reading back is where that becomes knowable, and leaving the event would make the report
    contradict `blocked_note` right beside it — a step that both names a prompt as never cleared and
    claims to have dismissed it. By identity, not equality, so an earlier, genuinely-cleared
    dismissal of the same shape (a re-raised prompt this call tapped a second time) keeps its own
    record even when *this* one is withdrawn.
    """
    if event is None:
        return
    for i, recorded in enumerate(alerts):
        if recorded is event:
            del alerts[i]
            return


def _withdraw_if_exhausted(
    alerts: list[AlertEvent],
    event: AlertEvent | None,
    note: str,
    exhaustion_note: str,
    *,
    used_fallback: bool = True,
) -> AlertEvent | None:
    """The one check all three native diagnosis branches of `__call__` share before withdrawing
    *event* (BE-0418 review finding): only when *note* — what the round actually reports — is
    exactly *exhaustion_note*, never when a co-present leftover outranked it (`_leftover_note`'s own
    precedence rule already implies nothing here says *event*'s own tap did not land) and, for the
    race and `"unhandled"` branches, never when *exhaustion_note* came from a rule this round's own
    read freshly resolved rather than from *event*'s own fallback shape (`used_fallback`, always
    `True` for `already_dismissed`'s direct call, which has no such rule to prefer).

    Returns the value the caller's own `*_dismiss_event` variable should hold afterward: `None` once
    withdrawn, *event* unchanged otherwise — so a call site can write `x_dismiss_event =
    _withdraw_if_exhausted(...)` in place of the three-line branch this factors out of `__call__`.
    """
    if used_fallback and note and note == exhaustion_note:
        _withdraw(alerts, event)
        return None
    return event


def _fresh_dismiss_leftover_note(
    native_rules: Sequence[ResolvedAlertRule],
    buttons: Sequence[str],
    dismissed_native: frozenset[frozenset[str]],
    round_index: int,
) -> str:
    """`__call__`'s own `"dismissed"` branch note, factored out to keep that method under ruff's
    statement ceiling: a co-present, not-yet-answered leftover always takes precedence, and the
    fallback beneath it resolves a rule fresh against this round's own read rather than trusting a
    possibly-stale shape from an earlier tap — see the call site's own comment for why no fallback
    shape of its own is needed here.
    """
    fallback_note, _ = _raced_exhaustion_note(
        native_rules, buttons, dismissed_native, round_index, None, None
    )
    return _leftover_note(
        buttons,
        dismissed_native
        | {rule.identifying_labels for rule in identified_alert_rules(native_rules, buttons)},
        fallback_note,
    )


def _already_dismissed_note(
    alerts: list[AlertEvent],
    native_rules: Sequence[ResolvedAlertRule],
    native_dismiss_shape: frozenset[str] | None,
    native_dismiss_label: str | None,
    native_dismiss_event: AlertEvent | None,
    buttons: Sequence[str],
    dismissed_native: frozenset[frozenset[str]],
    round_index: int,
) -> tuple[str, AlertEvent | None]:
    """`__call__`'s own `"already_dismissed"` branch note, factored out to keep that method under
    ruff's statement ceiling: a leftover the same read still holds outranks the exhaustion
    diagnosis, and `native_dismiss_event` is withdrawn exactly when it does not (see
    `_withdraw_if_exhausted`'s own docstring).

    Credits every rule `identified_alert_rules` finds on this read, not only the shapes
    `dismissed_native` already names (BE-0418 review finding), the same way the `"dismissed"`,
    race, and `"unhandled"` branches all credit their own leftover: a nested pair — a wider
    declared sibling `_resolve_alert_rule`'s own containment test treats as the same answered
    alert once it renders a label a narrower reading of it was dismissed without — reaches this
    branch (`_resolve_alert_rule` returns `None` because every match nests with `dismissed_native`)
    with the wider shape's own extra label still on `buttons`, and bare `dismissed_native` would
    report that label as an alert no rule identifies, when a rule does identify it.
    """
    exhaustion_note = _bound_exhaustion_note(
        dismiss_shape=native_dismiss_shape,
        dismiss_label=native_dismiss_label,
        buttons=buttons,
        round_index=round_index,
    )
    note = _leftover_note(
        buttons,
        dismissed_native
        | {rule.identifying_labels for rule in identified_alert_rules(native_rules, buttons)},
        exhaustion_note,
    )
    return note, _withdraw_if_exhausted(alerts, native_dismiss_event, note, exhaustion_note)


def _raced_or_unhandled_note(
    alerts: list[AlertEvent],
    native_rules: Sequence[ResolvedAlertRule],
    buttons: Sequence[str],
    dismissed_native: frozenset[frozenset[str]],
    round_index: int,
    native_dismiss_shape: frozenset[str] | None,
    native_dismiss_label: str | None,
    native_dismiss_event: AlertEvent | None,
    leftover_dismissed: frozenset[frozenset[str]],
    *,
    require_corroboration: frozenset[str] | None | Literal[False] = False,
) -> tuple[str, AlertEvent | None]:
    """The identical note shared by `__call__`'s race and `"unhandled"` branches, factored out to
    keep that method under ruff's statement ceiling: prefers whichever rule this round's own read
    freshly resolves over a possibly-stale fallback shape (`_raced_exhaustion_note`), then withdraws
    `native_dismiss_event` exactly when nothing did and the exhaustion diagnosis is what the round
    actually reports (see `_withdraw_if_exhausted`'s own docstring). `require_corroboration` is
    passed straight through to `_raced_exhaustion_note` — see its own docstring; only the race
    branch passes anything other than the default.
    """
    exhaustion_note, used_fallback = _raced_exhaustion_note(
        native_rules,
        buttons,
        dismissed_native,
        round_index,
        native_dismiss_shape,
        native_dismiss_label,
        require_corroboration=require_corroboration,
    )
    note = _leftover_note(buttons, leftover_dismissed, exhaustion_note)
    return note, _withdraw_if_exhausted(
        alerts, native_dismiss_event, note, exhaustion_note, used_fallback=used_fallback
    )


def _first_lingering_tree_shape(
    dismissed_tree_info: dict[frozenset[str], tuple[str, AlertEvent | None]],
    tree_buttons: Sequence[str],
) -> frozenset[str] | None:
    """The first shape in *dismissed_tree_info* (insertion order — the order each was tapped this
    call) still fully enumerable in *tree_buttons*, or `None` if none is.

    `__call__`'s own lingering-fade branch used to diagnose only the most recently tapped shape
    against this same containment test, even though the test itself (`any(...)`) already ranges
    over every dismissed shape: an earlier sheet this call tapped that never closed would report
    nothing at all once a later, different sheet was tapped and clears, since only the latter's own
    shape was ever compared (BE-0418 review finding). Returning the shape the test actually found
    lets the caller diagnose it directly instead.
    """
    buttons = set(tree_buttons)
    return next((shape for shape in dismissed_tree_info if shape <= buttons), None)


def _lingering_tree_note(
    alerts: list[AlertEvent],
    dismissed_tree_info: dict[frozenset[str], tuple[str, AlertEvent | None]],
    lingering_shape: frozenset[str],
    tree_buttons: Sequence[str],
    round_index: int,
) -> str:
    """`__call__`'s own lingering-fade branch note, factored out to keep that method under ruff's
    statement ceiling: diagnoses *lingering_shape* — whichever dismissed shape
    `_first_lingering_tree_shape` actually found still enumerable, not only the most recently
    tapped one (BE-0418 review finding) — and withdraws its own `AlertEvent` once the diagnosis
    fires, the same way `_withdraw_if_exhausted` does for the native side.
    """
    label, event = dismissed_tree_info[lingering_shape]
    note = _bound_exhaustion_note(
        dismiss_shape=lingering_shape,
        dismiss_label=label,
        buttons=tree_buttons,
        round_index=round_index,
    )
    if note:
        # The final round's own read still shows the sheet this call itself tapped, so it never
        # actually closed — withdraw the `AlertEvent` rather than let `alerts` claim a dismissal
        # `blocked_note` says never happened (BE-0418 review finding).
        _withdraw(alerts, event)
        dismissed_tree_info[lingering_shape] = (label, None)
    return note


def _final_tree_check(
    driver: base.Driver,
    alerts: list[AlertEvent],
    note: str,
    dismissed_tree_info: dict[frozenset[str], tuple[str, AlertEvent | None]],
    *,
    tree_read_round: int | None,
    round_index: int,
) -> str:
    """`__call__`'s own post-loop tree check, factored out to keep that method under ruff's
    branch/statement ceiling: the lingering-fade branch inside `"absent"` is the only place that can
    report a tapped tree shape still covering the screen, so a call whose *final* round takes any
    other path never runs it, even though the evidence that branch would have used survives right
    here to check.

    A *fresh* query, deliberately, rather than the last round's own read: a path that leaves
    `dismissed_tree_info` non-empty and reaches here without itself settling first also called
    `settle()` on an *earlier* round on its way here, so a stale read would misreport a sheet the
    call's own settling has since watched close (BE-0418 review finding). Read-only, so nothing here
    risks the unlicensed tap the loop's own gate above exists to prevent. Skipped only when
    `tree_read_round == round_index`: a round whose own read already tested this exact evidence and
    found it false, with no settle since to have moved the screen, would have a fresh query here
    only reproduce that same false (BE-0418 review finding) — `None` (the shape came from a tap
    `dismiss_from_tree_once` recorded but no *later* read has yet tested it) still runs the check,
    the same as any round strictly before the final one. Not gated on this fresh read's own
    signature matching the tap's own, for the identical reason the lingering-fade branch dropped
    that requirement: a sheet that changed shape without closing — a validation error re-presenting
    it, say — would otherwise never be named here either, and this check never taps regardless of
    the read it takes, so a revealed screen's ordinary buttons happening to share the dismissed
    shape's labels never cost a second tap. They cost more than an imprecise note, though: the
    `_withdraw` below takes that shape's own `AlertEvent` back on the same containment check, so a
    sheet that really did close is reported as never dismissed — and when it was this call's only
    dismissal, `__call__`'s return flips to `False` and the caller skips its one-shot retry.

    Resolved via `_first_lingering_tree_shape` over the whole of `dismissed_tree_info`, the same
    helper the in-loop lingering-fade branch uses — not a single most-recently-tapped shape of its
    own — so this check and that branch can never disagree about which dismissed sheet is still
    stuck (BE-0418 review finding): a call whose final round takes a path that never reads the tree
    (a native alert dismissed on the last round, say) reaches here with an *earlier* tree tap still
    unconfirmed, and only the shape actually found still enumerable is the one whose own `AlertEvent`
    is withdrawn from `alerts` in place — the post-loop twin of the in-loop withdrawal.

    A non-empty *note* does not skip this check the way the other two conditions do: `note` is a
    reporting-precedence decision (a native diagnosis wins over the tree's own), not evidence that
    the tree tap actually landed, and a round that ends on an unrelated native note — an undeclared
    alert raising after the tap, say — has never itself read the tree since. Skipping the withdrawal
    there would ship an `AlertEvent` for a sheet this call never confirmed closed, and a `True`
    return whose one-shot retry then spends itself against a screen that sheet is still covering
    (BE-0418 review finding). The withdrawal still runs; only the returned note keeps *note*'s own
    precedence when it is non-empty.

    Also skipped when `round_index < _GUARD_CALL_MAX_ROUNDS - 1`: `round_index` here is the round the
    loop *stopped* at, which is the final round only when the loop actually ran it out — a branch
    that `break`s early (an "unhandled" alert nothing is worth another round for, say) stops on an
    earlier round with rounds still unspent. The in-loop lingering-fade branch reaches this same
    withdrawal only through a check gated on `round_index == _GUARD_CALL_MAX_ROUNDS - 1`
    (`_bound_exhaustion_note`); reaching it here on an earlier round would withdraw a tap on
    evidence the in-loop branch itself would not yet have trusted, on a call that still had a round
    left to test it properly (BE-0418 review finding).
    """
    if (
        not dismissed_tree_info
        or round_index < _GUARD_CALL_MAX_ROUNDS - 1
        or (tree_read_round is not None and tree_read_round >= round_index)
    ):
        return note
    _, final_tree_buttons, _ = _read_tree(driver)
    lingering_shape = _first_lingering_tree_shape(dismissed_tree_info, final_tree_buttons)
    if lingering_shape is None:
        return note
    label, event = dismissed_tree_info[lingering_shape]
    _withdraw(alerts, event)
    return note or uncleared_prompt_note(label)


def _native_round_worth_another_try(
    dismissed_native: frozenset[frozenset[str]],
    buttons: Sequence[str],
    native_rules: Sequence[ResolvedAlertRule],
) -> bool:
    """Whether a native round that resolved nothing new might still read differently on a later
    round (BE-0418) — asked by the `"unhandled"` branch alone. The time-of-check/time-of-use race
    branch of `"absent"` faces the same read but never needs to ask: the raced rule's own shape is
    in `buttons` by construction, so this would always answer `True` there (see that branch).

    Settling and giving the surface another round, rather than ending the call, only pays off in
    two cases: a fade this call itself created (`dismissed_native` non-empty), or a live
    shared-label collision (some rule's own shape is present on `buttons`, just not uniquely). A
    rule ruled out by an *excluded* label being present is neither of those — its shape can be a
    subset of `buttons` with no fade and no collision at all — so the `excluded_labels` check keeps
    that case out too (review finding: no currently declared native rule has an excluded label, but
    a tree-side one already does, `savePassword`'s 26.5 shape, and native rules gain them the same
    way tree ones did). Neither holding means a later round can only re-read exactly what this one
    did or find the alert gone on its own, either of which would spend or erase a diagnosis this
    round could otherwise keep for nothing this call could still change.
    """
    return bool(dismissed_native) or any(
        rule.identifying_labels <= set(buttons) and not rule.excluded_labels & set(buttons)
        for rule in native_rules
    )


@dataclass(frozen=True)
class NotTappable:
    """`dismiss_from_tree_once`'s landing race: the button resolved but the tap could not land.

    Distinguished from `None` ("nothing to do here") so the caller's own round-bounded loop can
    retry the same tap rather than giving up on it (BE-0418) — the in-tree twin of the retry
    `_alert_guard_gate.py` already carries across polls, for the same scrim-over-a-still-animating-
    sheet race. `label` is the tap label `_resolve_alert_rule` already resolved, so a caller that gives
    up can name it in `uncleared_prompt_note` without resolving it a second time.

    `shape` is that same rule's `identifying_labels`, carried alongside the label rather than in
    place of it: two `in_tree` rules can share one tap label under different choices — `savePassword`
    under `choice: deny` resolves to three shapes that all tap "Not Now" — so a caller comparing
    labels alone to ask "is this still the same stuck prompt" can match a *different* prompt that
    merely shares the label, clearing a diagnosis for a sheet that is still stuck (BE-0418 review
    finding). The label still names the prompt in the note; the shape is what identifies it.
    """

    label: str
    shape: frozenset[str]


@dataclass
class AlertGuardConfig:
    """The reactive system-alert guard's per-scenario configuration and dismiss entry point (BE-0315).

    `guard(driver, alerts, settle=...)` clears whatever blocks the screen, across up to
    `_GUARD_CALL_MAX_ROUNDS` rounds (BE-0418), through the deterministic native path (BE-0316's
    SpringBoard query + `handle_system_alert`) on a backend advertising `HANDLE_SYSTEM_ALERT`, the
    in-tree dismiss for an app-owned prompt that query cannot see, or both in the same call when one
    sits stacked in front of the other. Every path here is deterministic: BE-0402 removed the
    AI-vision fallback from `run`, so where neither path can act the guard does nothing and records
    `blocked_note` for the blocked step to report. `rules` are the whole policy (BE-0406) — each
    answers one named prompt regardless of which label it shares with another, and an alert no rule
    identifies is left alone rather than answered by a guessed button. `poll_interval` is the native
    presence-query cadence the mid-wait gate polls on, decoupled from the wait's own condition poll.
    """

    rules: list[ResolvedAlertRule] = field(default_factory=list)
    poll_interval: float = DEFAULT_ALERT_POLL_INTERVAL
    # What the most recent `__call__` saw blocking the screen and could not clear, for the end-of-step
    # and `expect` retry to append to the step's own failure reason (BE-0402). Rewritten on every
    # call, never accumulated across calls: it states what this call's rounds (BE-0418) saw, not that
    # a block was ever seen on some earlier step. Within a call each round overwrites what the one
    # before it found, except while an earlier tree round's `NotTappable` diagnosis is still open —
    # see `__call__`. Safe to hold here
    # because `_guard_for` builds one config per scenario and a scenario's steps run in sequence, so
    # no note crosses a scenario or a worker boundary.
    blocked_note: str = field(default="", init=False)

    @property
    def tree_rules(self) -> list[ResolvedAlertRule]:
        """The rules the in-tree dismissal may act on: those whose prompt that path can reach.

        Arming on *any* rule would widen the tree match past what the author asked for — a scenario
        declaring `notifications` alone would arm one for a prompt that only ever appears in
        SpringBoard, and an application screen happening to show identifier-less "Allow" and
        "Don't Allow" buttons would be tapped (BE-0406).

        In declaration order — deliberately, unlike `native_rules` below: BE-0177's
        scenario-before-target precedence has to survive here, and what it actually needs is
        narrower than full declaration order — two layers declaring the same prompt resolve to the
        same shapes, and `_widest_first` below never reorders two rules of equal shape, so the
        scenario's copy still precedes the target's copy of it once `tree_dedup_rules` below
        applies that reordering to a copy of this property (review finding: `_widest_first`
        reorders more than just a nested pair when an unrelated rule sits between the two, so this
        property could not claim full declaration order survives regardless). `tree_dedup_rules`
        applies it once, so `_AlertGuardGate._dismiss_from_tree` (`waits/_alert_guard_gate.py`) and
        `dismiss_from_tree_once` below — declared twins over the same screen — read the identical
        ordering rather than each calling `_widest_first` on its own copy, which would leave
        nothing to stop the two from drifting apart the way this file's own `native_rules`
        docstring warns a duplicated filter would (BE-0418 review finding).
        """
        return [rule for rule in self.rules if rule.in_tree]

    @property
    def tree_dedup_rules(self) -> list[ResolvedAlertRule]:
        """`tree_rules`, with a nested shape's wider sibling moved ahead of it (`_widest_first`) —
        the one place every in-tree, dedup-aware match reads from, so `dismiss_from_tree_once`
        below, `__call__`'s own tree re-resolution, and `_AlertGuardGate._dismiss_from_tree`
        (`waits/_alert_guard_gate.py`) can never resolve the same screen through three different
        orderings (BE-0418 review finding).
        """
        return _widest_first(self.tree_rules)

    @property
    def native_rules(self) -> list[ResolvedAlertRule]:
        """The rules `probe_native` may act on: those whose prompt SpringBoard can raise.

        `probe_native` and `__call__`'s own re-resolution of what it just tapped must filter
        identically — the re-resolution is only correct because it reproduces `probe_native`'s
        selection exactly, over the same `buttons` and `dismissed`. A single property, rather than
        `[r for r in self.rules if r.native]` spelled out at each call site, keeps the two from
        drifting: a rule this filter admits that `probe_native` itself excludes (or the reverse)
        would have `_resolve_alert_rule` return `None` right where a caller asserts it cannot,
        turning a merely failed step into an aborted scenario.

        In declaration order, like `tree_rules` above and for the identical reason (BE-0418 review
        finding): `probe_native`'s own state depends on `matching_alert_rule(native_rules,
        buttons)`, first-match-in-list-order, and `_AlertGuardGate._observe_native`
        (`waits/_alert_guard_gate.py`) calls `probe_native` directly, with no `dismissed` of its
        own to accumulate — so sorting this property would reach that unrelated consumer through
        `probe_native`'s own resolution, not merely through a direct read of the property itself.
        No currently declared native shape nests inside another (`notifications` grants "Allow" /
        "Don't Allow", `tracking` "Allow" / "Ask App Not to Track", `paste` "Allow Paste" / "Don't
        Allow Paste" — every one exactly two labels, none a subset of another), so
        `_resolve_alert_rule`'s subset test has nothing to misorder yet; the tree side's
        `_widest_first` exists only because `savePassword` already does nest today.
        """
        return [rule for rule in self.rules if rule.native]

    def probe_native(
        self,
        driver: base.Driver,
        reserved: base.Selector | None = None,
        *,
        dismissed: frozenset[frozenset[str]] = frozenset(),
    ) -> tuple[NativeAlertState, AlertEvent | None, list[str]]:
        """Query and, where possible, clear a system alert natively; report what happened.

        Reads BE-0316's SpringBoard query (`system_alert_labels`) to learn the alert's buttons, picks
        the button a `rules` entry names for the prompt it identifies, and taps it through BE-0316's
        `handle_system_alert`. The returned `AlertEvent` is set only for
        `"dismissed"`. `"absent"` is a deterministic no-*SpringBoard*-alert fact — but the native query
        only sees `springboard.alerts`, so a non-enumerable surface (an action sheet, a WKWebView
        dialog) reads as `"absent"` too, and only the mid-wait gate's debounced collapsed-tree proxy
        can notice it. `"unhandled"` means an alert is up but no rule identifies it, so nothing here
        can clear it.

        The third member carries the buttons this query actually read. `"unhandled"` is the state
        that needs them: BE-0402 left that alert on screen, so the labels are all a blocked step or
        wait has to name what stopped it, and they would otherwise be discarded here. Returned
        rather than re-queried at that moment, since a second cross-process query costs another
        round trip on the runner's single main thread and reopens the time-of-check/time-of-use
        window the dismiss-race branches below exist to close. `"absent"` carries them too, and
        they are not always empty there: the genuine empty enumeration below returns `[]`, but the
        time-of-check/time-of-use race that answers `"absent"` after tapping a *non-empty* read
        carries that original read forward — `__call__` (BE-0418) needs the distinction to tell "the
        surface has nothing on it" from "the one alert this round tried to tap raced away, which
        says nothing about the rest of the surface" (BE-0418 review finding).

        Args:
            reserved: A waiting `handleSystemAlert` step's own selector, when one is running
                (BE-0406). An alert it names is left untouched — see `selector_names_button`.
            dismissed: `identifying_labels` sets naming every rule `__call__` (BE-0418) has
                already dismissed this call, checked *before* tapping — not merely deduplicated
                after the fact — so a lingering fade never reaches a second real tap on the
                device. Keyed on a rule's own shape, not on the raw `buttons` read:
                `system_alert_labels()` enumerates every alert SpringBoard currently holds, so a
                still-fading alert's own button set changes the moment any other alert joins or
                leaves the surface — a `buttons`-keyed dedup would then read it as a genuinely new
                alert and tap it again, precisely the repeat tap this parameter exists to rule
                out. `identifying_labels` rather than `tap_label`, so a scenario's `choice`
                overriding a target's for the same prompt (BE-0177) — two rules sharing one
                alert's shape under different `tap_label`s — still counts as one already-answered
                alert rather than promoting the sibling to tap the opposite button on it. A shape
                that is a *subset* of one already named here counts as the same answered alert
                too, not only an exact match — two rules for the same prompt can nest this way
                (see `dismiss_from_tree_once`'s `exclude` for the concrete, tree-side example;
                `savePassword` never reaches this native-only parameter, since that prompt is
                tree-only). A later alert resolving to a shape that is neither a match nor a
                subset of one already named — including one sharing only the tapped label, like
                `notifications` and `tracking` both tapping `"Allow"` — still taps as usual, once
                it is no longer read alongside the one already named.
        """
        if base.Capability.HANDLE_SYSTEM_ALERT not in driver.capabilities():
            return "incapable", None, []
        buttons = driver.system_alert_labels()
        if not buttons:
            return "absent", None, []
        if reserved is not None and selector_names_button(reserved, buttons):
            # The step is waiting on this very alert and taps it on its own next read. Not
            # "absent": an alert *is* up, and "absent" is the one answer licensing an in-tree tap.
            return "reserved", None, list(buttons)
        # Filtered to `native_rules`, not the full `self.rules`: an in-tree-only shape's identifying
        # labels are ordinary vocabulary a real SpringBoard alert could coincidentally offer (the
        # 26.5 save sheet's shape is just "Save" / "Not Now"), and matching it here would answer
        # through `handle_system_alert` a prompt that surface can never actually reach — the same
        # undeclared-screen tap this proposal removes everywhere else (BE-0406).
        native_rules = self.native_rules
        if matching_alert_rule(native_rules, buttons) is None:
            return "unhandled", None, list(buttons)
        # A shape does identify the alert, but it may be one this call has already answered,
        # still matching because that alert's own dismiss animation outran `settle` — regardless
        # of what else the SpringBoard surface now enumerates alongside it. `_resolve_alert_rule`
        # retries among the shapes not yet answered in that case, so a real, not-yet-answered
        # alert enumerable alongside the fade (the stacked case this loop exists to clear) is
        # still found rather than declined along with it.
        rule = _resolve_alert_rule(native_rules, buttons, dismissed)
        if rule is None:
            # Every matching shape here is one this call already answered, and a repeat tap would
            # land on nothing (the alert genuinely gone) or on whatever the closing alert has by
            # then revealed underneath it — the same hazard `dismiss_from_tree_once`'s `exclude`
            # closes on the tree side.
            return "already_dismissed", None, list(buttons)
        label = rule.tap_label
        try:
            driver.handle_system_alert({"label": label}, _NATIVE_TAP_TIMEOUT)
        except base.ElementNotFound:
            # A time-of-check/time-of-use race: the alert vanished between the presence query and the
            # tap. It is no longer blocking, so treat it as absent rather than failing the step on a
            # benign, self-resolved race — a genuine channel error still propagates. Carries the
            # non-empty read forward rather than discarding it like the genuine empty enumeration
            # above: this only proves the one alert this round tried to tap is gone, not that the
            # rest of the surface is (BE-0418 review finding).
            return "absent", None, list(buttons)
        except base.AmbiguousSelector:
            # The other half of that race, and *not* the same answer: the alert is still up, now
            # offering the label twice. Reporting "absent" would say no system alert is showing,
            # which is the one thing licensing an in-tree tap (`_observe_native`'s `probed_absent`) —
            # and that tap, made under a live alert, is what XCUITest answers with its own default
            # button. "unhandled" is what this already is by definition: an alert is up but no rule
            # resolves, so it licenses nothing and is reported instead.
            return "unhandled", None, list(buttons)
        return "dismissed", AlertEvent(label=label), list(buttons)

    def dismiss_from_tree_once(
        self, driver: base.Driver, *, exclude: frozenset[frozenset[str]] = frozenset()
    ) -> tuple[
        AlertEvent | NotTappable | None, list[str], tuple[tuple[str | None, str | None], ...]
    ]:
        """Tap a scenario-named dismiss button visible in the driver's own tree, once.

        The one-shot twin of `_AlertGuardGate._dismiss_from_tree` (waits.py), for the end-of-step and
        `expect` retry. It exists for the same prompt that motivated the mid-wait one: iOS raises its
        "Save Password" alert *inside the app's process*, so `springboard.alerts` never sees it and
        only a tap in the tree can clear it — and measured, such an alert can arrive after a
        scenario's last wait has already returned, where only this path is left to meet it.

        Called once per round of the caller's own loop rather than per poll, so it carries none of
        the mid-wait version's per-showing bookkeeping (retap delay, tap ceiling, decline bound) —
        the round bound already serves that purpose (BE-0418). It matches the same narrow surface —
        an identifier-less labelled button on an alert one of the scenario's own in-tree-capable
        rules identifies, resolving uniquely.

        `exclude` names every shape already dismissed earlier in the same call (BE-0418), via
        `_resolve_alert_rule` — the same shared, post-match lookup `probe_native` uses, and for the
        same two reasons. First, a button that stays in the tree past its own dismiss animation
        would otherwise match again, and tapping it a second time risks landing on an application
        button the closing sheet has by then revealed; declining a repeat match closes that.
        Second, retrying among the shapes not yet excluded — rather than ending the search at the
        first, already-answered match — still finds a real, not-yet-answered alert enumerable
        alongside that fade (the stacked case this loop exists to clear), and keying on
        `identifying_labels` rather than `tap_label` means two rules sharing one alert's shape
        under different choices (a scenario's `choice` overriding a target's for the same prompt,
        BE-0177) are excluded together rather than one promoting the other to tap the opposite
        button on the alert this call already answered. A shape that is a *subset* of one already
        excluded here counts as excluded too, the same as on the native side (`probe_native`'s
        `dismissed`): `savePassword`'s `choice: deny` resolves to three rules, two of whose shapes
        nest (the web-form shape naming "Save Password", "Never for This Website", and "Not Now";
        the iOS 18.6 in-app shape naming only "Save Password" and "Not Now" — the 26.5 shape,
        "Save" and "Not Now", nests with neither, sharing only "Not Now" with the widest), both
        tapping the same button, and a fade that still enumerates the wider shape's buttons would
        otherwise match the narrower sibling and tap it a second time.

        Returns the `AlertEvent` for the button it tapped, `NotTappable` when the button resolved but
        the tap could not land — a scrim still covering it mid-animation, which the caller's own
        round-bounded loop retries — or None when nothing not-yet-excluded matched, the match was
        ambiguous, or the tap lost a race with the prompt closing itself; alongside it, the buttons
        this round's own tree read found, so a caller can resolve the same match again to learn
        which shape a returned `AlertEvent` belongs to; and this same read's `tree_signature`
        (`bajutsu.common.drivers.elements`), returned on every path rather than only a successful tap,
        so the caller can tell a later round's merely-still-enumerable shape from a genuinely
        unchanged screen (BE-0418 review finding) the same way `_AlertGuardGate._dismiss_from_tree`
        already does for the mid-wait path.
        """
        rules = self.tree_dedup_rules
        if not rules:
            return None, [], ()
        elements, buttons, signature = _read_tree(driver)
        rule = _resolve_alert_rule(rules, buttons, exclude)
        if rule is None:
            return None, buttons, signature
        label = rule.tap_label
        # The same uniqueness pre-check the mid-wait path applies: a bare `{"label": label}` selector
        # ignores traits, so an identified app button of the same name would make the tap ambiguous.
        if (
            sum(1 for el in elements if el["label"] == label and base.Trait.BUTTON in el["traits"])
            != 1
        ):
            return None, buttons, signature
        try:
            driver.tap({"label": label, "traits": [base.Trait.BUTTON]})
        except (base.ElementNotFound, base.AmbiguousSelector):
            # The prompt closed itself, or another button of that name appeared. Both benign here:
            # this is one opportunistic attempt on a step that has already failed, and the step's own
            # outcome still decides the verdict.
            return None, buttons, signature
        except base.ElementNotTappable:
            # Visible but not yet reachable — a scrim the sheet draws over its own button before
            # finishing its presentation animation. Not a reason to give up: the caller's own
            # round-bounded loop retries the same tap, mirroring the mid-wait path's own retry for
            # this exception (BE-0418).
            return NotTappable(label=label, shape=rule.identifying_labels), buttons, signature
        return AlertEvent(label=label), buttons, signature

    def __call__(
        self,
        driver: base.Driver,
        alerts: list[AlertEvent],
        *,
        settle: Callable[[], None],
    ) -> bool:
        """The end-of-step / expect retry: dismiss whatever blocks the screen, in bounded rounds.

        Loops up to `_GUARD_CALL_MAX_ROUNDS` times (BE-0418), appending every `AlertEvent` it clears
        to `alerts` — the same contract `_AlertGuardGate` already uses — rather than returning one,
        since a multi-round call can dismiss more than a single `AlertEvent | None` could report: a
        stacked SpringBoard prompt and an app-owned sheet underneath it can both clear in one call.
        Returns whether anything was cleared at all, which is what the caller's own one-shot retry
        gates on; `blocked_note` can still be non-empty on a `True` return — an earlier round can
        clear a stacked alert while a later one leaves a second unhandled, and the caller reports both
        facts rather than treating them as mutually exclusive. A dismissal the round bound later
        concludes never actually landed — the shape most recently tapped on either surface still
        reading back on the final round — has its own `AlertEvent` withdrawn from `alerts` at that
        point (the twin of `_AlertGuardGate._withdraw_tree_event`, `waits/_alert_guard_gate.py`), so
        the return value is computed from `alerts`' own net length change rather than tracked
        separately: an earlier, genuinely-cleared dismissal this call never revisits still counts,
        even when a *later* one on the same or the other surface is withdrawn (BE-0418 review
        finding) — leaving both `alerts` and a `True` return claiming a dismissal `blocked_note`
        says never happened would contradict the one fact a caller building a report or deciding
        whether to retry the step can check.

        `settle` runs after every round that acted on a live alert or found one it could not yet
        resolve — one that dismissed something, one that found a button not yet tappable, one that
        declined a still-fading alert it had already dismissed, one that read a shared-label
        collision no rule could uniquely match, one whose own tap raced away over a non-empty read
        (BE-0399), and one whose tree read matched nothing while a `NotTappable` diagnosis was still
        open — the caller's own `settle_after_alert_dismiss` bound to its
        `clock`/`transitions`/`cancelled`, including the round that exhausts the bound, so a
        caller reading the screen right after this call returns never reads one still
        mid-animation. `settle` is best-effort and bounded, though: a dismiss whose animation
        outlasts it can still be up, unchanged, on a later round's read, and neither path re-taps
        it. Both pass every shape (a rule's `identifying_labels`) they have already dismissed this
        call into `_resolve_alert_rule` (shared with `probe_native` and `dismiss_from_tree_once`),
        which retries among the shapes not yet dismissed before declining a match that lands back
        on one of them — the same alert still fading, even a round or two after a *different*
        alert cleared in between, rather than a second real tap on the device that risks landing
        on nothing or on whatever the closing alert has by then revealed underneath it. Keyed on a
        rule's own shape rather than the raw buttons a probe reads, since that read enumerates
        every alert the surface currently holds and so changes the moment a different alert joins
        or leaves it, which the alert already dismissed did not do — and rather than a rule's own
        `tap_label`, since two rules can share one alert's shape under different choices (a
        scenario's `choice` overriding a target's for the same prompt, BE-0177), and keying on the
        label alone would let one such rule's exclusion promote its sibling to tap the opposite
        button on the same alert. A later alert resolving to a genuinely different shape, once the
        earlier one is no longer part of what a probe reads, still taps as usual on either path —
        including one sharing only the tapped label (`notifications` and `tracking` both grant
        `"Allow"`). Two such shapes enumerable *together* instead share that label's count, so
        `matching_alert_rule`'s own per-label uniqueness check (`_functions.py`) matches neither
        until the read no longer holds both, and the round reports the surface as unhandled rather
        than guessing which one a shared label answers for.

        `note` likewise survives a round that resolves a *different* surface: a tree button stuck
        behind a scrim (`NotTappable`) stays named in the eventual `blocked_note` even if a later
        round goes on to dismiss an unrelated SpringBoard alert, rather than that unrelated success
        silently erasing a diagnosis the tree round still stands by.
        """
        note = ""
        # Not tracked as this call goes, unlike `note`: computed from `alerts`' own net length
        # change at the very end, once every withdrawal below has already had its say (BE-0418
        # review finding) — see this method's own docstring.
        alerts_start_len = len(alerts)
        # The tap label and shape of a tree round that could not land, naming and identifying which
        # stuck diagnosis is still open — not a bare bool: a later round tapping a *different*
        # in-tree prompt must not clear a still-open diagnosis for one that never became tappable
        # (BE-0418 review finding). Compared by shape, not label: two `in_tree` rules can share one
        # tap label under different choices (`savePassword`'s three shapes all tap "Not Now" under
        # `choice: deny`), so a later round dismissing a genuinely different prompt that happens to
        # share the stuck one's label must not read as "the same prompt finally landed" (BE-0418
        # review finding). The label still names the prompt in the eventual note.
        stuck_tree_label, stuck_tree_shape = None, None
        dismissed_native: frozenset[frozenset[str]] = frozenset()
        # Every shape this call has dismissed from the tree, each with the tap label and `AlertEvent`
        # *that* shape's own tap earned — not only the most recently tapped one — so the
        # lingering-fade check below can diagnose whichever dismissed shape it actually finds still
        # enumerable, rather than only the last one tapped (BE-0418 review finding): an earlier
        # sheet this call tapped that never closed would otherwise report nothing at all once a
        # later, different sheet is tapped and clears, since only the latter's own shape was ever
        # compared against `_bound_exhaustion_note`.
        dismissed_tree_info: dict[frozenset[str], tuple[str, AlertEvent | None]] = {}
        # The shape and tap label of the native rule most recently tapped fresh, for
        # `_bound_exhaustion_note` to check against the final round's own read (BE-0418 review
        # finding) — the label is carried alongside the shape so that check can name it without a
        # second lookup. `native_dismiss_event` is the exact `AlertEvent` that tap appended to
        # `alerts`, held by identity (not equality — two dismissals of the same shape compare equal)
        # so the withdrawal below can remove precisely this one and never an earlier, genuine
        # dismissal of the same shape (BE-0418 review finding).
        native_dismiss_shape, native_dismiss_label, native_dismiss_event = None, None, None
        # The shape a race branch round most recently raced on fresh, for the race branch's own
        # `_raced_exhaustion_note` call to require corroboration against (BE-0418 review finding):
        # a fresh resolution's own containment check is tautological on a race round alone, since
        # `buttons` there is the very read that resolved the rule before its tap raced away — only
        # the identical shape racing on more than one round is real repetition rather than a
        # coincidence of pre-tap timing. Updated only by the race branch itself, so an intervening
        # round of a different kind neither corroborates nor clears it.
        raced_native_shape: frozenset[str] | None = None
        # The tree read `dismiss_from_tree_once` took the round it last tapped fresh — the same
        # signature `_AlertGuardGate._dismiss_from_tree` already compares a later poll's own read
        # against (`waits/_alert_guard_gate.py`). A later round's shape still being enumerable in
        # `tree_buttons` is not by itself evidence the tap never landed: an app-owned button revealed
        # once the sheet actually closed can carry the identical label. Comparing full tree identity
        # rather than just this shape's labels catches that case (BE-0418 review finding).
        tree_dismiss_signature: tuple[tuple[str | None, str | None], ...] | None = None
        # The round index the tree was last read at, so the post-loop check below can tell a stale
        # cached read from one a settle since then could plausibly have moved past (BE-0418 review
        # finding): every round that continues past this one calls settle() on its way there, so a
        # later round index here means at least one settle ran since; without one, nothing can have
        # advanced the screen, and a round whose own tree read already re-tested this exact evidence
        # and found it false would have that same fresh read reproduce the same false right back — a
        # redundant `driver.query()` this comparison lets the post-loop check skip.
        tree_read_round: int | None = None
        for round_index in range(_GUARD_CALL_MAX_ROUNDS):
            state, event, buttons = self.probe_native(driver, dismissed=dismissed_native)
            if state == "dismissed":
                assert event is not None  # "dismissed" always carries its event (see probe_native)
                alerts.append(event)  # never a repeat: probe_native declined an already-seen key
                # Re-resolves which shape `probe_native` just tapped, over the same `buttons` it
                # already read this round, via the identical shared lookup — rather than add a
                # return member only this one caller needs — so this always agrees with what
                # `probe_native` actually acted on, including when the plain first match was
                # itself already answered and the tap landed on its not-yet-answered fallback.
                rule = _resolve_alert_rule(self.native_rules, buttons, dismissed_native)
                assert rule is not None  # the round that just dismissed this alert matched it
                dismissed_native |= {rule.identifying_labels}
                # A fresh tap, of any shape, is what `_bound_exhaustion_note` checks on the final
                # round: whatever an earlier shape's own fade was doing says nothing about this one.
                native_dismiss_shape, native_dismiss_label, native_dismiss_event = (
                    rule.identifying_labels,
                    rule.tap_label,
                    event,
                )
                if stuck_tree_label is None:
                    # Not an unconditional clear: `buttons` is the whole SpringBoard enumeration, so
                    # a co-present alert no rule identifies can sit right alongside the one this
                    # round just dismissed, and dismissing one alert does not mean the rest of the
                    # surface is clear. Computing the leftover here, exactly as `already_dismissed`
                    # and `"unhandled"` below do, is what keeps that stranger from being silently
                    # dropped when this round happens to be the one that exhausts the bound — a
                    # later round re-probing fresh buttons would otherwise self-correct, but there is
                    # no later round on the last one (BE-0418 review finding). Crediting every rule
                    # `identified_alert_rules` finds on this read, not only the one `dismissed_native`
                    # already names: a fourth declared, live prompt queued behind the three this call
                    # already tapped is still a rule *did* identify, and the final round has no
                    # successor of its own to self-correct a single-rule credit's misdiagnosis
                    # (BE-0418 review finding). A queued-but-not-yet-tapped fourth prompt is exactly
                    # what that credit subtracts out of the leftover, though, so a bare `""` fallback
                    # would go silent on it in turn — `_raced_exhaustion_note` resolves a rule fresh
                    # against this round's own read the same way the race and `"unhandled"` branches
                    # already do, naming that fourth prompt instead of dropping it (BE-0418 review
                    # finding). No fallback of its own, unlike those two siblings: `dismissed_native`
                    # already includes the shape this round just tapped, so `_resolve_alert_rule`'s
                    # own dismissed-exclusion retry can never resolve back to it — a `None` result
                    # here means nothing else is queued, and `native_dismiss_shape` would otherwise
                    # trivially satisfy `_bound_exhaustion_note`'s check against this very round's own
                    # pre-tap read, naming the alert that was just confirmed tapped as still uncleared
                    # (BE-0418 review finding).
                    note = _fresh_dismiss_leftover_note(
                        self.native_rules, buttons, dismissed_native, round_index
                    )
                settle()
                continue
            if state == "already_dismissed":
                # The same lingering alert `dismissed` already named — probe_native declined the
                # tap outright, so nothing was actuated this round. Still settle: this round just
                # enumerated a live alert mid-fade, both call sites read the screen the instant
                # this returns, and letting the fade run down here is what lets a later round
                # reach "absent" — and any app-owned sheet stacked underneath — instead of
                # spending the whole bound re-reading the same alert.
                #
                if stuck_tree_label is None:
                    # `buttons` is the whole enumerable SpringBoard surface, not this one rule's
                    # own set, so declining a re-tap here does not mean nothing else is up: a
                    # second, still-live alert no rule identifies can sit right alongside it. Every
                    # rule `identified_alert_rules` finds on this read is accounted for, not only
                    # the shapes already in `dismissed_native` (BE-0418 review finding) — a wider
                    # declared sibling nesting with an already-dismissed shape reaches this branch
                    # too, and crediting only the tapped shapes would report its own extra label as
                    # an alert no rule identifies when a rule does identify it — exactly the check
                    # the "dismissed" branch above and the "unhandled" branch below both make too,
                    # for the identical reason. A leftover takes precedence over the exhaustion
                    # note: something else is demonstrably
                    # still up regardless of whether this round's own tap ever landed.
                    # `_bound_exhaustion_note` checks `native_dismiss_shape` directly against this
                    # round's own read — see its own docstring for why that must be a containment
                    # check against the final round alone, not a streak counted since the tap
                    # (BE-0418 review finding).
                    note, native_dismiss_event = _already_dismissed_note(
                        alerts,
                        self.native_rules,
                        native_dismiss_shape,
                        native_dismiss_label,
                        native_dismiss_event,
                        buttons,
                        dismissed_native,
                        round_index,
                    )
                settle()
                continue
            if state == "absent":
                # No *SpringBoard* alert, which is both the licence to tap an app element (XCUITest
                # answers an interrupting out-of-process alert before synthesizing any interaction)
                # and the case where an app-owned prompt is the remaining explanation for the block.
                #
                # A genuinely empty read also retracts every shape `dismissed_native` is still
                # holding: that record exists only to keep a still-fading alert from a second real
                # tap, and an empty enumeration just proved the surface holds nothing at all —
                # fading or otherwise. Carrying it forward past this point would decline a *later*
                # genuine re-raise of the same shape as though it were the earlier occurrence's own
                # stale fade, tapping nothing (BE-0418 review finding). Gated on `buttons` itself,
                # not merely on `state == "absent"`: the time-of-check/time-of-use race below also
                # answers "absent" after tapping a *non-empty* read, and that only proves the one
                # alert this round tried to tap is gone, not that the rest of the surface — an
                # earlier round's own still-fading dismissal included — is (BE-0418 review finding).
                if not buttons:
                    # And the shape `_bound_exhaustion_note` keys on, for the same reason: this read
                    # is proof the tap that recorded it landed, so a later round must not name it as
                    # one that never cleared (BE-0418 review finding). `native_dismiss_event` goes
                    # with it: every withdrawal path takes the event, not the shape, and this read is
                    # equally proof that event's own tap landed — carrying it forward would let a
                    # later branch withdraw an already-landed dismissal from `alerts` on some future
                    # exhaustion note this round cannot foresee (BE-0418 review finding).
                    # And the shape a race round would corroborate against: an empty enumeration is
                    # positive proof the surface was clear, so an earlier race on it is no longer
                    # repetition a later one can lean on (BE-0418 review finding).
                    (
                        dismissed_native,
                        native_dismiss_shape,
                        native_dismiss_label,
                        native_dismiss_event,
                        raced_native_shape,
                    ) = (frozenset(), None, None, None, None)
                # The one alert this round's own probe just proved gone (the race above) is not in
                # `dismissed_native` either — nothing was actually dismissed — so a leftover
                # computed against `dismissed_native` alone still lets that alert's own labels
                # survive into it, reporting an alert a rule *did* identify as unhandled (BE-0418
                # review finding). Crediting every rule `buttons` still identifies, not only the one
                # `probe_native` raced against: a second declared prompt co-present on the same read
                # is not the raced rule's own shape, so a single-rule credit still lets its labels
                # survive into the leftover and be named as an alert nothing identifies, when a rule
                # identified it and only the tap failed for a *different* shape (BE-0418 review
                # finding) — the same fix `_observe_native`'s own `raced` branch
                # (`waits/_alert_guard_gate.py`) already applies via `identified_alert_rules`.
                leftover_dismissed_native = dismissed_native | {
                    rule.identifying_labels
                    for rule in identified_alert_rules(self.native_rules, buttons)
                }
                if not buttons:
                    # Only a genuinely empty read licenses the tree tap at all: XCUITest answers an
                    # interrupting out-of-process alert with its own default button before
                    # synthesizing any interaction, so tapping the tree while the time-of-check/
                    # time-of-use race above has left a live SpringBoard alert on screen is
                    # unlicensed, not merely undiagnosed (BE-0399, BE-0418 review finding).
                    tree_result, tree_buttons, tree_read_signature = self.dismiss_from_tree_once(
                        driver, exclude=frozenset(dismissed_tree_info)
                    )
                    if isinstance(tree_result, AlertEvent):
                        alerts.append(tree_result)  # excluded once cleared, never a repeat report
                        # Re-resolves which shape was just tapped, over the same `buttons` this
                        # round's own tree read already found, the same way the native branch above
                        # does — and over the same widest-first ordering `dismiss_from_tree_once`
                        # itself just used, so this always agrees with what it actually matched.
                        rule = _resolve_alert_rule(
                            self.tree_dedup_rules, tree_buttons, frozenset(dismissed_tree_info)
                        )
                        assert rule is not None  # the round that dismissed this alert matched it
                        dismissed_tree_info[rule.identifying_labels] = (
                            rule.tap_label,
                            tree_result,
                        )
                        # A fresh tap, of any shape, is what `_first_lingering_tree_shape` and the
                        # post-loop check can diagnose on the final round (mirrors the native branch
                        # above). `tree_dismiss_signature` is this round's own pre-tap read, so a
                        # later round's exhaustion check can tell whether the screen has changed
                        # since — not merely whether this shape's labels are still somewhere in it
                        # (BE-0418 review finding).
                        tree_dismiss_signature = tree_read_signature
                        # No stuck diagnosis means whatever `note` holds is stale regardless. A
                        # stuck shape that nests with this round's own dismissal either way clears
                        # too — not only when the stuck shape is the narrower one: recording
                        # `rule.identifying_labels` in `dismissed_tree_info` two lines above feeds
                        # `_resolve_alert_rule`'s own *bidirectional* `_nests_with_a_dismissed_shape`
                        # test, so a stuck shape nesting either way can never match again this call
                        # either, and the two must agree (a one-directional test here left a stuck
                        # wider shape clearing only when the *dismissed* shape was the wider one —
                        # a `savePassword`-style stuck read of the full "Save Password", "Never for
                        # This Website", "Not Now" shape, later dismissed as the narrower two-button
                        # reading of the very same alert, stayed marked stuck forever even though the
                        # rules the two must agree with had already retired it, BE-0418 review
                        # finding). A shape genuinely unrelated by containment survives, even sharing
                        # the stuck one's own tap label (`savePassword`'s three shapes all tap
                        # "Not Now" under `choice: deny`): this round dismissed a genuinely different
                        # in-tree prompt, which says nothing about whether the stuck one is still
                        # stuck.
                        if stuck_tree_shape is None or (
                            stuck_tree_shape <= rule.identifying_labels
                            or rule.identifying_labels <= stuck_tree_shape
                        ):
                            # Not `_leftover_note`: `buttons` is `[]` here, inside `if not buttons:`
                            # above, so that call would only ever reduce to its own fallback (BE-0418
                            # review finding) — spelled out directly instead.
                            note = ""
                            stuck_tree_label = stuck_tree_shape = None
                        settle()
                        continue
                    # Reached only when `tree_result` was not a tap (the branch just above always
                    # continues): only a read that went on to *test* the exhaustion evidence retires
                    # the post-loop query below — a tap moved the screen and settled after this read,
                    # so nothing has checked yet whether that sheet actually closed (BE-0418 review
                    # finding).
                    tree_read_round = round_index
                    if isinstance(tree_result, NotTappable):
                        note, stuck_tree_label, stuck_tree_shape = (
                            uncleared_prompt_note(tree_result.label),
                            tree_result.label,
                            tree_result.shape,
                        )
                        settle()
                        continue
                    # The tree twin of the native retraction above, but keyed on a shape rather than
                    # the whole surface: a dismissed shape no longer enumerable anywhere in this
                    # read is gone, not fading, so keeping it in `exclude` could only ever wrongly
                    # block a *different*, not-yet-tapped rule whose own shape happens to nest
                    # inside it (BE-0418 review finding). The shape leaves `exclude` along with the
                    # record, so a later round whose own read holds those labels again would tap it
                    # afresh — the same treatment the native retraction gives a genuine re-raise,
                    # and out of reach at today's bound, which leaves no round for that re-tap.
                    # Keyed on the tree actually having moved since the dismiss, not merely on
                    # whether this read's own labels still overlap it: retracting unconditionally
                    # only ever removes a shape the `any()` check below would already have excluded
                    # itself, so it never actually changes this round's own outcome, only
                    # `exclude`'s contents for a round that may never come (BE-0418 review finding).
                    if tree_read_signature != tree_dismiss_signature:
                        dismissed_tree_info = {
                            shape: info
                            for shape, info in dismissed_tree_info.items()
                            if shape <= set(tree_buttons)
                        }
                    # A shape this call already cleared, still enumerable among this round's own
                    # tree read, is the in-tree twin of `probe_native`'s "already_dismissed": the
                    # sheet's own fade outlasted `settle`, so settle again and give a sheet stacked
                    # underneath it another round to be presented, rather than ending the call on a
                    # lingering fade this loop exists to see past. Not gated on the tree being
                    # otherwise unchanged since the tap: a sheet that accepts a tap without closing —
                    # a validation error re-presenting it, say — changes the tree by construction (at
                    # minimum, the new error row), so requiring an unchanged signature ruled out
                    # exactly the case this branch exists to catch (BE-0418 review finding). The false
                    # positive an unchanged-signature requirement would have protected against — the
                    # sheet genuinely closed and revealed an app screen whose own ordinary buttons
                    # happen to carry the same labels (`savePassword`'s 26.5 shape, "Save" / "Not
                    # Now", is exactly this) — never risks the second, unlicensed tap the mid-wait
                    # gate's own identical ambiguity would, since `exclude` already keeps this call
                    # from tapping the dismissed shape again regardless of which read this branch
                    # takes. It costs more than an imprecise note, though: `_lingering_tree_note`
                    # withdraws that shape's own `AlertEvent` on the same containment check, so a
                    # sheet that really did close is reported as never dismissed — and when it was
                    # this call's only dismissal, the return below flips to False and the caller
                    # skips its one-shot retry against a screen that had in fact moved on
                    # (`docs/architecture.md` records the same trade-off).
                    lingering_shape = (
                        _first_lingering_tree_shape(dismissed_tree_info, tree_buttons)
                        if tree_dismiss_signature is not None
                        else None
                    )
                    if lingering_shape is not None:
                        # The tree twin of the native diagnosis above (BE-0418 review finding):
                        # `dismiss_from_tree_once` reported a tap as landed, but a sheet that
                        # accepts a tap without closing (a validation error re-presenting it, say)
                        # leaves this call unable to ever act on it again (`exclude`), and otherwise
                        # the step would fail on the bare `element not found` BE-0402 exists to
                        # prevent. Diagnosed against `lingering_shape` — whichever dismissed shape
                        # the check above actually found still enumerable, not only the most
                        # recently tapped one — so an earlier sheet this call tapped that never
                        # closed is still named once a later, different sheet is tapped and clears
                        # (BE-0418 review finding). Not wrapped in `_leftover_note`, unlike the
                        # native side's own call on `_bound_exhaustion_note` above: `buttons` is
                        # provably `[]` here, inside `if not buttons:`, so that wrapper would only
                        # ever reduce to this very fallback — called directly instead.
                        if stuck_tree_label is None:
                            note = _lingering_tree_note(
                                alerts,
                                dismissed_tree_info,
                                lingering_shape,
                                tree_buttons,
                                round_index,
                            )
                        settle()
                        continue
                    # An open `NotTappable` diagnosis is itself something this call still has to
                    # act on, so an ambiguous read here — nothing matched, and no already-excluded
                    # shape is lingering either — must not end the call while a round remains for
                    # the scrim to lift: the exact bounded retry Unit 2 exists for (BE-0418 review
                    # finding). The same reasoning as the lingering-exclusion branch above applies:
                    # a tree read that matches nothing is not evidence the stuck sheet resolved,
                    # only that this read did not catch it.
                    if stuck_tree_label is not None:
                        settle()
                        continue
                    # Otherwise this round's tree read may simply have caught a still-animating
                    # screen mid-transition rather than a genuinely clear one, so a tree diagnosis
                    # is left as an earlier round's read left it rather than erased on this round's
                    # own account. Reaching here at all means no shape this call has already
                    # dismissed is enumerable anywhere in this read either — the `any()` above ruled
                    # that out regardless of whether the screen changed since the tap — so there is
                    # nothing inherited from an earlier dismiss left to diagnose (BE-0418 review
                    # finding). Not `_leftover_note`: `buttons` is `[]` here too, inside
                    # `if not buttons:` above, so that call would only ever reduce to its own
                    # fallback (BE-0418 review finding).
                    note = ""
                    break
                # A non-empty read here is the time-of-check/time-of-use race, not a genuinely
                # clear surface, so the tree is left alone entirely this round rather than tapped
                # under a live SpringBoard alert (BE-0399, BE-0418 review finding). Whatever this
                # round did not already answer is reported — unless a tree diagnosis is still open,
                # in which case it is left as an earlier round's read left it, the same deference
                # every sibling branch in this loop gives it (BE-0418 review finding). Falls back to
                # `_bound_exhaustion_note`, exactly like `already_dismissed` and `"unhandled"` over
                # the identical evidence: a call whose *final* round is a race must still be able to
                # name a native alert this call tapped and never saw clear, not only a call whose
                # final round happens to be one of those two other kinds (BE-0418 review finding).
                # Always worth another round, unlike `"unhandled"` below: the raced rule's own shape
                # is in `buttons` by construction — `probe_native` only reaches this race after
                # `matching_alert_rule` already matched it, which itself never returns a rule ruled
                # out by its own `excluded_labels` — so `_native_round_worth_another_try` can never
                # end the call here.
                if stuck_tree_label is None:
                    # Prefers the rule that raced *this* round over a possibly-stale
                    # `native_dismiss_shape`, the same way "unhandled" below prefers its own
                    # `resolved_rule` (BE-0418 review finding) — see `_raced_exhaustion_note`.
                    # `require_corroboration` gates the fresh resolution's own exhaustion note on
                    # having raced on a *previous* round too, not only this one (BE-0418 review
                    # finding) — see that function's own docstring for why a race round's fresh
                    # containment check is tautological without it.
                    note, native_dismiss_event = _raced_or_unhandled_note(
                        alerts,
                        self.native_rules,
                        buttons,
                        dismissed_native,
                        round_index,
                        native_dismiss_shape,
                        native_dismiss_label,
                        native_dismiss_event,
                        leftover_dismissed_native,
                        require_corroboration=raced_native_shape,
                    )
                    # Re-resolves the same rule the call above just did, so the *next* round's own
                    # corroboration check knows what raced this one (BE-0418 review finding) —
                    # rather than add a return member only this one caller needs, the same choice
                    # the "dismissed" branch's own re-resolution above makes.
                    raced_rule = _resolve_alert_rule(self.native_rules, buttons, dismissed_native)
                    raced_native_shape = raced_rule.identifying_labels if raced_rule else None
                settle()
                continue
            if state == "unhandled":
                # An alert is up that no rule identifies — but `buttons` is the whole SpringBoard
                # enumeration, not a fresh, self-contained read, and can still hold a label this
                # call already answered: BE-0418's own flagship stacked pair, `notifications` and
                # `tracking`, both grant `"Allow"`, so a round reading one's still-fading buttons
                # alongside the other's now-live ones fails `matching_alert_rule`'s per-label
                # uniqueness check for either and lands here rather than in `already_dismissed`.
                # Filtering the already-answered labels out of the note (the same computation
                # `already_dismissed` above makes) keeps the note from re-naming an alert this call
                # already cleared. A read that leaves nothing over still falls back to
                # `_bound_exhaustion_note`, exactly like `already_dismissed` above: "unhandled" here
                # is not only "no rule identifies this at all" — a per-label uniqueness collision on
                # a read that *does* hold the tapped shape lands here too (the flagship pair just
                # above is exactly that), and which of the two branches a torn-down alert's read
                # lands in from one round to the next is not something the caller controls, so the
                # diagnosis must not depend on it (review finding).
                #
                # `probe_native` reaches "unhandled" a second way, too: a matched rule whose tap
                # found the label twice (`AmbiguousSelector`, "the other half of that race" per its
                # own docstring) — not a genuinely unidentified alert. Crediting every rule
                # `identified_alert_rules` finds on this read, the same way the race branch above
                # resolves its own `leftover_dismissed_native` and `_AlertGuardGate._observe_native`'s
                # own `"unhandled"` branch (`waits/_alert_guard_gate.py`) credit theirs, not only the
                # single rule `_resolve_alert_rule` would pick: a second declared prompt co-present
                # with the ambiguously-tapped one is a rule *did* identify, and a single-rule credit
                # would still let its labels survive into the leftover and be named as an alert no
                # rule identifies (BE-0418 review finding; see `uncleared_prompt_note`'s docstring).
                # The exhaustion fallback still prefers whichever rule this round's own read resolves
                # over a possibly-stale `native_dismiss_shape` from an earlier tap: leftover empty
                # here means nothing else is on the surface, so the fallback must not go silent on
                # the final round just because this call never *tapped* anything (BE-0418 review
                # finding).
                if stuck_tree_label is None:
                    # The ambiguous tap's own rule aside — see `_raced_or_unhandled_note`'s own
                    # docstring for the shared exhaustion/withdrawal logic (BE-0418 review finding).
                    note, native_dismiss_event = _raced_or_unhandled_note(
                        alerts,
                        self.native_rules,
                        buttons,
                        dismissed_native,
                        round_index,
                        native_dismiss_shape,
                        native_dismiss_label,
                        native_dismiss_event,
                        dismissed_native
                        | {
                            rule.identifying_labels
                            for rule in identified_alert_rules(self.native_rules, buttons)
                        },
                    )
                if stuck_tree_label is None and not _native_round_worth_another_try(
                    dismissed_native, buttons, self.native_rules
                ):
                    # Breaking here keeps this round's own diagnosis and costs nothing this call
                    # could still change — unless an open `NotTappable` diagnosis is the one still
                    # in flight, in which case this round's own note computation above was already
                    # skipped, so there is nothing of this round's own to lose, and a round remains
                    # for Unit 2's landing-race retry to meet the scrim lifting (BE-0418 review
                    # finding).
                    break
                settle()
                continue
            # "reserved" and "incapable" clear the note instead: neither is evidence of anything
            # blocking the screen that this call could have acted on. Both are also driver facts
            # fixed for the whole call (a capability, or a step's own reserved selector — never
            # passed here, so "reserved" cannot actually occur from this call site), so neither can
            # follow a round that already found something concerning.
            note = ""
            break
        # The lingering-fade branch inside `"absent"` above is the only place that can report a
        # tapped tree shape still covering the screen, so a call whose *final* round takes any
        # other path — a native alert dismissed on the very last round, say — never runs it, even
        # though the evidence that branch would have used survives right here to check: the tree
        # twin of the native diagnosis's own reach across both `already_dismissed` and `"unhandled"`.
        # See `_final_tree_check`'s own docstring for the fresh-read and `tree_read_round` reasoning.
        note = _final_tree_check(
            driver,
            alerts,
            note,
            dismissed_tree_info,
            tree_read_round=tree_read_round,
            round_index=round_index,
        )
        self.blocked_note = note
        # Not tracked incrementally: a dismissal any of the withdrawals above took back must not
        # count, while an earlier, genuinely-cleared one this call never revisited still does, even
        # when a *later* dismissal on the same or the other surface is the one withdrawn (BE-0418
        # review finding) — see this method's own docstring.
        return len(alerts) > alerts_start_len
