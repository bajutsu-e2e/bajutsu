"""Tests for reading a finished recording's duration out of the file itself."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from bajutsu.evidence import media

# --- ISO base media (mp4) builders ---


def _box(kind: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def _large_box(kind: bytes, payload: bytes) -> bytes:
    """The 64-bit form: `size` is the literal 1 and the real size follows the type."""
    return (1).to_bytes(4, "big") + kind + (len(payload) + 16).to_bytes(8, "big") + payload


def _mvhd_v0(timescale: int, duration: int) -> bytes:
    body = bytes([0]) + b"\0\0\0"  # version 0 + flags
    body += b"\0" * 8  # creation + modification (32-bit each)
    body += struct.pack(">II", timescale, duration)
    return _box(b"mvhd", body + b"\0" * 80)  # the rate/volume/matrix tail this reader skips


def _mvhd_v1(timescale: int, duration: int) -> bytes:
    body = bytes([1]) + b"\0\0\0"  # version 1 + flags
    body += b"\0" * 16  # creation + modification (64-bit each)
    body += struct.pack(">IQ", timescale, duration)
    return _box(b"mvhd", body + b"\0" * 80)


def _mp4(mvhd: bytes, *, mdat: bytes = b"\0" * 4096) -> bytes:
    # `mdat` between `ftyp` and `moov` is the ordinary progressive-recording layout, and the bulk
    # the reader must seek over rather than read.
    return _box(b"ftyp", b"isom") + _box(b"mdat", mdat) + _box(b"moov", mvhd)


# --- Matroska / WebM builders ---


def _vint(value: int) -> bytes:
    """`value` as a 1-byte EBML size descriptor (every element these tests build is small)."""
    assert value < 0x7F
    return bytes([0x80 | value])


def _element(ident: bytes, payload: bytes) -> bytes:
    return ident + _vint(len(payload)) + payload


def _wide_vint(value: int) -> bytes:
    """`value` as an 8-byte EBML size descriptor — how a finalized mux writes a whole-file size."""
    return bytes([0x01]) + value.to_bytes(7, "big")


def _webm(duration_ticks: float, *, scale: int = 1_000_000, unknown_size: bool = False) -> bytes:
    info = _element(b"\x2a\xd7\xb1", scale.to_bytes(4, "big"))
    info += _element(b"\x44\x89", struct.pack(">d", duration_ticks))
    segment_body = _element(b"\x15\x49\xa9\x66", info)
    header = _element(b"\x1a\x45\xdf\xa3", b"\x42\x86\x81\x01")
    if unknown_size:
        # What a live mux writes while the segment is still open: every size bit set.
        return header + b"\x18\x53\x80\x67" + b"\x01\xff\xff\xff\xff\xff\xff\xff" + segment_body
    return header + _element(b"\x18\x53\x80\x67", segment_body)


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_mp4_duration_from_a_version_0_movie_header(tmp_path: Path) -> None:
    # The ordinary simctl / screenrecord shape: `mvhd` states ticks and the timescale they are in.
    path = _write(tmp_path, "v.mp4", _mp4(_mvhd_v0(timescale=600, duration=1500)))
    assert media.duration_seconds(path) == pytest.approx(2.5)


def test_mp4_duration_from_a_version_1_movie_header(tmp_path: Path) -> None:
    # A long recording widens the same two fields to 64 bits; the reader must follow the version.
    path = _write(tmp_path, "v.mp4", _mp4(_mvhd_v1(timescale=1000, duration=90_500)))
    assert media.duration_seconds(path) == pytest.approx(90.5)


def test_mp4_duration_reads_past_a_64_bit_sized_box(tmp_path: Path) -> None:
    # A recording whose `mdat` exceeds 4 GiB carries the 64-bit `largesize` form. The walk has to
    # skip it by its real size, or it lands mid-`mdat` and never finds `moov`.
    data = (
        _box(b"ftyp", b"isom")
        + _large_box(b"mdat", b"\0" * 4096)
        + _box(b"moov", _mvhd_v0(timescale=600, duration=600))
    )
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) == pytest.approx(1.0)


def test_mp4_without_a_movie_header_has_no_duration(tmp_path: Path) -> None:
    # A recording killed before its finalize has frames but no `moov`; that is unknown, not zero.
    data = _box(b"ftyp", b"isom") + _box(b"mdat", b"\0" * 32)
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) is None


def test_mp4_unknown_duration_sentinel_reads_as_unknown(tmp_path: Path) -> None:
    # All-ones is the format's own "not known" value — returning it as seconds would invent a span
    # roughly 200 days long and place the recording's origin that far in the past.
    path = _write(tmp_path, "v.mp4", _mp4(_mvhd_v0(timescale=600, duration=0xFFFFFFFF)))
    assert media.duration_seconds(path) is None


def test_mp4_zero_timescale_reads_as_unknown(tmp_path: Path) -> None:
    path = _write(tmp_path, "v.mp4", _mp4(_mvhd_v0(timescale=0, duration=600)))
    assert media.duration_seconds(path) is None


def test_mp4_truncated_movie_header_reads_as_unknown(tmp_path: Path) -> None:
    # Half a `mvhd` must answer None rather than unpack whatever bytes happen to follow it.
    data = _box(b"ftyp", b"isom") + _box(b"moov", _box(b"mvhd", bytes([0]) + b"\0\0\0"))
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) is None


def test_webm_duration_scaled_by_its_timestamp_scale(tmp_path: Path) -> None:
    # Playwright's recorder writes Matroska (the sink still names it `.mp4`), whose `Duration` is in
    # `TimestampScale` ticks — nanoseconds per tick — not seconds.
    path = _write(tmp_path, "v.mp4", _webm(2440.0))
    assert media.duration_seconds(path) == pytest.approx(2.44)


def test_webm_duration_inside_an_unknown_size_segment(tmp_path: Path) -> None:
    # A segment written by a still-open live mux declares an unknown size; the walk must bound it by
    # the buffer rather than reject it, or a recording whose duration *was* written reads as unknown.
    path = _write(tmp_path, "v.mp4", _webm(1000.0, unknown_size=True))
    assert media.duration_seconds(path) == pytest.approx(1.0)


def test_webm_without_a_duration_reads_as_unknown(tmp_path: Path) -> None:
    info = _element(b"\x2a\xd7\xb1", (1_000_000).to_bytes(4, "big"))
    header = _element(b"\x1a\x45\xdf\xa3", b"\x42\x86\x81\x01")
    data = header + _element(b"\x18\x53\x80\x67", _element(b"\x15\x49\xa9\x66", info))
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) is None


def test_webm_without_an_info_element_reads_as_unknown(tmp_path: Path) -> None:
    header = _element(b"\x1a\x45\xdf\xa3", b"\x42\x86\x81\x01")
    data = header + _element(b"\x18\x53\x80\x67", b"")
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) is None


def test_an_unrecognized_container_reads_as_unknown(tmp_path: Path) -> None:
    assert media.duration_seconds(_write(tmp_path, "v.mp4", b"not a recording at all")) is None


def test_a_missing_file_reads_as_unknown(tmp_path: Path) -> None:
    # The pull that never landed, or the finalize that dropped its artifact: unknown, never zero.
    assert media.duration_seconds(tmp_path / "gone.mp4") is None


# --- malformed input: every path degrades to "unknown", never to a wrong number ---


def test_mp4_with_an_implausibly_large_moov_reads_as_unknown(tmp_path: Path) -> None:
    # A damaged length must not turn into a multi-gigabyte read. A movie header plus its track
    # headers is kilobytes, so anything past the ceiling is corruption, not a real box.
    data = _box(b"ftyp", b"isom") + (media._MAX_MOOV_BYTES + 9).to_bytes(4, "big") + b"moov"
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) is None


def test_mp4_box_smaller_than_its_own_header_reads_as_unknown(tmp_path: Path) -> None:
    # A size below the header length would walk the reader backwards forever; it stops instead.
    data = _box(b"ftyp", b"isom") + (4).to_bytes(4, "big") + b"moov"
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) is None


def test_mp4_truncated_64_bit_header_reads_as_unknown(tmp_path: Path) -> None:
    # `size == 1` promises an 8-byte largesize that a truncated file does not carry.
    data = _box(b"ftyp", b"isom") + (1).to_bytes(4, "big") + b"moov" + b"\0\0\0"
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) is None


def test_mp4_moov_child_running_past_the_box_reads_as_unknown(tmp_path: Path) -> None:
    # A child claiming more bytes than `moov` holds is damage; reading it would run off the buffer.
    moov = (4096).to_bytes(4, "big") + b"trak" + b"\0" * 8
    assert media.duration_seconds(_write(tmp_path, "v.mp4", _mp4(moov, mdat=b""))) is None


def test_mp4_moov_child_smaller_than_its_header_reads_as_unknown(tmp_path: Path) -> None:
    moov = (4).to_bytes(4, "big") + b"trak" + b"\0" * 8
    assert media.duration_seconds(_write(tmp_path, "v.mp4", _mp4(moov, mdat=b""))) is None


def test_mp4_moov_without_a_movie_header_among_its_children_reads_as_unknown(
    tmp_path: Path,
) -> None:
    assert media.duration_seconds(_write(tmp_path, "v.mp4", _mp4(_box(b"trak", b"\0" * 8)))) is None


def test_webm_zero_length_descriptor_reads_as_unknown(tmp_path: Path) -> None:
    # A leading zero byte names a width past 8 bytes, which this format never writes.
    data = b"\x1a\x45\xdf\xa3" + b"\x84" + b"\0\0\0\0" + b"\x00"
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) is None


def test_webm_element_running_past_the_buffer_reads_as_unknown(tmp_path: Path) -> None:
    # A segment claiming more bytes than the file holds is damage, not an unknown-size segment.
    header = _element(b"\x1a\x45\xdf\xa3", b"\x42\x86\x81\x01")
    data = header + b"\x18\x53\x80\x67" + bytes([0x80 | 0x40]) + b"\0" * 4
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) is None


def test_webm_duration_written_as_a_32_bit_float(tmp_path: Path) -> None:
    # Matroska allows either float width for `Duration`; both must read the same seconds.
    info = _element(b"\x2a\xd7\xb1", (1_000_000).to_bytes(4, "big"))
    info += _element(b"\x44\x89", struct.pack(">f", 500.0))
    header = _element(b"\x1a\x45\xdf\xa3", b"\x42\x86\x81\x01")
    data = header + _element(b"\x18\x53\x80\x67", _element(b"\x15\x49\xa9\x66", info))
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) == pytest.approx(0.5)


def test_webm_duration_of_an_unreadable_width_reads_as_unknown(tmp_path: Path) -> None:
    info = _element(b"\x2a\xd7\xb1", (1_000_000).to_bytes(4, "big"))
    info += _element(b"\x44\x89", b"\0\0")  # neither 4 nor 8 bytes
    header = _element(b"\x1a\x45\xdf\xa3", b"\x42\x86\x81\x01")
    data = header + _element(b"\x18\x53\x80\x67", _element(b"\x15\x49\xa9\x66", info))
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) is None


def test_webm_zero_timestamp_scale_falls_back_to_the_format_default(tmp_path: Path) -> None:
    # A scale of 0 would divide the duration away; the specification's default stands in.
    info = _element(b"\x2a\xd7\xb1", (0).to_bytes(4, "big"))
    info += _element(b"\x44\x89", struct.pack(">d", 3000.0))
    header = _element(b"\x1a\x45\xdf\xa3", b"\x42\x86\x81\x01")
    data = header + _element(b"\x18\x53\x80\x67", _element(b"\x15\x49\xa9\x66", info))
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) == pytest.approx(3.0)


def test_webm_negative_duration_reads_as_unknown(tmp_path: Path) -> None:
    assert media.duration_seconds(_write(tmp_path, "v.mp4", _webm(-1.0))) is None


def test_webm_truncated_info_child_reads_as_unknown(tmp_path: Path) -> None:
    # An `Info` whose last child is cut off mid-descriptor: unknown, not a partial duration.
    header = _element(b"\x1a\x45\xdf\xa3", b"\x42\x86\x81\x01")
    info = b"\x2a\xd7\xb1"  # an id with no size descriptor after it
    data = header + _element(b"\x18\x53\x80\x67", _element(b"\x15\x49\xa9\x66", info))
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) is None


def test_a_directory_in_place_of_a_recording_reads_as_unknown(tmp_path: Path) -> None:
    # The `OSError` branch: a path that exists but cannot be opened as a file.
    (tmp_path / "v.mp4").mkdir()
    assert media.duration_seconds(tmp_path / "v.mp4") is None


def test_mp4_empty_movie_header_reads_as_unknown(tmp_path: Path) -> None:
    # A zero-length `mvhd` has not even a version byte to branch on.
    assert media.duration_seconds(_write(tmp_path, "v.mp4", _mp4(_box(b"mvhd", b"")))) is None


def test_mp4_truncated_version_1_movie_header_reads_as_unknown(tmp_path: Path) -> None:
    # Version 1's widened fields need 12 more bytes than this payload carries.
    payload = bytes([1]) + b"\0\0\0" + b"\0" * 16 + b"\0\0\0\0"
    assert media.duration_seconds(_write(tmp_path, "v.mp4", _mp4(_box(b"mvhd", payload)))) is None


def test_webm_skips_info_children_it_does_not_read(tmp_path: Path) -> None:
    # `Info` also carries `MuxingApp`, `Title`, and friends; the walk must step over them rather
    # than stop at the first element it does not recognize.
    info = _element(b"\x4d\x80", b"bajutsu")  # MuxingApp, before the two fields that matter
    info += _element(b"\x2a\xd7\xb1", (1_000_000).to_bytes(4, "big"))
    info += _element(b"\x44\x89", struct.pack(">d", 1500.0))
    header = _element(b"\x1a\x45\xdf\xa3", b"\x42\x86\x81\x01")
    data = header + _element(b"\x18\x53\x80\x67", _element(b"\x15\x49\xa9\x66", info))
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) == pytest.approx(1.5)


def test_webm_duration_in_a_segment_larger_than_the_scan_window(tmp_path: Path) -> None:
    # The shape a real Playwright recording has: on finalize the mux writes `Segment`'s real
    # whole-file size, which for a scenario-length clip runs far past the bounded scan this reader
    # holds. Rejecting a size that overruns the buffer would make every recording worth measuring
    # read as unknown — the exact case a suite of sub-window fixtures cannot see.
    info = _element(b"\x2a\xd7\xb1", (1_000_000).to_bytes(4, "big"))
    info += _element(b"\x44\x89", struct.pack(">d", 12_000.0))
    header = _element(b"\x1a\x45\xdf\xa3", b"\x42\x86\x81\x01")
    declared = media._MATROSKA_SCAN_BYTES * 8  # a size no bounded read will ever hold
    data = header + b"\x18\x53\x80\x67" + _wide_vint(declared) + _element(b"\x15\x49\xa9\x66", info)
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) == pytest.approx(12.0)


def test_webm_child_header_straddling_its_parents_end_reads_as_unknown(tmp_path: Path) -> None:
    # Bounding an over-long size by the buffer must not also swallow a header that begins inside its
    # parent and ends outside it: there is no element there to read, only the next one's bytes.
    header = _element(b"\x1a\x45\xdf\xa3", b"\x42\x86\x81\x01")
    # A `Segment` declaring two bytes, followed by an `Info` header that needs five.
    data = header + b"\x18\x53\x80\x67" + _vint(2) + b"\x15\x49\xa9\x66" + _vint(0)
    assert media.duration_seconds(_write(tmp_path, "v.mp4", data)) is None
