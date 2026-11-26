"""The version an app binary declares about itself, read from the same files its hash covers."""

from __future__ import annotations

import plistlib
import zipfile
from collections.abc import Mapping
from pathlib import Path

from bajutsu.common.run_identity.axml import manifest_attributes
from bajutsu.common.run_identity.gap import Gap, Reason

# A real manifest is far smaller; the bound keeps a crafted archive from expanding without limit.
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024

Version = tuple[str | Gap, str | Gap]


def _both(reason: Reason) -> Version:
    return Gap(reason), Gap(reason)


def _declared(values: Mapping[str, object], key: str, missing: Reason) -> str | Gap:
    value = values.get(key)
    return value if isinstance(value, str) and value else Gap(missing)


def ios_version(app: Path) -> Version:
    """`CFBundleShortVersionString` and `CFBundleVersion` from the bundle's `Info.plist`."""
    try:
        with (app / "Info.plist").open("rb") as f:
            info = plistlib.load(f)
    except (FileNotFoundError, NotADirectoryError):
        return _both(Reason.PLIST_UNREADABLE)
    except OSError:
        return _both(Reason.READ_ERROR)
    # plistlib surfaces malformed input as many unrelated exception types, and the stamp must never
    # stop the run, so the list is not enumerated.
    except Exception:
        return _both(Reason.PLIST_UNREADABLE)
    if not isinstance(info, dict):
        return _both(Reason.PLIST_UNREADABLE)
    return (
        _declared(info, "CFBundleShortVersionString", Reason.PLIST_UNREADABLE),
        _declared(info, "CFBundleVersion", Reason.PLIST_UNREADABLE),
    )


def android_version(apk: Path) -> Version:
    """`versionName` and `versionCode` from the `.apk`'s `AndroidManifest.xml`."""
    try:
        with zipfile.ZipFile(apk) as archive:
            entry = archive.getinfo("AndroidManifest.xml")
            if entry.file_size > _MAX_MANIFEST_BYTES:
                return _both(Reason.APK_UNREADABLE)
            data = archive.read(entry)
    except OSError:
        return _both(Reason.READ_ERROR)
    # No such entry, not a zip, or an entry that does not inflate or decrypt. Each compression
    # method raises its own type, and the stamp must never stop the run.
    except Exception:
        return _both(Reason.APK_UNREADABLE)
    try:
        attributes = manifest_attributes(data)
    except ValueError:
        return _both(Reason.MANIFEST_UNPARSABLE)
    return (
        _declared(attributes, "versionName", Reason.MANIFEST_UNPARSABLE),
        _declared(attributes, "versionCode", Reason.MANIFEST_UNPARSABLE),
    )
