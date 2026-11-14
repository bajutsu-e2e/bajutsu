"""The `paste` action: insert the clipboard into a field."""

from __future__ import annotations

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.models.selector import Selector


class Paste(_Model):
    """`paste` action — focus the field, then trigger the platform paste of the clipboard.

    The counterpart of `copy` and `setClipboard`: whatever the clipboard holds — a `copy` from an
    earlier `select`, or text seeded with `setClipboard` — is inserted at the focused field's caret,
    replacing any active selection. The step holds no state of its own; its outcome is verified with
    the existing `value` assertion on the field, never by reading anything this step recorded.
    """

    into: Selector
