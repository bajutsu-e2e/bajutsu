"""The servers a run starts or stands in for, as written under `services:` (BE-0452)."""

from __future__ import annotations

from pydantic import Field

from ._model import _Model
from .launch_server import LaunchServer
from .mailbox import Mailbox
from .mock_server import MockServer


class Services(_Model):
    """`services`: the servers a run brings up around the app, whatever its platform.

    `server` was the target's `launchServer`; it works on every platform once it has a readiness URL,
    so it sits with the other servers rather than with the web app (BE-0452).
    """

    server: LaunchServer | None = None
    mock_server: MockServer | None = Field(default=None, alias="mockServer")
    mailbox: Mailbox | None = None
