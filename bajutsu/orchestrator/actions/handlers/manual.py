"""The `manual` human-takeover handler (BE-0185).

Unlike every other handler it drives no device: a `manual` step marks an operation a human
performed live during `record` (a CAPTCHA, a biometric prompt) that has no deterministic run-time
equivalent. At `run` time it fails loudly with the recorded label rather than faking a pass —
directives 1 and 2. An author makes the step replayable by wiring the named `bypass` (a test-build
flag, a device-control / device-state primitive), which replaces this marker with a real step.
"""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.orchestrator.actions._registry import _handler
from bajutsu.scenario import Step
from bajutsu.scenario.models.actions import bypass_hint


@_handler("manual")
def _do_manual(_d: object, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.manual is not None
    m = step.manual
    raise base.ManualStepRequired(f"manual takeover step: {m.label} — {bypass_hint(m.bypass)}")
