"""Read `versionName` and `versionCode` from the binary `AndroidManifest.xml` inside an `.apk`.

Only the string pool, the resource map, and the root start element are decoded: enough for the two
attributes, with no dependency and no Android SDK (BE-0506). The layout is AOSP's
`ResourceTypes.h`.
"""

from __future__ import annotations

import struct

_XML = 0x0003
_STRING_POOL = 0x0001
_RESOURCE_MAP = 0x0180
_START_ELEMENT = 0x0102
_UTF8_FLAG = 0x0100

_TYPE_STRING = 0x03
_TYPE_INT_DEC = 0x10
_TYPE_INT_HEX = 0x11

# `android.R.attr` ids.
_ATTR_IDS = {0x0101021B: "versionCode", 0x0101021C: "versionName"}


def _utf8_length(data: bytes, at: int) -> tuple[int, int]:
    first = data[at]
    if first & 0x80:
        return ((first & 0x7F) << 8) | data[at + 1], at + 2
    return first, at + 1


def _string(chunk: bytes, at: int, utf8: bool) -> str:
    if utf8:
        _chars, at = _utf8_length(chunk, at)
        size, at = _utf8_length(chunk, at)
        raw, encoding = chunk[at : at + size], "utf-8"
    else:
        (length,) = struct.unpack_from("<H", chunk, at)
        at += 2
        if length & 0x8000:
            (low,) = struct.unpack_from("<H", chunk, at)
            length, at = ((length & 0x7FFF) << 16) | low, at + 2
        size = length * 2
        raw, encoding = chunk[at : at + size], "utf-16-le"
    if len(raw) != size:
        raise ValueError("string runs past the pool")
    # Every pool entry is decoded, so one a packer or a lone surrogate broke must not cost the
    # two values this reads.
    return raw.decode(encoding, errors="replace")


def _string_pool(chunk: bytes, header_size: int) -> list[str]:
    count, _styles, flags, start = struct.unpack_from("<IIII", chunk, 8)
    offsets = struct.unpack_from(f"<{count}I", chunk, header_size)
    return [_string(chunk, start + o, bool(flags & _UTF8_FLAG)) for o in offsets]


def _attributes(
    chunk: bytes, header_size: int, strings: list[str], resource_ids: tuple[int, ...]
) -> dict[str, str]:
    start, size, count = struct.unpack_from("<HHH", chunk, header_size + 8)
    found: dict[str, str] = {}
    for index in range(count):
        at = header_size + start + index * size
        _ns, name, _raw, _size, _res0, data_type, value = struct.unpack_from("<IIIHBBI", chunk, at)
        # By resource id, as Android matches them: a shrunk app can blank the names, and another
        # namespace can reuse one.
        key = _ATTR_IDS.get(resource_ids[name]) if name < len(resource_ids) else None
        if key is None:
            continue
        if data_type == _TYPE_STRING:
            found[key] = strings[value]
        elif data_type in (_TYPE_INT_DEC, _TYPE_INT_HEX):
            found[key] = str(value)
        # Any other type (a resource reference, notably) cannot be resolved from the manifest alone.
    return found


def _decode(data: bytes) -> dict[str, str]:
    kind, header_size, total = struct.unpack_from("<HHI", data, 0)
    if kind != _XML or total > len(data):
        raise ValueError("not a complete binary XML document")
    strings: list[str] = []
    resource_ids: tuple[int, ...] = ()
    at = header_size
    while at < total:
        kind, header_size, size = struct.unpack_from("<HHI", data, at)
        if size < 8 or at + size > total:
            raise ValueError("chunk size is out of range")
        chunk = data[at : at + size]
        if kind == _STRING_POOL:
            strings = _string_pool(chunk, header_size)
        elif kind == _RESOURCE_MAP:
            resource_ids = struct.unpack_from(f"<{(size - header_size) // 4}I", chunk, header_size)
        elif kind == _START_ELEMENT:
            (name,) = struct.unpack_from("<I", chunk, header_size + 4)
            if strings[name] != "manifest":
                raise ValueError("the root element is not <manifest>")
            return _attributes(chunk, header_size, strings, resource_ids)
        at += size
    raise ValueError("no start element")


def manifest_attributes(data: bytes) -> dict[str, str]:
    """The root `<manifest>` element's `versionName` and `versionCode`, those it states literally.

    Raises:
        ValueError: The bytes are not a decodable binary manifest.
    """
    try:
        return _decode(data)
    except (struct.error, IndexError) as e:
        raise ValueError("truncated or malformed binary XML") from e
