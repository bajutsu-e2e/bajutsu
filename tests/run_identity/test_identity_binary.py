"""The binary hash and the version readers behind the `app` stamp (BE-0506)."""

from __future__ import annotations

import hashlib
import os
import plistlib
import struct
import zipfile
from pathlib import Path

import pytest

from bajutsu.common.run_identity.app_version import android_version, ios_version
from bajutsu.common.run_identity.axml import manifest_attributes
from bajutsu.common.run_identity.binary import hash_binary
from bajutsu.common.run_identity.gap import Gap, Reason


def _bundle(root: Path) -> Path:
    (root / "Frameworks").mkdir(parents=True)
    (root / "App").write_bytes(b"binary")
    (root / "Frameworks" / "lib").write_bytes(b"library")
    return root


def _tree_hash(root: Path) -> str:
    result = hash_binary(root)
    assert not isinstance(result, Gap)
    assert result[1] == "tree"
    return result[0]


def test_a_file_hashes_its_bytes(tmp_path: Path) -> None:
    (tmp_path / "a.apk").write_bytes(b"apk")
    digest = hashlib.sha256(b"apk").hexdigest()
    assert hash_binary(tmp_path / "a.apk") == (f"sha256:{digest}", "file")


def test_equal_bundles_hash_the_same(tmp_path: Path) -> None:
    assert _tree_hash(_bundle(tmp_path / "a")) == _tree_hash(_bundle(tmp_path / "b"))


def test_the_tree_hash_covers_names_bytes_and_links(tmp_path: Path) -> None:
    app = _bundle(tmp_path / "a")
    seen = [_tree_hash(app)]
    (app / "App").rename(app / "Renamed")
    seen.append(_tree_hash(app))
    (app / "Renamed").write_bytes(b"binarY")
    seen.append(_tree_hash(app))
    (app / "link").symlink_to("Renamed")
    seen.append(_tree_hash(app))
    (app / "linked-dir").symlink_to("Frameworks")
    seen.append(_tree_hash(app))
    assert len(set(seen)) == len(seen)


def test_a_file_and_a_link_with_equal_content_hash_differently(tmp_path: Path) -> None:
    (tmp_path / "file").mkdir()
    (tmp_path / "file" / "x").write_text("target", encoding="utf-8")
    (tmp_path / "link").mkdir()
    (tmp_path / "link" / "x").symlink_to("target")
    assert _tree_hash(tmp_path / "file") != _tree_hash(tmp_path / "link")


def test_a_missing_binary_is_a_gap(tmp_path: Path) -> None:
    assert hash_binary(tmp_path / "none.app") == Gap(Reason.PATH_MISSING)


def test_an_unreadable_binary_is_a_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _bundle(tmp_path / "a")

    def refuse(*_a: object, **_k: object) -> None:
        raise PermissionError

    monkeypatch.setattr(Path, "open", refuse)
    assert hash_binary(app) == Gap(Reason.READ_ERROR)


def test_ios_version_reads_the_info_plist(tmp_path: Path) -> None:
    info = {"CFBundleShortVersionString": "1.4", "CFBundleVersion": "52"}
    (tmp_path / "Info.plist").write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
    assert ios_version(tmp_path) == ("1.4", "52")


@pytest.mark.parametrize("content", [None, b"not a plist", b"<?xml", plistlib.dumps([1])])
def test_an_unusable_info_plist_is_a_gap(tmp_path: Path, content: bytes | None) -> None:
    if content is not None:
        (tmp_path / "Info.plist").write_bytes(content)
    assert ios_version(tmp_path) == (Gap(Reason.PLIST_UNREADABLE),) * 2


def test_a_plist_without_a_version_key_is_a_gap_for_that_key(tmp_path: Path) -> None:
    (tmp_path / "Info.plist").write_bytes(plistlib.dumps({"CFBundleVersion": "52"}))
    assert ios_version(tmp_path) == (Gap(Reason.PLIST_UNREADABLE), "52")


def _pool(strings: list[str], utf8: bool) -> bytes:
    blobs, offsets, at = [], [], 0
    for s in strings:
        if utf8:
            raw = s.encode()
            lengths = b"".join(
                bytes([n]) if n < 0x80 else bytes([0x80 | n >> 8, n & 0xFF])
                for n in (len(s), len(raw))
            )
            blob = lengths + raw + b"\0"
        else:
            blob = struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\0\0"
        offsets.append(at)
        blobs.append(blob)
        at += len(blob)
    body = b"".join(blobs)
    body += b"\0" * (-len(body) % 4)
    start = 28 + 4 * len(strings)
    header = struct.pack(
        "<HHIIIIII",
        0x0001,
        28,
        start + len(body),
        len(strings),
        0,
        0x100 if utf8 else 0,
        start,
        0,
    )
    return header + struct.pack(f"<{len(offsets)}I", *offsets) + body


def _manifest(
    *, utf8: bool = False, name_type: int = 0x03, root: str = "manifest", blank: bool = False
) -> bytes:
    names = ["", ""] if blank else ["versionCode", "versionName"]
    strings = [*names, root, "2.0.1"]
    resource_map = struct.pack("<HHIII", 0x0180, 8, 16, 0x0101021B, 0x0101021C)
    attributes = struct.pack("<IIIHBBI", 0xFFFFFFFF, 0, 0xFFFFFFFF, 8, 0, 0x10, 41)
    attributes += struct.pack("<IIIHBBI", 0xFFFFFFFF, 1, 3, 8, 0, name_type, 3)
    element = struct.pack("<IIIIHHHHHH", 1, 0xFFFFFFFF, 0xFFFFFFFF, 2, 20, 20, 2, 0, 0, 0)
    element += attributes
    element = struct.pack("<HHI", 0x0102, 16, 8 + len(element)) + element
    body = _pool(strings, utf8) + resource_map + element
    return struct.pack("<HHI", 0x0003, 8, 8 + len(body)) + body


