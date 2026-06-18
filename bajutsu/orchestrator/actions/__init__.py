"""Action execution: tap / type / swipe / gestures / relaunch / device control / http.

`wait` and `assert` are conditions handled by the run loop; everything here is a one-shot
effect on the device. Handlers live in `handlers/<group>.py` and self-register via
`@_handler(kind)`; the directory scan below imports them, so adding an action's handler is
adding (or editing) a focused group file, never a central dispatch (BE-0043).
"""

from __future__ import annotations

import importlib
import pkgutil

from bajutsu.orchestrator.actions import handlers
from bajutsu.orchestrator.actions._registry import _action_of, _do_action, _step_label

# Import every handler module so its `@_handler` registrations run.
for _mod in pkgutil.iter_modules(handlers.__path__):
    importlib.import_module(f"{handlers.__name__}.{_mod.name}")

__all__ = ["_action_of", "_do_action", "_step_label"]
