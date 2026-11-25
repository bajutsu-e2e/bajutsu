"""The services a run depends on, as written under `services:` (BE-0452, BE-0473, BE-0491)."""

from __future__ import annotations

from typing import Any

from pydantic import model_validator

from ._model import _Model
from .mailbox import Mailbox

# The two blocks BE-0491 removed, and the shape that replaces each. The loader reads neither name,
# the rule BE-0452 applied when it renamed the target's keys, so the error is the whole migration
# path an author gets.
REMOVED_SERVICES: dict[str, str] = {
    "server": (
        "a `hooks.beforeRun` entry whose script starts the server and exits once the server "
        "answers, plus a `hooks.afterRun` entry that stops it; the script owns the readiness check "
        "that `readyUrl` / `readyTimeout` once named, and `dockerImage` / `dockerfile` / `port` "
        "move to the target's `hooks.sandbox` block"
    ),
    "mockServer": (
        "a `hooks.beforeRun` entry that starts the mock server like any other server; the block "
        "never started a process, and a scenario's own `mocks` stubs its dependencies in-protocol"
    ),
}


class Services(_Model):
    """`services`: the services a run depends on — today the external inbox it polls.

    `mailbox` is an inbox a run reads and never starts (BE-0473). The two blocks that *did* start a
    process left with BE-0491: a command that brings a server up is a `hooks.beforeRun` entry, which
    runs any readiness check its own server needs instead of the one URL probe `server` could make.
    """

    mailbox: Mailbox | None = None

    @model_validator(mode="before")
    @classmethod
    def _removed_blocks(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for key, replacement in REMOVED_SERVICES.items():
            if key in data:
                raise ValueError(f"services.{key} was removed (BE-0491); use {replacement}")
        return data