def _apk(path: Path, manifest: bytes | None) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("classes.dex", b"dex")
        if manifest is not None:
            archive.writestr("AndroidManifest.xml", manifest)
    return path


@pytest.mark.parametrize("utf8", [False, True])
@pytest.mark.parametrize("blank", [False, True])
def test_android_version_reads_the_binary_manifest(tmp_path: Path, utf8: bool, blank: bool) -> None:
    apk = _apk(tmp_path / "a.apk", _manifest(utf8=utf8, blank=blank))
    assert android_version(apk) == ("2.0.1", "41")


@pytest.mark.parametrize("cut", [4, 20, 60, -4])
def test_a_truncated_manifest_is_a_gap(tmp_path: Path, cut: int) -> None:
    apk = _apk(tmp_path / "a.apk", _manifest()[:cut])
    assert android_version(apk) == (Gap(Reason.MANIFEST_UNPARSABLE),) * 2


@pytest.mark.parametrize(
    "data", [_manifest(root="application"), b"<manifest/>" * 4, struct.pack("<HHI", 3, 8, 8)]
)
def test_a_document_without_a_manifest_root_is_rejected(data: bytes) -> None:
    with pytest.raises(ValueError, match=r"manifest|binary XML|start element"):
        manifest_attributes(data)


def test_a_zero_sized_chunk_is_rejected_rather_than_looped_on() -> None:
    data = struct.pack("<HHIHHI", 3, 8, 16, 1, 8, 0)
    with pytest.raises(ValueError, match="chunk size"):
        manifest_attributes(data)


def test_a_version_name_given_as_a_resource_reference_is_a_gap(tmp_path: Path) -> None:
    apk = _apk(tmp_path / "a.apk", _manifest(name_type=0x01))
    assert android_version(apk) == (Gap(Reason.MANIFEST_UNPARSABLE), "41")


@pytest.mark.parametrize("content", ["truncated", "not-a-zip", "no-manifest"])
def test_an_unusable_apk_is_a_gap(tmp_path: Path, content: str) -> None:
    apk = tmp_path / "a.apk"
    if content == "not-a-zip":
        apk.write_bytes(b"nope")
    elif content == "no-manifest":
        _apk(apk, None)
    else:
        whole = _apk(apk, _manifest()).read_bytes()
        apk.write_bytes(whole[: len(whole) // 2])
    assert android_version(apk) == (Gap(Reason.APK_UNREADABLE),) * 2


@pytest.mark.parametrize(
    "content",
    [
        b"<plist><dict></dict><key>a</key></plist>",
        b'<?xml version="1.0" encoding="nope"?><plist/>',
    ],
)
def test_a_plist_that_raises_an_unusual_error_is_still_a_gap(
    tmp_path: Path, content: bytes
) -> None:
    (tmp_path / "Info.plist").write_bytes(content)
    assert ios_version(tmp_path) == (Gap(Reason.PLIST_UNREADABLE),) * 2


def test_a_corrupt_lzma_manifest_is_still_a_gap(tmp_path: Path) -> None:
    apk = tmp_path / "a.apk"
    with zipfile.ZipFile(apk, "w", compression=zipfile.ZIP_LZMA) as archive:
        archive.writestr("AndroidManifest.xml", _manifest() * 20)
    data = bytearray(apk.read_bytes())
    start = 30 + len("AndroidManifest.xml") + 9
    data[start : start + 40] = b"\xff" * 40
    apk.write_bytes(bytes(data))
    assert android_version(apk) == (Gap(Reason.APK_UNREADABLE),) * 2


def test_an_empty_bundle_is_no_binary(tmp_path: Path) -> None:
    (tmp_path / "App.app" / "Frameworks").mkdir(parents=True)
    assert hash_binary(tmp_path / "App.app") == Gap(Reason.PATH_MISSING)


def test_a_directory_that_cannot_be_listed_is_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _bundle(tmp_path / "a")
    real = os.scandir

    def scandir(path: str) -> object:
        if str(path).endswith("Frameworks"):
            raise PermissionError
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    assert hash_binary(app) == Gap(Reason.READ_ERROR)


def test_a_linked_directory_is_not_followed(tmp_path: Path) -> None:
    app = _bundle(tmp_path / "a")
    outside = tmp_path / "outside"
    outside.mkdir()
    (app / "out").symlink_to(outside)
    before = _tree_hash(app)
    (outside / "new").write_bytes(b"x")
    assert _tree_hash(app) == before


@pytest.mark.parametrize("utf8", [False, True])
def test_long_strings_and_chunks_before_the_root_are_skipped(utf8: bool) -> None:
    # Real manifests open with a namespace chunk and hold strings past the one-unit length.
    plain = _manifest(utf8=utf8)
    long = "x" * 200
    pool_size = struct.unpack_from("<I", plain, 12)[0]
    pool = _pool(["versionCode", "versionName", "manifest", "2.0.1", long], utf8)
    namespace = struct.pack("<HHIIIII", 0x0100, 16, 24, 1, 0xFFFFFFFF, 4, 4)
    body = pool + namespace + plain[8 + pool_size :]
    document = struct.pack("<HHI", 0x0003, 8, 8 + len(body)) + body
    assert manifest_attributes(document) == {"versionCode": "41", "versionName": "2.0.1"}


def test_a_missing_apk_is_a_read_error(tmp_path: Path) -> None:
    assert android_version(tmp_path / "none.apk") == (Gap(Reason.READ_ERROR),) * 2
