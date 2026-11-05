"""Name what blocks a guarded wait, from the backend's own alert resolution (BE-0461)."""

from __future__ import annotations

from dataclasses import dataclass, field

from bajutsu.common.drivers import base
from bajutsu.common.drivers.elements import shows_app_ui, tree_buttons
from bajutsu.common.orchestrator.types import (
    AlertGuardConfig,
    Clock,
    alert_block_note,
    collapsed_tree_note,
    uncleared_prompt_note,
)

from .resolving_reads import ResolvingReads

# Consecutive collapsed polls before the hedged note: a hair above `_SETTLE_POLLS`, so a transient
# collapsed frame does not read as a blocked screen (BE-0269, BE-0402).
_GUARD_DEBOUNCE_POLLS = 3


@dataclass
class _AlertGuardGate:
    """The mid-wait guard: it keeps the note a timeout appends, and never acts (BE-0402, BE-0461).

    It never decides the wait either (prime directive 1). On a backend advertising `RESOLVE_ALERTS`
    the backend answers the alerts inside the wait's own reads (`reads`), and this reads what each
    read recorded. Two records name a block outright and outrank the collapsed-tree proxy's
    label-less note, which never clears them: *unidentified* (a SpringBoard alert no rule names,
    `alert_block_note`) and *gave up* (a rule's button pressed without clearing it,
    `uncleared_prompt_note`). A note holds until the screen is shown clear of what it names, never on
    a remembered answer.

    On any other backend `reads` is None and only the collapsed-tree proxy runs: a prompt that
    collapses the app's tree to bare content earns the hedged note past a short debounce.
    """

    clock: Clock
    guard: AlertGuardConfig
    reads: ResolvingReads | None = None
    # The note a timeout appends; empty whenever nothing is known to block the screen.
    blocked_note: str = field(default="", init=False)
    # Set once the tree has stayed collapsed for `frozen_screen_timeout` on a screen a read just
    # ruled a SpringBoard alert out of, so a `for` / `screenChanged` wait stops on it.
    stuck_note: str = field(default="", init=False)
    _unidentified: list[str] | None = field(default=None, init=False)
    _gave_up: base.GaveUpAlert | None = field(default=None, init=False)
    # Whether the `handleSystemAlert` step running this gate last read its own alert up: that alert
    # covers the app, so the proxy must not record a block against a prompt about to be answered.
    _own_alert_up: bool = field(default=False, init=False)
    _proxy_note: str = field(default="", init=False)
    _collapsed_polls: int = field(default=0, init=False)
    _collapsed_since: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.reads is not None:
            # Reads made before this wait (an earlier step's, a handler's) are not this wait's to
            # explain.
            self.reads.take()

    def observe(self, elements: list[base.Element]) -> None:
        """Take this poll's resolving reads, and the tree the last of them returned."""
        polled = self.reads.take() if self.reads is not None else []
        for read in polled:
            self.note_records(read.drained)
        checked = bool(polled) and polled[-1].springboard_checked
        if checked:
            # This request saw no SpringBoard alert, so a native note it names is stale.
            self._unidentified = None
            if self._gave_up is not None and self._gave_up.surface == "native":
                self._gave_up = None
        if (
            self._gave_up is not None
            and self._gave_up.surface == "inTree"
            and shows_app_ui(elements)
            and not self._gave_up_shape_shown(elements, self._gave_up)
        ):
            self._gave_up = None
        if any(read.drained.tapped for read in polled):
            # An answer accounts for the collapse it ends.
            self._reset_collapse()
        self._observe_collapsed(elements, checked=checked)
        self._refresh_note()

    def note_records(self, drained: base.DrainedInterruptions) -> None:
        """Keep the blocked-screen facts *drained* carries; the latest of each kind wins."""
        if drained.unidentified:
            self._unidentified = drained.unidentified[-1]
        if drained.gave_up:
            self._gave_up = drained.gave_up[-1]
        self._refresh_note()

    def note_step_read(self, seen: list[str], *, own: bool) -> None:
        """A `handleSystemAlert` step's own read of the SpringBoard alert (BE-0461 Unit 4).

        Args:
            seen: The buttons it read.
            own: Whether the alert it read is the step's own — its selector names a button and the
                title does not rule the prompt out.
        """
        self._own_alert_up = own
        # The step's own query is a fresh SpringBoard read too: once it no longer shows the buttons a
        # native note names, that alert is gone.
        if self._unidentified is not None and not set(self._unidentified) <= set(seen):
            self._unidentified = None
        if (
            self._gave_up is not None
            and self._gave_up.surface == "native"
            and not set(self._gave_up.identify) <= set(seen)
        ):
            self._gave_up = None
        self._refresh_note()

    def _gave_up_shape_shown(self, elements: list[base.Element], gave_up: base.GaveUpAlert) -> bool:
        """Whether a declared in-tree shape nesting with the given-up one is still on screen.

        Nesting either way: a sheet re-presented with only a narrower sibling's buttons is the same
        sheet, so its note stays (BE-0418).
        """
        buttons = set(tree_buttons(elements))
        shape = frozenset(gave_up.identify)
        return any(
            rule.identifying_labels <= buttons
            and (rule.identifying_labels <= shape or shape <= rule.identifying_labels)
            for rule in self.guard.tree_dedup_rules
        )

    def _observe_collapsed(self, elements: list[base.Element], *, checked: bool) -> None:
        """The collapsed-tree proxy, idle while a record or the step's own alert explains the screen."""
        if self._unidentified is not None or self._gave_up is not None or self._own_alert_up:
            self._reset_collapse()
            self._proxy_note = ""
            return
        if shows_app_ui(elements):
            self._reset_collapse()
            self._proxy_note = ""
            return
        self._collapsed_polls += 1
        bound = self.guard.frozen_screen_timeout
        if self.reads is not None and self.guard.tree_rules and bound > 0:
            # Only for a scenario declaring an in-app prompt such as `savePassword`, the one kind
            # measured to leave the screen collapsed and unanswerable, and only on a read that just
            # ruled a SpringBoard alert out: a remembered "no alert" could predate one. A backend with
            # no such check cannot rule an alert out at all, so it never stops early.
            now = self.clock.now()
            if self._collapsed_since is None:
                self._collapsed_since = now
            elif checked and now - self._collapsed_since >= bound:
                self.stuck_note = collapsed_tree_note(now - self._collapsed_since)
        if self._collapsed_polls >= _GUARD_DEBOUNCE_POLLS:
            self._proxy_note = alert_block_note([])

    def _reset_collapse(self) -> None:
        self._collapsed_polls = 0
        self._collapsed_since = None

    def _refresh_note(self) -> None:
        if self._gave_up is not None:
            self.blocked_note = uncleared_prompt_note(self._gave_up.tap)
        elif self._unidentified is not None:
            self.blocked_note = alert_block_note(self._unidentified)
        else:
            self.blocked_note = self._proxy_note
