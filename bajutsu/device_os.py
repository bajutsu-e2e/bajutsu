"""One shared, documented answer to "which OS version did this run happen on?" (BE-0358).

A run already records the version per scenario as `device_runtime` — the label `simctl.runtime_label`
(and its adb counterpart) produces, e.g. `"iOS 18.6"` / `"Android 14"`. That string is enough for the
report's Environment tab, but not for the two determinism surfaces: `audit --history` and
`rank_flakiness` group a scenario's verdict history by its content fingerprint alone, so the same
scenario run on two OS versions lands in one history and a verdict that differs *because the versions
differ* is scored as flakiness. Parsing the label once, here, gives both surfaces the same notion of
"same OS" — and gives a driver somewhere to read the version it is running on, which nothing below
the report could do before.

Two deliberate omissions:

- **No comparison operators.** Nothing here compares versions. An ordering nobody uses would be a
  standing invitation to the per-OS branch table BE-0358 argues against building before a case
  survives the version-agnostic alternative; the first case that needs one can add it then.
- **No guessing.** An absent or unrecognized label parses to `None`, never to a default version. The
  web backend and the WebDriver grid return an empty device catalog, so absence is a normal state
  every consumer already has to handle.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

# `iOS 18.6` / `Android 14` / `iOS 18.6.1` — anchored (`fullmatch`), so a label this doesn't
# recognize parses to None rather than to a version read out of its prefix. The patch component is
# matched but dropped: no observed behavioural difference distinguishes 18.6 from 18.6.1, and
# splitting a scenario's history across patch releases is the cost the raw-string alternative pays.
_LABEL_RE = re.compile(r"\s*(iOS|Android)\s+(\d+)(?:\.(\d+))?(?:\.\d+)*\s*", re.IGNORECASE)

# The platform token -> how it is spelled to a human. Also the set of platforms this recognizes at
# all: a `watchOS` / `tvOS` runtime is not one Bajutsu runs against, so its label parses to None.
_PLATFORMS = {"ios": "iOS", "android": "Android"}


@dataclass(frozen=True)
class DeviceOS:
    """The parsed operating-system version a run happened on — the grouping fact, not a display string.

    `label` is excluded from equality (and therefore from the hash), so two spellings of one version
    — `"iOS 18.6"` and `"iOS 18.6.1"` — are the *same* OS. That is the whole point of parsing rather
    than grouping on the raw string: a scenario's history must not split across patch releases no
    observed difference distinguishes.
    """

    platform: str  # `ios` | `android` — the token, lowercase (see `display` for the human spelling)
    major: int
    minor: int  # 0 when the label carried no minor component (`"Android 14"`)
    label: str = field(compare=False)  # the raw label this was parsed from

    @property
    def display(self) -> str:
        """The canonical human spelling of this version (`"iOS 18.6"`).

        Canonical rather than the raw `label`, because one group can hold several spellings and
        showing whichever run happened to be read first would be arbitrary.
        """
        return f"{_PLATFORMS[self.platform]} {self.major}.{self.minor}"


def parse(label: object) -> DeviceOS | None:
    """The parsed OS a `device_runtime` label names, or None when it names none.

    Args:
        label: The recorded label, as read from an untyped manifest / record. Anything that is not a
            string, and any string this doesn't recognize, is `None` — an unknown OS, never a guess.
    """
    if not isinstance(label, str):
        return None
    match = _LABEL_RE.fullmatch(label)
    if match is None:
        return None
    return DeviceOS(
        platform=match.group(1).lower(),
        major=int(match.group(2)),
        minor=int(match.group(3) or 0),
        label=label,
    )


def from_manifest(manifest: Mapping[str, object]) -> DeviceOS | None:
    """The single OS a whole run happened on, from its parsed `manifest.json`, or None when there isn't one.

    The label is recorded per *scenario* while the DB flakiness record is per *run*, so a run whose
    scenarios span OS versions can speak for none of them: it reports None and groups under the same
    unknown key an unrecognized label gets, rather than lending one version's history a verdict from
    another's.
    """
    scenarios = manifest.get("scenarios")
    parsed = {
        parse(s.get("device_runtime"))
        for s in (scenarios if isinstance(scenarios, list) else [])
        if isinstance(s, Mapping)
    }
    # A single element is unanimity — including `{None}`, where no scenario named an OS at all.
    return next(iter(parsed)) if len(parsed) == 1 else None


def describe(os: DeviceOS | None) -> str:
    """How a report names this OS, spelling out the unknown case rather than leaving a blank."""
    return os.display if os is not None else "unknown OS"


def ordering_key(os: DeviceOS | None) -> tuple[int, str, int, int]:
    """A display-ordering component for a report's sort key, sorting an unknown OS last.

    Deliberately a free function returning a tuple, not an ordering on `DeviceOS` itself: two rows
    that differ only by OS would otherwise tie and render in input order, while a comparable type
    would invite the version gate this module's docstring rules out.
    """
    if os is None:
        return (1, "", 0, 0)
    return (0, os.platform, os.major, os.minor)
