"""Everything one declared target contributes to a multi-target run (BE-0428)."""

from __future__ import annotations

from dataclasses import dataclass, field

from bajutsu.common.assertions import EvalContext
from bajutsu.common.drivers import base
from bajutsu.common.drivers.webview import DomSource
from bajutsu.common.evidence import EvidenceSink
from bajutsu.common.evidence.network import TransitionSource, _no_transitions
from bajutsu.common.scenario import Interrupt

from ._functions import _no_network
from ._shared import NetworkSource, RelaunchFn
from .alert_guard_config import AlertGuardConfig
from .device_control import DeviceControl
from .mailbox_reader import MailboxReader


@dataclass(frozen=True)
class TargetRuntime:
    """One declared target's live driver and everything the step loop reads alongside it.

    Read together, these are exactly what the pipeline binds from one target's one lease today and
    hands `run_scenario` as flat keyword arguments. A scenario declaring several targets needs one
    such bundle per declared name instead of one set for the whole run, so a step routed to the
    second target reads *its* network source, *its* evidence sink, and *its* WebView bridge rather
    than the first target's.

    `caps` is the one field that never reaches `run_scenario`: the capability preflight that
    consults it runs before the scenario is dispatched at all. It travels here anyway because it is
    resolved per target the same way the rest are, and a separate carrier for one field would only
    give the two a chance to disagree about which target they describe.

    Carries no `launchEnv`: the touch-marker policy it would drive (`_hides_touch_markers`) is a
    property of the whole visual-capture group, not of one target, so `_evaluate_expect` reads it
    from the *primary*'s own launch env once, exactly like `control`/`channel`/`cancelled` — see
    that function's docstring. A per-target field here would sit unread the way it did before this
    note existed (BE-0428 review), which is worse than not carrying it at all.
    """

    driver: base.Driver
    sink: EvidenceSink
    alert_guard: AlertGuardConfig | None = None
    network: NetworkSource = _no_network
    relaunch: RelaunchFn | None = None
    control: DeviceControl | None = None
    ctx: EvalContext | None = None
    mailbox: MailboxReader | None = None
    webview_bridge: DomSource | None = None
    transitions: TransitionSource = _no_transitions
    interrupts: list[Interrupt] = field(default_factory=list)
    locale: str | None = None
    capture: list[str] = field(default_factory=list)
    # The in-app control channel (BE-0365). Typed loosely because the collector it names lives in
    # the evidence layer, which imports back from here; the step loop only ever hands it on.
    channel: object | None = None
    caps: frozenset[str] | None = None
