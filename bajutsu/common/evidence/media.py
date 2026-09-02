"""How long a finished recording actually is, read from the file itself.

The interval providers stop a recording at a known instant, so the recording's own duration places
its *origin* — the moment its first frame was captured — without trusting any start-confirmation
proxy (`intervals.Interval.true_start`). That origin is what a report anchors step and network
timestamps to, so the parsing here is deliberately narrow: only the one field each container needs
to state its duration, from the two containers the interval providers produce — ISO base media
(`simctl io recordVideo`, `adb shell screenrecord`) and Matroska/WebM (Playwright's recorder, whose
output the sink still names `.mp4`). Anything else, and any file that does not parse, answers
`None` so the caller degrades to the proxy rather than to a guessed number.

Reads are bounded rather than whole-file: a scenario recording is tens of megabytes, and the field
in question is a few bytes near one end of it.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import BinaryIO

_logger = logging.getLogger(__name__)

# How much of a Matroska file to hold while looking for its `Info` element. ffmpeg writes `Info`
# right after the segment header and rewrites its `Duration` in place on finalize, so the field is
# always within the first blocks; the cap keeps a malformed file from pulling a whole recording
# into memory.
_MATROSKA_SCAN_BYTES = 1 << 20
# The largest `moov` this reads once the top-level walk has found it. A movie header plus its track
# headers is kilobytes; a value past this is a damaged length, not a real box.
_MAX_MOOV_BYTES = 64 << 20

_FTYP = b"ftyp"
_EBML_MAGIC = b"\x1a\x45\xdf\xa3"


def duration_seconds(path: Path) -> float | None:
    """How many seconds of footage `path` holds, or None when the file cannot state it.

    None is the honest answer for an unreadable, truncated, or unrecognized recording — never 0.0,
    which a caller would read as a real duration and so place the recording's origin at the moment
    it stopped.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(_MATROSKA_SCAN_BYTES)
            if head[4:8] == _FTYP:
                return _mp4_duration(fh)
            if head[:4] == _EBML_MAGIC:
                return _matroska_duration(head)
    except OSError as exc:
        _logger.debug("could not read %s to measure its duration: %s", path, exc)
        return None
    _logger.debug("%s is in no container this module reads; its duration stays unknown", path)
    return None


# --- ISO base media (mp4) ---


def _mp4_duration(fh: BinaryIO) -> float | None:
    """The movie header's duration, in seconds, from an open ISO base media file.

    Walks the top-level boxes by seeking rather than reading: `mdat` (the frames) sits between
    `ftyp` and `moov` and is the whole file's bulk. `mvhd` is the movie-wide field, so finding it
    needs no per-track walk.
    """
    payload = _mp4_movie_header(fh)
    return _mvhd_duration(payload) if payload is not None else None


def _mp4_movie_header(fh: BinaryIO) -> bytes | None:
    """The `mvhd` payload, found by walking `moov`'s children, or None when there is none."""
    fh.seek(0)
    pos = 0
    while True:
        kind_size = _mp4_box_header(fh.read(16))
        if kind_size is None:
            return None
        kind, size, header_len = kind_size
        if kind == b"moov":
            if size > _MAX_MOOV_BYTES:
                return None
            fh.seek(pos + header_len)
            return _mvhd_in(fh.read(size - header_len))
        pos += size
        fh.seek(pos)


def _mp4_box_header(header: bytes) -> tuple[bytes, int, int] | None:
    """One box header as (type, total box size, header length), or None when it is not readable.

    A `size` of 0 ("runs to end of file") answers None: this walk only needs the boxes *before* the
    last one, and treating an unbounded box as a real length would make the caller seek past the
    file rather than stop.
    """
    if len(header) < 8:
        return None
    size = int.from_bytes(header[:4], "big")
    kind = header[4:8]
    header_len = 8
    if size == 1:  # 64-bit largesize follows the type
        if len(header) < 16:
            return None
        size = int.from_bytes(header[8:16], "big")
        header_len = 16
    if size < header_len:
        return None
    return kind, size, header_len


def _mvhd_in(moov: bytes) -> bytes | None:
    """The `mvhd` payload among `moov`'s direct children."""
    pos = 0
    while pos + 8 <= len(moov):
        kind_size = _mp4_box_header(moov[pos : pos + 16])
        if kind_size is None:
            return None
        kind, size, header_len = kind_size
        stop = pos + size
        if stop > len(moov):
            return None
        if kind == b"mvhd":
            return moov[pos + header_len : stop]
        pos = stop
    return None


