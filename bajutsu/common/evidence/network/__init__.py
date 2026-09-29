"""Network observation — the exchange model and the in-process collector.

How traffic is observed (DESIGN: network): a Simulator app runs as a host process
and shares the Mac's loopback, so the app POSTs each request/response it makes to a
small collector bajutsu runs on `127.0.0.1:<port>` (the port is injected into the app
via launch env, `BAJUTSU_COLLECTOR`, and a per-run shared token via
`BAJUTSU_COLLECTOR_TOKEN` — the collector accepts only POSTs bearing that token, so
another local process can't inject fabricated exchanges). The collector keeps the
exchanges in memory so a step's `request` assertion can be evaluated in real time, and
dumps them to `network.json` as scenario evidence.

The same receiver also accepts screen-transition reports on `/transitions`
(BE-0310): the opt-in `BajutsuScreen` observer in `BajutsuKit` (a
`UIViewController.viewDidAppear` hook) POSTs one record per completed appearance. They are
kept in an independent store from the network exchanges — the readiness gate and the
`settled` wait read only this one, never network-capture state, so the two stay independent
as documented.

The same receiver also carries the in-app control channel (BE-0365): bajutsu queues a command
naming one piece of its own in-app instrumentation and the state that piece should take, the app
drains the queue over an authenticated `GET /commands`, and reports back on `/commands/ack` whether
it applied the command. A command either toggles one piece of that instrumentation or, for the stub
table, replaces the whole table the app launched with.
That direction is what lets a capability change *within* a scenario rather than only at launch, and
it needs no new server, port, or authentication scheme — the app opens no socket, and the per-run
token above guards the commands exactly as it guards the reports. The channel carries no judgement:
nothing on it may influence whether a step passes, and no assertion reads from it.

The in-app side that captures and POSTs the exchanges is a separate Swift package
(`BajutsuKit`); this module is only the bajutsu-side receiver and data model.
"""

from ._functions import _ACKNOWLEDGE_PATH as _ACKNOWLEDGE_PATH
from ._functions import _COMMANDS_PATH as _COMMANDS_PATH
from ._functions import _TRANSITIONS_PATH as _TRANSITIONS_PATH
from ._functions import _make_handler as _make_handler
from ._functions import _no_transitions as _no_transitions
from ._shared import TransitionSource
from .app_command import AppCommand
from .app_command_report import AppCommandReport
from .app_stub_table_command import AppStubTableCommand
from .collector import Collector
from .control_channel import ControlChannel
from .in_app_capability import InAppCapability, ToggleCapability
from .network_collector import _BRIDGE_PORT_BASE as _BRIDGE_PORT_BASE
from .network_collector import _BRIDGE_PORT_SPAN as _BRIDGE_PORT_SPAN
from .network_collector import NetworkCollector
from .network_exchange import NetworkExchange
from .screen_transition import ScreenTransition

__all__ = [
    "AppCommand",
    "AppCommandReport",
    "AppStubTableCommand",
    "Collector",
    "ControlChannel",
    "InAppCapability",
    "NetworkCollector",
    "NetworkExchange",
    "ScreenTransition",
    "ToggleCapability",
    "TransitionSource",
]
