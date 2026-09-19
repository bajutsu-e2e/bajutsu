"""The `app` step: launch an app the test target never started and run inner steps against it."""

from __future__ import annotations

from pydantic import Field

from bajutsu.common.scenario.models._base import _Model

from .step import Step


class App(_Model):
    """Activate an app by bundle id and run inner steps against its own UI (iOS only).

    ``bundle_id`` is a plain scenario-level string, not a value drawn from a fixed set — any
    installed app can be named, including one the scenario's own target has no way to open
    itself. Inner ``steps`` address that app's own accessibility tree, exactly as they address the
    test target's outside this block; control returns to whatever was active before the block once
    it ends, matching `Web`'s enter/leave contract.
    """

    bundle_id: str = Field(alias="bundleId")
    steps: list[Step]
