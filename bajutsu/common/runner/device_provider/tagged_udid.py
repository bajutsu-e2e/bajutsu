"""Split a platform-tagged `--udid` list, as `ios=AAAA,android=emulator-5588` (BE-0511).

A job that borrows devices of two platforms hands them to one run as one list. An entry with no tag
serves every platform, which keeps a hand-written `--udid` working.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import get_args

from bajutsu.common.capability.worker_capability import LeasedPlatform

_TAGS: tuple[str, ...] = get_args(LeasedPlatform)


def tag_addresses(addresses: Iterable[tuple[str, str]]) -> str:
    """The `--udid` list of ``(platform, address)`` pairs, each address tagged with its platform."""
    return ",".join(f"{platform}={address}" for platform, address in addresses)


def entries_for_platform(udid: str, platform: str) -> str:
    """The entries of *udid* a *platform* target drives, without their tags, comma-joined.

    Raises:
        ValueError: An entry carries an unknown tag, or none serves *platform*.
    """
    kept: list[str] = []
    for entry in (e.strip() for e in udid.split(",")):
        if not entry:
            continue
        # The tag ends at the first `=`; an address with a colon stays whole.
        tag, separator, address = entry.partition("=")
        if not separator:
            kept.append(entry)
        elif tag not in _TAGS:
            raise ValueError(
                f"--udid entry {entry!r} names an unknown platform {tag!r}: "
                f"use one of {', '.join(_TAGS)}"
            )
        elif tag == platform:
            kept.append(address)
    if not kept:
        raise ValueError(f"--udid has no entry for platform {platform!r}")
    return ",".join(kept)
