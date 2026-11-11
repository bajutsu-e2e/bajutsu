"""The error raised when the resident server refuses to set a picker wheel, and why."""

from __future__ import annotations

from .adb_resident_error import AdbResidentError


class AdbPickerRefused(AdbResidentError):
    """The resident server reached the wheel, or tried to, and could not land it on the value (BE-0480).

    `reason` is the token the server leads its refusal with — `not-a-picker-wheel`,
    `picker-value-absent`, `picker-walk-exhausted`, `picker-immovable` — or `failed` for a refusal it
    did not classify.
    The driver maps each to its own message, so a walk budget that needs retuning never reads as a
    missing row.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
