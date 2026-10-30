#!/usr/bin/env python3
"""Measure the touch-synthesis marker path from recorded log streams (BE-0462 Unit 1).

Reads the `synthesis-stream.ndjson` files `synthesis_stream_record.py` leaves in each iOS job's
`runs/diagnostics/`, and answers the questions BE-0462 Unit 1 owes the host monitor:

  arrival lag     how long each marker kind, and each `backboardd` line, took to reach the host
                  after its own timestamp — the bound the monitor's condition wait needs
  inversions      `backboardd` lines that reached the host after a runner line stamped later than
                  them; any at all means a fence alone cannot close a window
  drops           every drop line, attributed to the innermost synthesis window open at its
                  timestamp, with its category (the monitor's predicate needs it) — and the drop
                  lines no window covers, which the monitor would miss
  excerpts        every line inside each dropped window, so a reader can tell whether the drop
                  followed a delivered touch-down mid-drag

It measures; it decides nothing. The numbers go into the BE item by hand.

Usage:
    python3 scripts/synthesis_stream_report.py <file-or-dir> [...] [--json]

A directory is searched recursively for `synthesis-stream.ndjson`, so a folder of downloaded CI
artifacts can be passed as is.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

RECORDING_NAME = "synthesis-stream.ndjson"
RUNNER_SUBSYSTEM = "bajutsu.runner"

_MARKER = re.compile(r"bajutsu\.synthesis (begin|end|fence|abort|serving) ([0-9a-f]+)(?:/(\d+))?")

# The three `backboardd` messages BE-0462 classifies as a drop, matched on a fragment each so the
# variable parts (display, service, path index) don't matter.
DROP_FRAGMENTS = (
    "unknown digitizer",
    "from unsupported service",
    "skipping event",
)


@dataclass(frozen=True)
class Event:
    """One recorded log line: where it came from, when it was stamped, and when the host saw it."""

    source: str
    host_ts: float
    ts: float
    process: str
    subsystem: str
    category: str
    message: str

    @property
    def is_runner(self) -> bool:
        """Whether the runner wrote this line."""
        return self.subsystem == RUNNER_SUBSYSTEM

    @property
    def is_backboardd(self) -> bool:
        """Whether `backboardd` wrote this line."""
        return self.process == "backboardd"

    @property
    def is_drop(self) -> bool:
        """Whether this is one of the `backboardd` lines BE-0462 reads as a dropped touch."""
        return self.is_backboardd and any(f in self.message for f in DROP_FRAGMENTS)

    @property
    def lag(self) -> float:
        """Seconds between the line's own timestamp and its arrival on the host."""
        return self.host_ts - self.ts


@dataclass
class Window:
    """One synthesis id's begin-to-end span within one recording."""

    source: str
    key: str
    begin: float
    end: float | None = None
    aborted: bool = False
    drops: list[Event] = field(default_factory=list)


@dataclass
class Recording:
    """One recording's events plus what its `meta` records and unreadable lines say about its health."""

    path: Path
    events: list[Event]
    bad_lines: int = 0
    sessions: int = 0
    stream_ends: int = 0
    recorder_skipped: int = 0
    gave_up: bool = False

    def warnings(self) -> list[str]:
        """Name every way this recording may under-report, so an empty one never reads as a quiet run."""
        found: list[str] = []
        if self.sessions == 0:
            found.append("no recorder session started (not a recorder's output, or it never ran)")
        if not self.events:
            found.append("no events recorded")
        elif not any(e.is_runner for e in self.events):
            found.append(
                "no runner markers (no touch synthesized, or the stream missed the runner)"
            )
        if self.bad_lines:
            found.append(f"{self.bad_lines} unreadable line(s) skipped")
        if self.recorder_skipped:
            found.append(
                f"the recorder skipped {self.recorder_skipped} stream line(s) it could not read"
            )
        if self.stream_ends:
            found.append(
                f"the stream ended {self.stream_ends} time(s) mid-job; events between sessions are missing"
            )
        if self.gave_up:
            found.append("the recorder gave up restarting the stream")
        return [f"{self.path}: {w}" for w in found]


def parse_ts(text: str) -> float:
    """Parse a unified-log timestamp (`2026-10-29 12:01:14.690123+0000`) to epoch seconds."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f%z").timestamp()


def parse_marker(message: str) -> tuple[str, str] | None:
    """Return a runner marker's kind and `<nonce>/<id>` key (the bare nonce for `serving`)."""
    m = _MARKER.search(message)
    if m is None:
        return None
    kind, nonce, ident = m.groups()
    return kind, nonce if ident is None else f"{nonce}/{ident}"


