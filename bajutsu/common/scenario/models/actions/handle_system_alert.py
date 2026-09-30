"""The `handleSystemAlert` action: tap a button on a system permission prompt (BE-0316)."""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.models.selector import Selector
from bajutsu.common.scenario.system_alerts import (
    SystemAlertChoice,
    SystemAlertPrompt,
    SystemAlertRole,
    alert_surfaces,
    labels_cover,
    system_alert_label,
    system_alert_role,
)

# The label-based Selector fields a SpringBoard alert button can actually carry (Python field
# names); every other field (id/idMatches/traits/value/within) could never match one — the alert
# lives outside the app's accessibility tree, so it has no app-assigned identifier, trait, or value.
_SYSTEM_ALERT_SEL_FIELDS = frozenset({"label", "label_matches", "index"})


class HandleSystemAlert(_Model):
    """`handleSystemAlert` action — tap a button on an iOS SpringBoard permission prompt (BE-0316).

    A permission alert lives outside the app's accessibility tree (SpringBoard owns it), so it
    carries no app-assigned identifier, trait set, or value — only its visible text. `sel` therefore
    accepts the label-based fields alone (`label` / `labelMatches` / `index`) and rejects the rest at
    parse time. `timeout` bounds the condition wait for the prompt, required exactly as `wait`'s is.

    Two ways to name the button, exactly one per step:
        handleSystemAlert: { sel: { label: "Allow" }, timeout: 10 }
        handleSystemAlert: { prompt: notifications, choice: grant, timeout: 10 }

    The second form states the *intent* and lets the run resolve the label from the scenario's
    locale (BE-0320), for the prompts a `permissions` preset cannot pre-answer — notification
    authorization, App Tracking Transparency, and the cross-process paste consent (BE-0369). It is
    worth reaching for because the literal text is easy to get subtly wrong: English's own deny
    button spells its apostrophe typographically, not as the ASCII character a hand-typed label
    carries. Every other alert keeps naming its button through `sel`, unchanged.

    Not every covered prompt is nameable here. This step reads the SpringBoard query alone, so a
    prompt iOS raises into the application's own process — `savePassword` — is rejected below and
    declared as a `systemAlertHandling` rule instead (BE-0406).
    """

    sel: Selector | None = None
    prompt: SystemAlertPrompt | None = None
    choice: SystemAlertChoice | None = None
    timeout: float

    @model_validator(mode="after")
    def _one_way_to_name_the_button(self) -> Self:
        # The pairing check runs first, so a step carrying only one half of the intent form is told
        # what is actually wrong rather than the misleading "neither form was given".
        if (self.prompt is None) != (self.choice is None):
            raise ValueError("handleSystemAlert prompt and choice are set together (§6.2)")
        if (self.sel is None) == (self.prompt is None):
            raise ValueError(
                "handleSystemAlert names its button either by sel or by prompt + choice, "
                "never both and never neither (§6.2)"
            )
        if self.prompt is not None and not alert_surfaces(self.prompt)["step"]:
            # This step resolves its selector against the SpringBoard query alone, so a prompt that
            # query can never see would poll an empty button list until the step's deadline — the
            # very failure BE-0406 exists to remove. Reject it here, naming the declaration that
            # does reach it, rather than accepting a step that could only ever time out.
            raise ValueError(
                f"handleSystemAlert cannot answer the {self.prompt} prompt: iOS raises it inside "
                "the application's own process, where this step's SpringBoard query never sees it. "
                f"Declare it reactively instead — "
                f"systemAlertHandling: {{ rules: [{{ prompt: {self.prompt}, choice: … }}] }}"
            )
        return self

    @model_validator(mode="after")
    def _label_only_selector(self) -> Self:
        if self.sel is None:
            return self
        disallowed = sorted(
            f for f in self.sel.model_fields_set if f not in _SYSTEM_ALERT_SEL_FIELDS
        )
        if disallowed:
            # Name each rejected field by the alias the author writes (idMatches, not id_matches),
            # read from the Selector model itself so this can't drift from the real aliases.
            aliases = ", ".join(Selector.model_fields[f].alias or f for f in disallowed)
            raise ValueError(
                "handleSystemAlert sel accepts only label / labelMatches / index — "
                f"a SpringBoard alert button carries no {aliases} (§6.2)"
            )
        return self

    def resolved(self, locale: str) -> HandleSystemAlert:
        """This step with `prompt`/`choice` turned into the `sel` the locale's SpringBoard renders.

        A `sel` form returns unchanged, so the resolution is a no-op for every alert outside the
        prompts the lookup covers. Under a language the label table does not cover, a prompt with a
        position rule (`role`) also returns unchanged: that rule is resolved against the live alert
        instead, since the label it lands on is exactly what this run cannot know in advance
        (BE-0445).

        Raises:
            UncoveredSystemAlertLocale: the lookup has no labels for the locale's language and the
                prompt has no position rule either — the step fails loudly rather than tapping a
                guessed button.
        """
        if self.prompt is None or self.choice is None:
            return self
        if not labels_cover(self.prompt, locale) and self.role() is not None:
            return self
        label = system_alert_label(self.prompt, self.choice, locale)
        return self.model_copy(
            update={"sel": Selector(label=label), "prompt": None, "choice": None}
        )

    def role(self) -> SystemAlertRole | None:
        """The position rule naming this step's button, for a `prompt`/`choice` step that has one."""
        if self.prompt is None or self.choice is None:
            return None
        return system_alert_role(self.prompt, self.choice)
