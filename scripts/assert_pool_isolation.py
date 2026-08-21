#!/usr/bin/env python3
"""Assert the device pool's isolation invariant against a real concurrent-device run (BE-0298).

`bajutsu/runner/pool.py`'s `device_pool` claims that under `--workers N` each worker leases its own
device and writes evidence under its own `run_dir/<sid>` subdirectory of the one shared run
directory, sharing no mock port or index with any other worker's scenario — the no-shared-state
invariant DESIGN.md §3.3 states. `tests/runner/test_pool.py` proves that claim only of the pool's own
bookkeeping: it monkeypatches `make_driver` to hand `FakeDriver` instances fabricated udids, so it
can show worker A's resources are separate from worker B's *in the data structures the pool manages*
and nothing about OS-level device and process contention outside them. This helper turns the same
claim into a pass/fail an E2E lane can gate on, read from what a real two-device run actually left
behind.

It reads the finished run's `manifest.json` (the run's canonical render model — every result carries
its leased `device`, its evidence-dir slug `sid`, and every artifact name it recorded) and the run
directory's own subdirectory listing, and checks five things:

- **Every result names its device and its evidence dir.** A blank `device` would make every check
  below vacuous rather than failing.
- **The devices really were shared out.** The distinct `device` count must be at least
  `--expect-devices`, so a run where one worker quietly did all the work fails instead of passing as
  "isolated". A lower bound rather than an equality, because a device replaced mid-run legitimately
  makes a result name a device the pool was never handed (see `_device_violations`).
- **Two workers really were busy at once.** With more than one device expected, some pair of
  scenarios on *different* devices must overlap in wall-clock — the concurrency the lane exists to
  produce, rather than a pool that alternated devices serially.
- **No scenario wrote outside its own evidence dir.** Every artifact name a result recorded must sit
  under that result's own `sid/`, and no two results may share a `sid`.
- **The directory and the results account for each other.** Every subdirectory of the run directory
  must belong to exactly one result, so evidence cross-written under a name no result claims fails
  rather than going unnoticed — and every directory a result recorded evidence under must exist, so a
  manifest entry pointing at evidence the run never wrote fails too.

The decision is a file read and a set comparison: no model touches it, and it runs after `bajutsu
run` has already returned its own verdict, so it is an observer of the run's artifacts and never an
input to any scenario's pass/fail.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

# Where a platform that starts recording before the app launches stages that video — reserved at the
# run directory's top level by `bajutsu/runner/pool.py`, and left behind once the finished recording
# has been moved into the scenario's own evidence dir. It belongs to no worker (Android and web
# reserve it per lease under the one shared name), so the orphan-directory check below must not read
# it as evidence written outside every worker's dir.
_VIDEO_STAGING = "_video_tmp"


def _entries(container: Mapping[str, object], key: str) -> list[object]:
    """The list stored at `key`, or an empty list when the manifest holds anything else there.

    A manifest is read from disk, so no field's shape is guaranteed. Treating a non-list as empty
    keeps a damaged record from crashing the check — the run's own results are still read, and a
    result whose steps or artifacts went missing simply contributes no names or window, which the
    device and overlap checks then report as the gap it is.
    """
    value = container.get(key)
    return value if isinstance(value, list) else []


def artifact_names(scenario: Mapping[str, object]) -> list[str]:
    """Every artifact name one result recorded — its scenario-level captures and its steps'.

    Names are run-directory-relative (`RunArtifactWriter._resolve`), which is what makes the
    own-evidence-dir check below a plain prefix test.
    """
    names: list[str] = [
        name
        for artifact in _entries(scenario, "artifacts")
        if isinstance(artifact, Mapping) and isinstance(name := artifact.get("name"), str)
    ]
    for step in _entries(scenario, "steps"):
        if isinstance(step, Mapping):
            names.extend(artifact_names(step))
    return names


def scenario_window(scenario: Mapping[str, object]) -> tuple[float, float] | None:
    """The wall-clock window a result occupied, or None when its steps recorded no absolute instant.

    Derived from the steps' own `started_at` (absolute epoch seconds since manifest v6) rather than
    the scenario's `duration_s`, which also covers the launch and verification either side of the
    first and last step: an end taken from the last step's own finish *understates* the window, so a
    reported overlap is real rather than an artifact of counting setup time twice. None (a run
    predating v6, or a result with no steps) leaves the result out of the overlap check instead of
    contributing a window of zeros that would never overlap anything.
    """
    starts: list[float] = []
    ends: list[float] = []
    for step in _entries(scenario, "steps"):
        if not isinstance(step, Mapping):
            continue
        started_at = step.get("started_at")
        if not isinstance(started_at, int | float) or started_at <= 0:
            continue
        duration = step.get("duration_s")
        elapsed = float(duration) if isinstance(duration, int | float) else 0.0
        starts.append(float(started_at))
        ends.append(float(started_at) + elapsed)
    if not starts:
        return None
    return min(starts), max(ends)


def _overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Whether two half-open wall-clock windows intersect."""
    return a[0] < b[1] and b[0] < a[1]


