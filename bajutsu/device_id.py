"""One shared, documented answer to "what is a valid device id?".

A device id — an Android serial (`emulator-5554`, `192.168.1.5:5555`), an iOS Simulator UDID
(`A1B2C3D4-1122-3344-5566-77889900AABB`), or the `booted` alias — flows from `--udid` / config
into a subprocess command line: `adb -s <id>`, `xcrun simctl … <id>`. The
security invariant behind every backend's check is the same, and it is the reason this validator
exists: **an id must never start with `-`**, or the CLI it is handed to would read it as an option
(`-rf`, `--config`) — argv option injection from an untrusted `--udid` / config value.

This used to be spelled out several slightly different ways (adb's `_SERIAL`,
serve's `_UDID_RE`), differing in charset, length cap, and first-character rule. They now all
reference the single policy defined here, so "valid device id" has exactly one definition.

Policy:
    - **charset:** alphanumerics plus ``. _ : -`` — the union that covers Android serials (dots,
      underscores, and the colon of an `IP:port` `adb connect` target) and iOS UUIDs (hyphens),
      plus the `booted` alias. None of these are shell metacharacters, so an accepted id is also
      safe as a plain argv token.
    - **first character:** an alphanumeric — never ``-`` (the option-injection guard above), and
      by extension never ``. _ :`` either, so the leading character is always unambiguous.
    - **length:** 1-128 characters (a generous bound; real ids — a 36-char UUID, a short serial —
      sit well under it, while a pathologically long argument is rejected).
    - matched against the **whole** string (anchored both ends).

Callers keep their own error type: adb raises `adb.DeviceError`, the simctl family raises
`simctl.DeviceError`, and serve returns a bool. The single thing they share is this predicate.
"""

from __future__ import annotations

import re

# First char alphanumeric; then 0-127 more of the union charset (total length 1-128). `fullmatch`
# anchors both ends, so no leading `^` / trailing `$` is needed.
_DEVICE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def is_valid_device_id(value: str) -> bool:
    """Whether `value` is a safe device serial / UDID under the shared policy (see module docstring)."""
    return _DEVICE_ID_RE.fullmatch(value) is not None
