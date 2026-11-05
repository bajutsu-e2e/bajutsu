"""The services a run depends on, as written under `services:` (BE-0452, BE-0473)."""

from __future__ import annotations

from pydantic import Field

from ._model import _Model
from .launch_server import LaunchServer
from .mailbox import Mailbox
from .mock_server import MockServer


class Services(_Model):
    """`services`: the services a run depends on, the ones it starts and the ones it reaches.

    `server` and `mockServer` are brought up around the app; `mailbox` is an external inbox the run
    polls and never starts (BE-0473).

    `server` was the target's `launchServer`; it works on every platform once it has a readiness URL,
    so it sits with the other servers rather than with the web app (BE-0452).
    """

    server: LaunchServer | None = None
    mock_server: MockServer | None = Field(default=None, alias="mockServer")
    mailbox: Mailbox | None = None
