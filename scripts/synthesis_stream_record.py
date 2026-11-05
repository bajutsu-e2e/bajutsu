#!/usr/bin/env python3
"""Record the Simulator's touch-synthesis log stream with each line's host arrival time (BE-0462 Unit 1).

BE-0462's host monitor will judge each synthesized touch from a live `log stream`, and two of its
choices rest on numbers nobody has measured on the CI host: how long a runner marker takes to reach
the host (the bound its condition wait needs), and whether logd ever delivers a `backboardd` line
after a runner line stamped later than it (whether a fence can close a window at all). A `log show`
extract taken after the job cannot answer either, because it carries no arrival time. This records
the live stream instead, one JSON object per event, so `synthesis_stream_report.py` can measure both.

The predicate takes `backboardd` and `testmanagerd` whole rather than one category: the drop lines'
category is itself something Unit 1 has to establish for the monitor's own predicate.

The child writes through a pseudo-terminal, not a pipe. A pipe lets `log stream` block-buffer its
output, which would charge the buffering to the very arrival lag this exists to measure.

Every stream session opens with a `start` record and closes with one terminator: `end` (the stream
ended on its own; it carries the child's exit code and how many lines it wrote and skipped) or
`stopped` (the action's SIGTERM). A session left with neither was killed outright or crashed, so an
empty or cut-short recording reads as a recorder failure rather than as a quiet run. A stream that
ends (the lanes that reboot or erase the device mid-job end it) is restarted until this process is
told to stop.

Purely an observer: it decides nothing, and it runs until the `collect-ios-diagnostics` action stops
it with SIGTERM.

Usage:
    python3 scripts/synthesis_stream_record.py --udid <udid> --out <file.ndjson>
"""

from __future__ import annotations

import argparse
import json
import os
import pty
import select
import signal
import subprocess
import sys
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import FrameType
from typing import Any, TextIO

PREDICATE = 'subsystem == "bajutsu.runner" OR process == "backboardd" OR process == "testmanagerd"'
PREAMBLE = "Filtering the log data"

# A restart cadence and a read poll for an observer, not waits any verdict depends on.
RESTART_PAUSE = 2.0
POLL_INTERVAL = 0.5
# Give up once no session has written an event for this long: a device gone for good (a re-leased
# lane's old UDID) must not fill the artifact with failed sessions, while a slow erase-and-boot on a
# contended host must not end the recording before the device is back.
GIVE_UP_AFTER = 600.0
# A session that streamed this long reached the device, even if nothing matched the predicate: an
# idle device is not an unreachable one.
HEALTHY_SESSION = 30.0


def stream_cmd(udid: str) -> list[str]:
    """Build the `simctl` command that streams the guest log the measurement needs, as ndjson."""
    return [
        "xcrun",
        "simctl",
        "spawn",
        udid,
        "log",
        "stream",
        "--style",
        "ndjson",
        "--predicate",
        PREDICATE,
    ]


def to_record(line: str, host_ts: float) -> dict[str, Any] | None:
    """Reduce one `log stream` ndjson line to the fields the report reads, stamped with *host_ts*.

    Returns None for a line that is not a log event.
    """
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict) or "timestamp" not in event:
        return None
    return {
        "host_ts": host_ts,
        "ts": event["timestamp"],
        "process": Path(str(event.get("processImagePath", ""))).name,
        "subsystem": event.get("subsystem", ""),
        "category": event.get("category", ""),
        "type": event.get("messageType", ""),
        "message": event.get("eventMessage", ""),
    }


def record(lines: Iterable[tuple[float, bytes | str]], out: TextIO) -> tuple[int, int]:
    """Write one record per stamped event line in *lines*; return how many were written and skipped.

    The stream's own preamble is neither: only a line that should have been an event and was not
    counts as skipped, so a format change shows up as a skip count instead of an empty file.
    """
    written = skipped = 0
    for host_ts, raw in lines:
        text = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw).strip()
        if not text or text.startswith(PREAMBLE):
            continue
        rec = to_record(text, host_ts)
        if rec is None:
            skipped += 1
            continue
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # Flushed per line: the action stops this with SIGTERM, and nothing buffered may be lost.
        out.flush()
        written += 1
    return written, skipped


