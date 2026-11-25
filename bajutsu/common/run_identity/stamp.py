"""Which build of which code a run exercised, read once per run for the provenance stamp (BE-0506).

Pure metadata: a value that cannot be read is recorded as a gap and never changes a verdict or
stops the run. A group the target never configured is omitted instead, so "not configured" and
"tried and could not read" stay distinguishable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from bajutsu.common.config.accessors import xcuitest_targets_real_device
from bajutsu.common.config.effective import AndroidConfig, Effective, IosConfig
from bajutsu.common.run_identity.app_version import android_version, ios_version
from bajutsu.common.run_identity.binary import hash_binary
from bajutsu.common.run_identity.gap import Gap, with_gaps
from bajutsu.common.run_identity.git import source_identity, tool_source


@dataclass(frozen=True)
class RunIdentity:
    """The stamp's `targets` mapping and `toolSource`, shared by every stamp of one run."""

    targets: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    tool_source: Mapping[str, object] | None = None


def app_identity(path: Path, config: IosConfig | AndroidConfig) -> dict[str, object]:
    """The `app` group for the binary at *path*: its hash, and the version it declares."""
    hashed = hash_binary(path)
    if isinstance(hashed, Gap):
        # Nothing to read a version from either, for the same reason.
        keys = ("hash", "hashKind", "version", "buildNumber")
        return with_gaps(dict.fromkeys(keys, hashed))
    digest, kind = hashed
    read = ios_version if isinstance(config, IosConfig) else android_version
    version, build_number = read(path)
    return with_gaps(
        {"hash": digest, "hashKind": kind, "version": version, "buildNumber": build_number}
    )


def _installed_elsewhere(eff: Effective) -> bool:
    """Whether the run launches an install it did not make from `subject.path`.

    A non-local device provider hands the device over with the app on it, and a real iOS device's
    build is installed out of band. The file at `subject.path` is then not known to be what ran.
    """
    if xcuitest_targets_real_device(eff):
        return True
    return eff.device_provider is not None and eff.device_provider.kind != "local"


def target_identity(eff: Effective, *, trusted: bool) -> dict[str, object]:
    """One target's `app` and `appSource`; empty for a target with neither (a web target)."""
    config = eff.platform_config
    if not isinstance(config, IosConfig | AndroidConfig):
        return {}
    out: dict[str, object] = {}
    if config.app_path and not _installed_elsewhere(eff):
        out["app"] = app_identity(Path(config.app_path), config)
    if config.source_root:
        out["appSource"] = source_identity(Path(config.source_root), trusted=trusted)
    return out


def read_run_identity(target_effs: Mapping[str, Effective], *, trusted: bool) -> RunIdentity:
    """Read every target's identity and Bajutsu's own.

    Call it after the run's builds and before its first lease, so the hash covers what the run
    launches and a rebuild mid-run cannot give two scenarios different values. *trusted* is
    `source_identity`'s own.
    """
    return RunIdentity(
        targets={
            name: target_identity(eff, trusted=trusted) for name, eff in sorted(target_effs.items())
        },
        tool_source=tool_source(),
    )