def load(path: Path) -> Recording:
    """Read one recording, counting its `meta` records and every line it could not read."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rec_out = Recording(path=path, events=[])
    for i, line in enumerate(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # The job's SIGTERM can cut the last line short; anywhere else, a bad line is counted.
            if i != len(lines) - 1:
                rec_out.bad_lines += 1
            continue
        if not isinstance(rec, dict):
            rec_out.bad_lines += 1
            continue
        meta = rec.get("meta")
        if meta == "start":
            rec_out.sessions += 1
        elif meta == "end":
            # A session the action stopped never writes `end`, so every `end` is a stream that died.
            rec_out.stream_ends += 1
            rec_out.recorder_skipped += int(rec.get("skipped", 0))
        elif meta == "gave-up":
            rec_out.gave_up = True
        if meta is not None:
            continue
        try:
            rec_out.events.append(
                Event(
                    source=str(path),
                    host_ts=float(rec["host_ts"]),
                    ts=parse_ts(rec["ts"]),
                    process=str(rec.get("process", "")),
                    subsystem=str(rec.get("subsystem", "")),
                    category=str(rec.get("category", "")),
                    message=str(rec.get("message", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            rec_out.bad_lines += 1
    return rec_out


def find_recordings(paths: Iterable[Path]) -> list[Path]:
    """Expand each directory in *paths* to the recordings beneath it, warning on a path that is absent."""
    found: list[Path] = []
    for p in paths:
        if p.is_dir():
            found.extend(sorted(p.rglob(RECORDING_NAME)))
        elif p.is_file():
            found.append(p)
        else:
            print(f"warning: {p} does not exist", file=sys.stderr)
    return found


def windows(events: Sequence[Event]) -> list[Window]:
    """Build the synthesis windows of one recording from its begin / end / abort markers.

    Begins and ends are paired by key rather than by arrival order: an end that arrives before its
    begin is exactly the reordering this report measures, and must not leave the window open.
    """
    begins: dict[str, float] = {}
    ends: dict[str, tuple[float, bool]] = {}
    source = events[0].source if events else ""
    for e in events:
        marker = parse_marker(e.message) if e.is_runner else None
        if marker is None:
            continue
        kind, key = marker
        if kind == "begin":
            begins.setdefault(key, e.ts)
        elif kind in ("end", "abort"):
            ends.setdefault(key, (e.ts, kind == "abort"))
    result: list[Window] = []
    for key, begin in begins.items():
        end, aborted = ends.get(key, (None, False))
        result.append(Window(source=source, key=key, begin=begin, end=end, aborted=aborted))
    return result


def attribute(drop: Event, wins: Sequence[Window]) -> Window | None:
    """Return the innermost closed window containing *drop*'s timestamp, the one that began last.

    A window with no end marker has no known span, so it takes no drops: letting it cover the rest
    of the recording would hide every later drop from the unattributed count.
    """
    containing = [w for w in wins if w.end is not None and w.begin <= drop.ts <= w.end]
    return max(containing, key=lambda w: w.begin, default=None)


def inversions(events: Sequence[Event]) -> list[tuple[Event, float]]:
    """Find `backboardd` lines that arrived after a runner line stamped later than them.

    *events* must be in arrival order (a recording's file order). Each hit carries how far the
    earlier-arriving runner line's timestamp ran ahead of it.
    """
    hits: list[tuple[Event, float]] = []
    latest_runner: float | None = None
    for e in events:
        if e.is_runner:
            latest_runner = e.ts if latest_runner is None else max(latest_runner, e.ts)
        elif e.is_backboardd and latest_runner is not None and e.ts < latest_runner:
            hits.append((e, latest_runner - e.ts))
    return hits


def percentiles(values: Sequence[float]) -> dict[str, float | int]:
    """Summarize *values* by count and nearest-rank p50 / p95 / p99 / max."""
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    n = len(ordered)

    def rank(q: float) -> float:
        # Nearest rank is ceil(q * n); the epsilon keeps float error from pushing an exact product
        # (0.95 * 100) up a rank.
        return ordered[min(n - 1, max(0, math.ceil(q * n - 1e-9) - 1))]

    return {"n": n, "p50": rank(0.50), "p95": rank(0.95), "p99": rank(0.99), "max": ordered[-1]}


def analyze(recordings: Sequence[Path]) -> dict[str, Any]:
    """Measure every recording in *recordings* and return the combined report."""
    lags: dict[str, list[float]] = {}
    inverted: list[dict[str, Any]] = []
    attributed: list[dict[str, Any]] = []
    unattributed: list[dict[str, Any]] = []
    categories: Counter[str] = Counter()
    health: list[dict[str, Any]] = []
    warnings: list[str] = []
    open_windows = 0
    total_windows = 0
    for path in recordings:
        rec = load(path)
        events = rec.events
        warnings += rec.warnings()
        health.append(
            {
                "source": str(path),
                "events": len(events),
                "runner_lines": sum(1 for e in events if e.is_runner),
                "sessions": rec.sessions,
                "stream_ends": rec.stream_ends,
                "bad_lines": rec.bad_lines,
                "recorder_skipped": rec.recorder_skipped,
            }
        )
        wins = windows(events)
        total_windows += len(wins)
        open_windows += sum(1 for w in wins if w.end is None)
        for e in events:
            marker = parse_marker(e.message) if e.is_runner else None
            if marker is not None:
                lags.setdefault(f"marker:{marker[0]}", []).append(e.lag)
            elif e.is_backboardd:
                lags.setdefault("backboardd", []).append(e.lag)
        for e, ahead in inversions(events):
            inverted.append({"source": e.source, "ts": e.ts, "ahead": ahead, "message": e.message})
        for drop in (e for e in events if e.is_drop):
            categories[drop.category] += 1
            win = attribute(drop, wins)
            if win is None:
                unattributed.append(_line(drop))
            else:
                win.drops.append(drop)
        attributed.extend(_dropped_window(w, events) for w in wins if w.drops)
    return {
        "recordings": len(recordings),
        "warnings": warnings,
        "health": health,
        "windows": total_windows,
        "windows_never_closed": open_windows,
        "lag": {kind: percentiles(v) for kind, v in sorted(lags.items())},
        "inversions": {
            "count": len(inverted),
            "worst": sorted(inverted, key=lambda i: -i["ahead"])[:20],
        },
        "drop_categories": dict(categories),
        "dropped_windows": attributed,
        "unattributed_drops": unattributed,
    }


def _line(e: Event) -> dict[str, Any]:
    return {
        "ts": e.ts,
        "lag": e.lag,
        "process": e.process,
        "category": e.category,
        "message": e.message,
    }


def _dropped_window(win: Window, events: Sequence[Event]) -> dict[str, Any]:
    # Only a closed window takes drops (see `attribute`), so `end` is set here.
    end = win.end if win.end is not None else win.begin
    return {
        "source": win.source,
        "key": win.key,
        "aborted": win.aborted,
        "duration": end - win.begin,
        "drops": len(win.drops),
        "excerpt": [
            _line(e) for e in sorted(events, key=lambda e: e.ts) if win.begin <= e.ts <= end
        ],
    }


def render(report: dict[str, Any]) -> str:
    """Format *report* for reading in a terminal."""
    out = [f"WARNING {w}" for w in report["warnings"]]
    out += [
        f"recordings: {report['recordings']}   windows: {report['windows']}"
        f"   never closed: {report['windows_never_closed']}",
        "",
        "arrival lag (s)        n       p50       p95       p99       max",
    ]
    for kind, s in report["lag"].items():
        if s["n"]:
            out.append(
                f"  {kind:<18}{s['n']:>5}{s['p50']:>10.4f}{s['p95']:>10.4f}{s['p99']:>10.4f}{s['max']:>10.4f}"
            )
    inv = report["inversions"]
    out += [
        "",
        f"inversions (backboardd arrived after a later-stamped runner line): {inv['count']}",
    ]
    out += [f"  {i['ahead']:.4f}s  {i['message']}" for i in inv["worst"]]
    out += ["", f"drop line categories: {report['drop_categories'] or 'none'}"]
    out += [f"unattributed drop lines: {len(report['unattributed_drops'])}"]
    out += [f"  {d['message']}" for d in report["unattributed_drops"]]
    out += ["", f"dropped windows: {len(report['dropped_windows'])}"]
    for w in report["dropped_windows"]:
        out.append(f"--- {w['key']} ({w['drops']} drop line(s)) in {w['source']}")
        out += [f"  {d['process']:<14}{d['category']:<18}{d['message']}" for d in w["excerpt"]]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """Print the measurement report for the recordings named on the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    recordings = find_recordings(args.paths)
    if not recordings:
        print(f"no {RECORDING_NAME} found", file=sys.stderr)
        return 1
    report = analyze(recordings)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
