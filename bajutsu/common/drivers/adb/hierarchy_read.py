"""One resident-channel read: the hierarchy XML and its read mark (BE-0332)."""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from .heads_up_banner import HeadsUpBanner


# A resident UI Automator server (BE-0245) returns the hierarchy over an already-open channel,
# skipping the ~2.4 s per-invocation `uiautomator dump` startup. Its response is UI Automator's own
# XML, unchanged, so `parse_hierarchy` consumes it identically — only the transport differs.
@dataclass(frozen=True)
class HierarchyRead:
    """One resident-channel read: the hierarchy XML and its read mark (BE-0332 Unit 3).

    `mark` is the device-clock timestamp (`SystemClock.uptimeMillis`) of the most recent accessibility
    event the resident reader had observed when it served this dump. `AdbDriver` trusts a read once its
    `mark` postdates the mark it took before actuating, so a stable-but-stale tree — one that agrees
    with itself yet predates the last gesture — is no longer accepted (the read-lag defect). It is None
    on the `uiautomator dump` fallback, which carries no such stamp; there the wall-clock budget stands
    in, exactly as before this unit.

    `text` is the body exactly as the device answered it, untouched by bajutsu's own processing, so a
    `rawTree` capture (`RawSourceProvider`) can tell a device dump that already looked wrong from one
    bajutsu changed.

    `root` is that same body parsed once, with the SystemUI decor windows already stripped — the tree
    the caller should consume. Carrying the parsed tree rather than a re-serialized string is what
    keeps the host from parsing every read twice (BE-0407 unit 23): narrowing had to parse to strip a
    window, and the parser then parsed the string it produced. None on the dump fallback and on a
    body that would not parse, where the caller falls back to slicing `text` itself. `narrowed` says
    whether stripping actually removed a window, so the `rawTree` capture knows whether the parsed
    tree is worth serializing beside the reply — and, being the only consumer, pays that serialization
    only when it is actually taken.

    `native_z` maps each opted-in view's content key to its own `View.getZ()` (BE-0355 Unit 3), keyed
    the way it is because the device measures those values in a second walk whose node sequence does
    not line up with the dumped body's. Empty on the dump fallback, on a server that does not report
    it, and on an app that opted no view in.

    `heads_up` is the heads-up notification the body's SystemUI windows showed, read before
    narrowing drops them (BE-0496). None on the dump fallback, which never carries those windows, and
    whenever no heads-up was pinned.

    `settled` is whether the device's own settle closed on two agreeing dumps (BE-0505). False means
    the server gave up on a tree that may be part old and part new. None on the dump fallback and on a
    server that sends no such header, where the driver takes the tree as it always has.
    """

    text: str
    mark: float | None = None
    native_z: dict[str, float] = field(default_factory=dict)
    root: ET.Element | None = None
    narrowed: bool = False
    heads_up: HeadsUpBanner | None = None
    settled: bool | None = None
