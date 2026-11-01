"""One prompt the reactive guard answers, and how: a `systemAlert` interrupt resolved over its layers."""

from __future__ import annotations

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.system_alerts import SystemAlertChoice, SystemAlertPrompt


class SystemAlertRule(_Model):
    """The choice the guard makes on one named prompt, once the layers have resolved.

    A `systemAlert` entry of `interrupts` (BE-0473) whose `choice` is not `leave` becomes one of
    these after the scenario's entries are layered over the target's; a `leave` answers nothing, so
    it produces none.

    `prompt` and `choice` reuse the vocabulary the proactive `handleSystemAlert` step already takes
    (its `prompt`/`choice` form) instead of a literal button label, so the same rule grants or denies
    the prompt under any locale `bajutsu.common.scenario.system_alerts` covers. The reactive guard
    identifies which alert is on screen from that prompt's own identifying labels, which is why
    BE-0406 made this the guard's only declaration: a button label names a button, never the alert
    it sits on, so it could never say which prompt an author actually expected.

    A prompt not every answer path can reach is still declarable here — `savePassword` is answered by
    the in-tree dismissal alone, since iOS raises it into the application's own process where the
    SpringBoard query never sees it. `system_alerts.alert_surfaces` records which paths each prompt
    reaches, and `run` reads it when it resolves these rules.
    """

    prompt: SystemAlertPrompt
    choice: SystemAlertChoice