def _device_violations(scenarios: Sequence[Mapping[str, object]], expect_devices: int) -> list[str]:
    """The devices-were-shared-out and workers-were-concurrent halves of the invariant."""
    violations: list[str] = []
    devices = sorted({str(s.get("device") or "") for s in scenarios if s.get("device")})
    # A lower bound, not an equality: `pool.py`'s `adopt_replacement` re-keys a lease onto the device
    # `XcuitestEnvironment` mints when CoreSimulator has stopped listing the leased one — which happens
    # on a `--udid`-pinned run too (BE-0344) — and `pipeline.py` stamps that replacement on the result,
    # so an isolated run can name more devices than its pool held. Exceeding the pool size is strictly
    # *more* separation between workers, and it is indistinguishable in the manifest from a legitimate
    # replacement; using fewer devices than the run was handed is the violation, and it still fails.
    if len(devices) < expect_devices:
        violations.append(
            f"expected at least {expect_devices} distinct leased device(s), saw {len(devices)}: "
            f"{devices or '[]'} — the pool did not share the work out across the devices it was given"
        )
    if expect_devices < 2:
        return violations
    windows = [
        (str(s.get("device") or ""), str(s.get("sid") or ""), window)
        for s in scenarios
        if (window := scenario_window(s)) is not None
    ]
    concurrent = [
        (a_sid, b_sid)
        for i, (a_device, a_sid, a_window) in enumerate(windows)
        for b_device, b_sid, b_window in windows[i + 1 :]
        if a_device != b_device and _overlaps(a_window, b_window)
    ]
    if not concurrent:
        violations.append(
            "no two scenarios on different devices overlapped in wall-clock — the run used several "
            "devices but never kept two workers busy at once, so it exercised no real contention"
        )
    return violations


def isolation_violations(
    scenarios: Sequence[Mapping[str, object]],
    dir_names: Sequence[str],
    *,
    expect_devices: int,
) -> list[str]:
    """Every way the run's artifacts contradict the pool's isolation claim, in report order.

    `dir_names` are the run directory's own subdirectory names, so evidence written under a slug no
    result claims is caught even though no manifest entry points at it. An empty list means the run's
    artifacts are consistent with the claim.
    """
    if not scenarios:
        return ["the manifest recorded no scenario results"]
    violations: list[str] = []
    slug_owners: dict[str, list[str]] = {}
    recorded_evidence: set[str] = set()
    for index, scenario in enumerate(scenarios):
        name = str(scenario.get("scenario") or f"#{index}")
        sid = str(scenario.get("sid") or "")
        if not sid:
            violations.append(f"scenario {name!r} recorded no evidence-dir slug (sid)")
            continue
        if not scenario.get("device"):
            violations.append(f"scenario {name!r} ({sid}) recorded no leased device (udid)")
        slug_owners.setdefault(sid, []).append(name)
        names = artifact_names(scenario)
        if names:
            recorded_evidence.add(sid)
        violations.extend(
            f"scenario {name!r} recorded artifact {artifact!r} outside its own "
            f"evidence dir {sid!r} — evidence crossed between workers"
            for artifact in names
            if artifact != sid and not artifact.startswith(f"{sid}/")
        )
    for sid, owners in slug_owners.items():
        if len(owners) > 1:
            violations.append(
                f"evidence dir {sid!r} is claimed by {len(owners)} scenarios ({', '.join(owners)}) "
                "— two workers shared one evidence dir"
            )
    violations.extend(
        f"the run directory holds {dir_name + '/'!r}, which belongs to no scenario result "
        "— evidence was written outside every worker's own dir"
        for dir_name in dir_names
        if dir_name not in slug_owners
    )
    # The mirror direction: a result naming an artifact under a directory the run never wrote. That is
    # the "artifact path computed before a worker's subdirectory exists" hazard this module's header
    # names — a manifest entry pointing at evidence a reader cannot open. Gated on the result having
    # recorded a name at all, so the claim stays "it recorded evidence and the evidence is not there"
    # rather than demanding a directory from a result that captured nothing (a dropped recording is
    # never recorded: `evidence/core.py` skips the artifact entry along with the file).
    held = set(dir_names)
    violations.extend(
        f"scenario {owners[0]!r} recorded evidence under {sid!r}, which the run directory does not "
        "hold — the worker's evidence never landed"
        for sid, owners in slug_owners.items()
        if sid in recorded_evidence and sid not in held
    )
    violations.extend(_device_violations(scenarios, expect_devices))
    return violations


def _subdirectory_names(run_dir: Path) -> list[str]:
    """The run directory's own subdirectories, sorted, minus the one that is not a worker's.

    Only directories are collected, so the run-level report files (`manifest.json`, `report.html`,
    `junit.xml`, `ctrf.json`, `scenario.yaml`) need no exemption. `_VIDEO_STAGING` is the single
    top-level directory that is not a scenario's evidence dir, and it is exempted by that exact
    name rather than by a broad "skip anything underscored" rule, which would let a genuinely
    cross-written directory through on a name nobody chose deliberately. Symlinks are excluded the
    same way `RunArtifactReader.names` excludes them: `is_dir()` follows one, so a link planted in a
    run directory would otherwise be read as a scenario's evidence dir.
    """
    return sorted(
        entry.name
        for entry in run_dir.iterdir()
        if entry.is_dir() and not entry.is_symlink() and entry.name != _VIDEO_STAGING
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        required=True,
        type=Path,
        help="the finished run's directory (runs/<run_id>)",
    )
    parser.add_argument(
        "--expect-devices",
        required=True,
        type=int,
        help="the fewest distinct devices the run's workers must have leased",
    )
    args = parser.parse_args()

    manifest_path = args.run_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::error::cannot read the run manifest {manifest_path}: {exc}")
        return 1
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list):
        print(f"::error::{manifest_path} holds no scenario results")
        return 1

    violations = isolation_violations(
        scenarios, _subdirectory_names(args.run_dir), expect_devices=args.expect_devices
    )
    if violations:
        for violation in violations:
            print(f"::error::pool isolation: {violation}")
        return 1
    devices = sorted({str(s.get("device")) for s in scenarios if s.get("device")})
    print(
        f"pool isolation holds: {len(scenarios)} scenarios across {len(devices)} devices "
        f"({', '.join(devices)}), each in its own evidence dir"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
