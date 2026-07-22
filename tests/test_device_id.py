"""Tests for the shared device-id validator (`bajutsu.device_id`).

`is_valid_device_id` is the single policy that adb (`_checked_serial`) and serve (`valid_udid`)
both reference. These pin the unified charset / first-character / length
rules — in particular the security invariant that an id may never start with `-` (argv option
injection) — so the one definition can't drift.
"""

from __future__ import annotations

import pytest

from bajutsu.device_id import is_valid_device_id


@pytest.mark.parametrize(
    "value",
    [
        "booted",  # the "current device" alias every backend accepts
        "U",  # a single alphanumeric is the shortest valid id
        "A1B2C3D4-1122-3344-5566-77889900AABB",  # iOS Simulator UUID (hyphens)
        "emulator-5554",  # Android emulator serial
        "192.168.1.5:5555",  # adb connect IP:port target (dots + colon)
        "usb_serial.01",  # underscore + dot
        "x" * 128,  # at the length cap (1 first char + 127 more)
    ],
)
def test_accepts_real_device_ids(value: str) -> None:
    assert is_valid_device_id(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",  # empty
        "-rf",  # leading hyphen — the option-injection case behind the whole policy
        "--udid",  # leading hyphen
        ".hidden",  # first char must be alphanumeric, not `.`
        "_leading",  # first char must be alphanumeric, not `_`
        ":colon",  # first char must be alphanumeric, not `:`
        "a b",  # embedded space
        "a;b",  # shell metacharacter
        "a$b",  # shell metacharacter
        "a`b",  # shell metacharacter
        "a/b",  # `/` is outside the charset
        "a\nb",  # newline
        "x" * 129,  # one over the length cap
    ],
)
def test_rejects_unsafe_ids(value: str) -> None:
    assert is_valid_device_id(value) is False
