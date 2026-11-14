"""The `setClipboard` action: seed the pasteboard for a paste flow."""

from __future__ import annotations

from bajutsu.common.scenario.models._base import _Model


class SetClipboard(_Model):
    """Seed the device clipboard with text, for paste flows.

    `simctl pbcopy` on iOS; on Android, the BajutsuAndroid in-app receiver (BE-0233).
    """

    text: str
