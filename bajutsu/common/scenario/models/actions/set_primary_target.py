"""The `setPrimaryTarget` action: move the target an omitted `target` resolves to (BE-0447)."""

from __future__ import annotations

from bajutsu.common.scenario.models._base import _Model


class SetPrimaryTarget(_Model):
    """From this step on, a step, `interrupts` entry, or `expect` entry omitting `target` runs on `target`.

    It changes routing and nothing else: each target keeps its own config, driver, and environment,
    so the move needs no mid-run swap. Leasing, evidence, and crash recovery stay with the primary
    the scenario declared.
    """

    target: str
