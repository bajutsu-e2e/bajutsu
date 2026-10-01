"""The device-group lifecycle steps, `installApp` and `setPrimaryTarget`, outside `run` (BE-0447).

`bajutsu run`'s step loop handles both itself, against the run's target roster, before dispatch
ever reaches this registry. Any other caller of `_do_action` — `record`, `enrich` — drives one
target with no roster, so the steps have nothing to act on there and fail clearly instead.
"""

from __future__ import annotations

from bajutsu.common.drivers import base
from bajutsu.common.orchestrator.actions._registry import _handler


@_handler("install_app")
def _do_install_app(_d: object, _s: object, _r: object, _c: object, _b: object) -> None:
    raise base.UnsupportedAction("installApp runs only in `bajutsu run`, against a device group")


@_handler("set_primary_target")
def _do_set_primary_target(_d: object, _s: object, _r: object, _c: object, _b: object) -> None:
    raise base.UnsupportedAction("setPrimaryTarget runs only in `bajutsu run`, across targets")