def _mvhd_duration(payload: bytes) -> float | None:
    """`timescale` and `duration` out of one `mvhd` payload, as seconds.

    An all-ones `duration` is the format's own "unknown" sentinel, so it answers None like an
    absent header rather than a nonsensical span.
    """
    if not payload:
        return None
    version = payload[0]
    # version 0 stores 32-bit creation/modification/duration; version 1 widens them to 64-bit.
    head = 12 if version == 0 else 20  # version + flags, then the two creation/modification fields
    if version == 0:
        if len(payload) < head + 8:
            return None
        timescale, duration = struct.unpack_from(">II", payload, head)
        unknown = 0xFFFFFFFF
    else:
        if len(payload) < head + 12:
            return None
        (timescale,) = struct.unpack_from(">I", payload, head)
        (duration,) = struct.unpack_from(">Q", payload, head + 4)
        unknown = 0xFFFFFFFFFFFFFFFF
    if timescale == 0 or duration == 0 or duration == unknown:
        return None
    return float(duration) / float(timescale)


# --- Matroska / WebM ---

_SEGMENT = 0x18538067
_INFO = 0x1549A966
_TIMESTAMP_SCALE = 0x2AD7B1
_DURATION = 0x4489
# Matroska's default `TimestampScale`, in nanoseconds per tick, when a file states none.
_DEFAULT_TIMESTAMP_SCALE = 1_000_000


def _vint(data: bytes, pos: int, *, keep_marker: bool) -> tuple[int, int] | None:
    """One EBML variable-length integer at `pos`, as (value, position after it).

    Element ids keep the length marker (`keep_marker`) because that is how the specification writes
    them; sizes strip it. None when the descriptor runs past the buffer or names no width at all.
    """
    if pos >= len(data):
        return None
    first = data[pos]
    if first == 0:  # a width past 8 bytes: not a descriptor this format produces
        return None
    width = 8 - first.bit_length() + 1
    if pos + width > len(data):
        return None
    raw = int.from_bytes(data[pos : pos + width], "big")
    return (raw if keep_marker else raw & ~(1 << (7 * width))), pos + width


def _matroska_duration(data: bytes) -> float | None:
    """The segment's `Duration`, scaled by its `TimestampScale`, in seconds."""
    # From byte 0: the `EBML` header element precedes `Segment`, and the walk skips over it.
    segment = _find_element(data, 0, len(data), _SEGMENT)
    if segment is None:
        return None
    info = _find_element(data, segment[0], segment[1], _INFO)
    if info is None:
        return None
    scale = _DEFAULT_TIMESTAMP_SCALE
    duration: float | None = None
    pos, end = info
    while pos < end:
        found = _element_at(data, pos, end)
        if found is None:
            return None
        element_id, body, stop = found
        if element_id == _TIMESTAMP_SCALE:
            scale = int.from_bytes(data[body:stop], "big") or _DEFAULT_TIMESTAMP_SCALE
        elif element_id == _DURATION:
            duration = _ebml_float(data[body:stop])
        pos = stop
    if duration is None or duration <= 0:
        return None
    return duration * scale / 1_000_000_000


def _element_at(data: bytes, pos: int, end: int) -> tuple[int, int, int] | None:
    """One EBML element header at `pos`, as (id, payload start, payload end).

    A declared size running past what the caller holds is bounded by `end` rather than rejected,
    which covers both shapes `Segment` arrives in: the "unknown size" descriptor (every size bit
    set) a still-open live mux writes, and the real whole-file size a finalized mux writes — larger
    than the bounded scan above whenever the recording is, which is the ordinary case for a
    scenario-length clip. A genuinely damaged file still reads as unknown, because the `Info` /
    `Duration` walk inside then finds nothing to parse.
    """
    ident = _vint(data, pos, keep_marker=True)
    if ident is None:
        return None
    size = _vint(data, ident[1], keep_marker=False)
    if size is None:
        return None
    body = size[1]
    width = size[1] - ident[1]
    stop = end if size[0] == (1 << (7 * width)) - 1 else min(body + size[0], end)
    if not body <= stop <= end:
        return None
    return ident[0], body, stop


def _find_element(data: bytes, pos: int, end: int, wanted: int) -> tuple[int, int] | None:
    """The payload span of the first `wanted` element among the siblings from `pos` to `end`."""
    while pos < end:
        found = _element_at(data, pos, end)
        if found is None:
            return None
        element_id, body, stop = found
        if element_id == wanted:
            return body, stop
        pos = stop
    return None


def _ebml_float(payload: bytes) -> float | None:
    """An EBML float payload (4 or 8 bytes) as a Python float."""
    if len(payload) == 4:
        return float(struct.unpack(">f", payload)[0])
    if len(payload) == 8:
        return float(struct.unpack(">d", payload)[0])
    return None