def write_meta(out: TextIO, kind: str, **fields: object) -> None:
    """Append one `meta` record (a session boundary), which the report counts but never measures."""
    out.write(json.dumps({"host_ts": time.time(), "meta": kind, **fields}) + "\n")
    out.flush()


def _pty_lines(fd: int, child: subprocess.Popen[bytes]) -> Iterator[tuple[float, bytes]]:
    """Yield the child's output lines from the pty master until it exits, each with its read time.

    Stamped when `os.read` returns, not when the consumer pulls the line, so the time spent writing
    earlier lines of a burst is not charged to the later ones' arrival. The end is the child's exit,
    not the master's EOF: on macOS the master drops unread output once the last slave fd closes, so
    the caller keeps its slave open and this drains the master after the exit instead.
    """
    buf = b""
    exited = False
    while True:
        ready, _, _ = select.select([fd], [], [], 0 if exited else POLL_INTERVAL)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if chunk:
                host_ts = time.time()
                buf += chunk
                *complete, buf = buf.split(b"\n")
                for line in complete:
                    yield host_ts, line
                continue
        if exited:
            break
        if child.poll() is not None:
            exited = True
    if buf:
        yield time.time(), buf


def stream_once(udid: str, out: TextIO) -> tuple[int, int, int]:
    """Run one `log stream` session into *out*; return its exit code and the written/skipped counts.

    The child's stderr is this process's, which the action keeps beside the recording.
    """
    master, slave = pty.openpty()
    child: subprocess.Popen[bytes] | None = None
    written = skipped = 0
    try:
        # Spawned inside the `try`, so a SIGTERM landing right after the spawn still reaches the
        # cleanup below.
        child = subprocess.Popen(stream_cmd(udid), stdin=subprocess.DEVNULL, stdout=slave)
        written, skipped = record(_pty_lines(master, child), out)
    finally:
        # Reached on the child's exit and on the SystemExit the signal handler raises mid-read
        # alike, so the child is never left streaming on its own.
        if child is not None:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        os.close(slave)
        os.close(master)
    return child.returncode, written, skipped


# The open recording, so the signal handler can close the running session with a `stopped` record.
_recording: list[TextIO] = []


def _stop(_signum: int, _frame: FrameType | None) -> None:
    # Python runs this between bytecodes on the main thread, so no record is half-written beneath it.
    if _recording:
        write_meta(_recording[0], "stopped")
    sys.exit(0)


def main(argv: list[str] | None = None) -> int:
    """Stream the device log into *--out* until this process is terminated."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--udid", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    # Installed before any child exists, so no signal can arrive while one runs unsupervised.
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Created owner-only rather than chmod-ed afterwards: the stream carries the app's own messages.
    fd = os.open(args.out, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as out:
        _recording.append(out)
        session = 0
        last_productive = time.monotonic()
        while True:
            session += 1
            write_meta(out, "start", session=session, udid=args.udid)
            began = time.monotonic()
            rc, written, skipped = stream_once(args.udid, out)
            write_meta(out, "end", session=session, rc=rc, written=written, skipped=skipped)
            print(
                f"log stream session {session} ended: exit {rc}, {written} written, {skipped} skipped",
                file=sys.stderr,
            )
            if written or time.monotonic() - began >= HEALTHY_SESSION:
                last_productive = time.monotonic()
            elif time.monotonic() - last_productive >= GIVE_UP_AFTER:
                write_meta(out, "gave-up", sessions=session)
                return 1
            time.sleep(RESTART_PAUSE)


if __name__ == "__main__":
    sys.exit(main())
