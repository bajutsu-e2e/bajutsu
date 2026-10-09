"""The surface table behind every prompt the schema names."""

from __future__ import annotations

from typing import TypedDict

from .alert_surfaces import AlertSurfaces


class _PromptSurfaces(TypedDict):
    """Every prompt `SystemAlertPrompt` names, with the surfaces it reaches.

    A `TypedDict` for the same reason `_Prompts` is one: declaring a prompt without its record is
    then a type error, rather than a `KeyError` raised out of `alert_surfaces` at parse time.
    """

    notifications: AlertSurfaces
    tracking: AlertSurfaces
    paste: AlertSurfaces
    savePassword: AlertSurfaces
    localNetwork: AlertSurfaces
