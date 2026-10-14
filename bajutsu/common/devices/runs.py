"""Form a scenario's runs from its `runsOn` conditions, and assign each run its devices (BE-0452).

A *run* is one cell of the matrix: a scenario on one set of coordinates. Two kinds of condition
multiply runs: a list (`model`, `avd`, `browser.engine`) makes one run per listed value, and a range
on `os` or `apiLevel` makes one run per major version. A list or a range bounded on both sides names
runs that must happen, so a run no device fits fails; a range open on a side says where a scenario
applies, so a pool without a device in it records the run as not applicable.

Everything here is a pure function of the declared conditions and the device facts read before the
run, so the run counts and the devices each run takes are deterministic. Ties are broken by pool
order alone, never by whichever device answered first.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from bajutsu.common.devices.version import Version, VersionSpec

if TYPE_CHECKING:
    # Typing only: the facts readers reach the device CLIs, which the config's import of this
    # module must not pull in.
    from bajutsu.common.devices.facts import DeviceFacts

Outcome = Literal["run", "failed", "not-applicable"]

# The condition fields each platform names a model and a version range by.
_MODEL_FIELD = {"ios": "model", "android": "avd"}
_VERSION_FIELD = {"ios": "os", "android": "apiLevel"}


def _major_label(platform: str, major: int) -> str:
    return f"iOS {major}" if platform == "ios" else f"API {major}"


@dataclass(frozen=True)
class TargetRun:
    """One target's part of a run: its coordinates, the conditions a device must meet, and its outcome.

    `coordinates` are the values that tell this run apart from the target's other runs (a listed
    value, a major version), in the order they are shown. A run whose outcome is not `run` carries
    the reason that names the unmet condition and the devices it saw.
    """

    target: str
    platform: str
    # The model (iOS) or AVD (Android) a device must have; None when the run fixes none.
    model: str | None = None
    # The version range a device's release must fall in, and the major version of this run.
    spec: VersionSpec | None = None
    major: int | None = None
    # The web engine of this run; None on iOS and Android.
    engine: str | None = None
    coordinates: tuple[tuple[str, str], ...] = ()
    outcome: Outcome = "run"
    reason: str = ""
    # Whether the run needs a device of its eligible set, rather than any device of the pool.
    conditioned: bool = False

    @property
    def label(self) -> str:
        """The coordinates as a run name shows them: `iPhone SE (3rd generation) / iOS 17`."""
        return " / ".join(value for _field, value in self.coordinates)

    @property
    def key(self) -> tuple[object, ...]:
        """Two target runs with equal keys draw on the same eligible set."""
        return (self.platform, self.model, str(self.spec), self.major, self.engine)


def _available(devices: Sequence[DeviceFacts]) -> str:
    return ", ".join(d.describe for d in devices) or "no device"


def _matches_model(device: DeviceFacts, model: str | None) -> bool:
    if model is None:
        return True
    # A physical Android device has no AVD, so it never matches an `avd` condition.
    return device.avd == model if device.platform == "android" else device.model == model


def _in_run(device: DeviceFacts, spec: VersionSpec | None, major: int | None) -> bool:
    if spec is None:
        return True
    return spec.contains(device.version) and (major is None or device.version.major == major)


def _listed(value: str | list[str] | None) -> tuple[list[str | None], bool]:
    if isinstance(value, list):
        return list(value), True
    return [value], False


def form_target_runs(
    target: str,
    platform: str,
    conditions: Mapping[str, str | list[str]],
    devices: Sequence[DeviceFacts],
    *,
    engine: str | None = None,
) -> list[TargetRun]:
    """Every run one target's effective conditions make against the available *devices*.

    Args:
        target: The target's name.
        platform: The target's platform (`ios`, `android`, or `web`).
        conditions: The effective `runsOn` conditions, keyed by field (`model`, `os`, `avd`,
            `apiLevel`, `browser.engine`, `browser.version`); ranges are their text as written.
        devices: The available devices of the target's pool in pool order; on the web, one per
            engine a `browser.version` range is compared for, with the engine as its model.
        engine: The web engine the target is driven with when no engine is listed.

    Returns:
        The runs in order: listed values as written, then major versions ascending. An empty
        *conditions* makes one unconditioned run.
    """
    if platform == "web":
        return _web_runs(target, conditions, devices, engine)
    if platform not in _MODEL_FIELD or not conditions:
        return [TargetRun(target=target, platform=platform)]
    model_field, version_field = _MODEL_FIELD[platform], _VERSION_FIELD[platform]
    models, listed = _listed(conditions.get(model_field))
    raw_spec = conditions.get(version_field)
    spec = VersionSpec.parse(raw_spec) if isinstance(raw_spec, str) else None
    runs: list[TargetRun] = []
    for model in models:
        coords = ((model_field, model),) if listed and model is not None else ()
        base = TargetRun(target=target, platform=platform, model=model, spec=spec, conditioned=True)
        candidates = [d for d in devices if _matches_model(d, model)]
        if model is not None and not candidates:
            # A missing model is never "not applicable": only the version part of a run can be.
            runs.append(
                _with(
                    base,
                    coords,
                    "failed",
                    f'runsOn.{platform}.{model_field}  "{model}"  available: {_available(devices)}',
                )
            )
            continue
        if spec is None:
            runs.append(_with(base, coords))
            continue
        runs += _version_runs(base, coords, spec, candidates, version_field)
    return runs


def _with(
    base: TargetRun,
    coords: tuple[tuple[str, str], ...],
    outcome: Outcome = "run",
    reason: str = "",
    major: int | None = None,
) -> TargetRun:
    return TargetRun(
        target=base.target,
        platform=base.platform,
        model=base.model,
        spec=base.spec,
        major=major,
        engine=base.engine,
        coordinates=coords,
        outcome=outcome,
        reason=reason,
        conditioned=base.conditioned,
    )


def _version_runs(
    base: TargetRun,
    coords: tuple[tuple[str, str], ...],
    spec: VersionSpec,
    candidates: Sequence[DeviceFacts],
    version_field: str,
) -> list[TargetRun]:
    """One run per major version: the bounded sides' majors, then those the devices have on an open side."""
    bounded = set(spec.majors())
    open_sides = [alt for alt in spec.alternatives if not alt.bounded]
    in_open = [d for d in candidates if any(alt.contains(d.version) for alt in open_sides)]
    majors = sorted(bounded | {d.version.major for d in in_open})
    runs = [
        _with(base, (*coords, (version_field, _major_label(base.platform, m))), major=m)
        for m in majors
    ]
    if open_sides and not in_open:
        runs.append(
            _with(
                base,
                coords,
                "not-applicable",
                f'runsOn.{base.platform}.{version_field}  "{spec}"  available: '
                f"{_available(candidates)}",
            )
        )
    return runs


def _web_runs(
    target: str,
    conditions: Mapping[str, str | list[str]],
    browsers: Sequence[DeviceFacts],
    engine: str | None,
) -> list[TargetRun]:
    """One run per listed engine; a version range narrows each one instead of multiplying it."""
    engines, listed = _listed(conditions.get("browser.engine") or engine)
    raw_spec = conditions.get("browser.version")
    spec = VersionSpec.parse(raw_spec) if isinstance(raw_spec, str) else None
    runs: list[TargetRun] = []
    for name in engines:
        coords = (("browser.engine", name),) if listed and name is not None else ()
        run = TargetRun(target=target, platform="web", spec=spec, engine=name, coordinates=coords)
        if spec is None:
            runs.append(run)
            continue
        launched = next((b for b in browsers if b.model == name), None)
        if launched is None:
            raise ValueError(f"no browser version was read for engine {name!r}")
        if spec.contains(launched.version):
            runs.append(run)
            continue
        # Each side of a union keeps its own rule: a bounded side that excludes the launched version
        # named a run that must happen, so it fails; an open side alone makes the run not
        # applicable. With both kinds, the failure wins, as it does across a combination's targets.
        outcome: Outcome = (
            "failed" if any(alt.bounded for alt in spec.alternatives) else "not-applicable"
        )
        runs.append(
            TargetRun(
                target=target,
                platform="web",
                spec=spec,
                engine=name,
                coordinates=coords,
                outcome=outcome,
                reason=f'runsOn.web.browser.version  "{spec}"  launched: {launched.describe}',
            )
        )
    return runs


def eligible_set(
    run: TargetRun, devices: Sequence[DeviceFacts], demand: int = 1
) -> list[DeviceFacts] | None:
    """The devices *run* may take, all of one model and release, or None when too few fit.

    Keeps the devices that meet the run's conditions; then, unless the run fixes a model, the devices
    of the first model in pool order that still has *demand* devices; then the devices at the newest
    release that still has that many. Every member then observes the same model and release, so any
    idle one may take the run. A run with no condition takes any device of the pool.

    Args:
        run: The target run to assign.
        devices: The devices still free for it, in pool order.
        demand: How many devices one combination draws from this set at once.
    """
    if not run.conditioned:
        return list(devices) if len(devices) >= demand else None
    kept = [d for d in devices if _matches_model(d, run.model) and _in_run(d, run.spec, run.major)]
    by_model: dict[str, list[DeviceFacts]] = {}
    for d in kept:
        by_model.setdefault(d.model, []).append(d)
    model = next((m for m, group in by_model.items() if len(group) >= demand), None)
    if model is None:
        return None
    by_release: dict[Version, list[DeviceFacts]] = {}
    for d in by_model[model]:
        by_release.setdefault(d.version, []).append(d)
    newest = max((v for v, group in by_release.items() if len(group) >= demand), default=None)
    return by_release[newest] if newest is not None else None


@dataclass(frozen=True)
class Participant:
    """One device a scenario holds: a target, or a device group whose members share one device.

    `pool` names the device pool it draws from; participants on one pool never share a device
    unless they are one participant. `runs` are the target runs its effective conditions form.
    """

    targets: tuple[str, ...]
    pool: str
    runs: tuple[TargetRun, ...]


@dataclass(frozen=True)
class Combination:
    """One run of a scenario: a target run per participant, its outcome, and the devices it takes.

    `devices` maps each participant's first target to the udids of its eligible set; it is empty
    unless the outcome is `run`. A participant whose pool has no device facts (a web lane), or an
    unconditioned one whose pool no condition narrowed in this combination, is left out of
    `devices` and takes any lane, as today.
    """

    runs: tuple[TargetRun, ...]
    outcome: Outcome
    reason: str = ""
    devices: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """The run's coordinates across every target, as its name shows them."""
        multi = len(self.runs) > 1
        parts = [f"{r.target}: {r.label}" if multi else r.label for r in self.runs if r.coordinates]
        return ", ".join(parts)

    @property
    def coordinates(self) -> dict[str, dict[str, str]]:
        """The coordinates per target, keyed by condition field, for the manifest and the history."""
        return {r.target: dict(r.coordinates) for r in self.runs if r.coordinates}


def combine(
    participants: Sequence[Participant], pools: Mapping[str, Sequence[DeviceFacts]]
) -> list[Combination]:
    """Every combination of the participants' runs, each with its outcome and eligible sets.

    A combination fails when any of its target runs fails, and is not applicable when any is;
    otherwise each set of participants on one pool with equal coordinates takes an eligible set
    sized to their count. Conditioned sets are claimed first, in participant order, then the
    unconditioned ones take what is left, so two participants never share a device.

    Args:
        participants: The scenario's participants in declared order.
        pools: The device facts of each pool, in pool order; a pool absent here (a web lane) is not
            narrowed.
    """
    combos: list[Combination] = []
    for runs in itertools.product(*(p.runs for p in participants)):
        failed = [r for r in runs if r.outcome == "failed"]
        skipped = [r for r in runs if r.outcome == "not-applicable"]
        if failed:
            combos.append(Combination(runs, "failed", "; ".join(r.reason for r in failed)))
        elif skipped:
            combos.append(Combination(runs, "not-applicable", "; ".join(r.reason for r in skipped)))
        else:
            combos.append(_assign(participants, runs, pools))
    return combos


def _assign(
    participants: Sequence[Participant],
    runs: tuple[TargetRun, ...],
    pools: Mapping[str, Sequence[DeviceFacts]],
) -> Combination:
    groups: dict[tuple[object, ...], list[int]] = {}
    for i, (p, r) in enumerate(zip(participants, runs, strict=True)):
        if p.pool in pools:
            groups.setdefault((p.pool, r.key), []).append(i)
    claimed: dict[str, set[str]] = {}
    devices: dict[str, tuple[str, ...]] = {}
    # Conditioned sets first, so a participant that may take any device never takes the only one a
    # condition fits.
    order = sorted(groups.values(), key=lambda members: not runs[members[0]].conditioned)
    for members in order:
        pool = participants[members[0]].pool
        run = runs[members[0]]
        if not run.conditioned and not claimed.get(pool):
            # Nothing on this pool is narrowed, so the participant takes its own lanes as today;
            # recording the whole pool would split its runs from those of a scenario whose pool
            # was never read.
            continue
        free = [d for d in pools[pool] if d.udid not in claimed.get(pool, set())]
        chosen = eligible_set(run, free, len(members))
        if chosen is None:
            what = run.label or "any device"
            return Combination(
                runs,
                "failed",
                f"target {run.target!r}: no {len(members)} available device(s) fit "
                f"{what} with the same model and release; available: {_available(free)}",
            )
        claimed.setdefault(pool, set()).update(d.udid for d in chosen)
        for i in members:
            devices[participants[i].targets[0]] = tuple(d.udid for d in chosen)
    return Combination(runs, "run", devices=devices)


def first_run(
    target: str,
    platform: str,
    conditions: Mapping[str, str | list[str]],
    devices: Sequence[DeviceFacts],
    *,
    engine: str | None = None,
) -> tuple[TargetRun, DeviceFacts | None]:
    """The run `record`, `crawl`, and `repl` take, and the device it runs on.

    These commands never multiply runs: they take the first listed value and the newest major
    version the pool has within the range. A web run has no device to pick, so its device is None.

    Raises:
        LookupError: No available device fits, named with the condition and the devices seen.
    """
    if platform == "web":
        run = _web_runs(target, conditions, devices, engine)[0]
        if run.outcome != "run":
            raise LookupError(run.reason)
        return run, None
    if platform not in _MODEL_FIELD or not conditions:
        return TargetRun(target=target, platform=platform), (devices[0] if devices else None)
    model_field, version_field = _MODEL_FIELD[platform], _VERSION_FIELD[platform]
    models, _ = _listed(conditions.get(model_field))
    model = models[0]
    raw_spec = conditions.get(version_field)
    spec = VersionSpec.parse(raw_spec) if isinstance(raw_spec, str) else None
    fitting = [d for d in devices if _matches_model(d, model) and _in_run(d, spec, None)]
    if not fitting:
        named = [f'{model_field} "{model}"'] if model is not None else []
        named += [f'{version_field} "{spec}"'] if spec is not None else []
        raise LookupError(
            f"runsOn.{platform} {', '.join(named)}: no available device fits; available: "
            f"{_available(devices)}"
        )
    major = max(d.version.major for d in fitting) if spec is not None else None
    run = TargetRun(
        target=target, platform=platform, model=model, spec=spec, major=major, conditioned=True
    )
    chosen = eligible_set(run, devices)
    assert chosen  # `fitting` holds a device of this major, so the set is never empty
    return run, chosen[0]


# How a declared listing names the runs an open range forms, which depend on the pool.
PER_AVAILABLE_MAJOR = "one run per available major version"


def declared_runs(
    platform: str, conditions: Mapping[str, str | list[str]], *, engine: str | None = None
) -> list[str]:
    """The runs *conditions* name before any device is read, as `doctor` lists them with no pool.

    A list names one run per value and a bounded range one per major version it covers, so those
    are listed as the runs they form. An open range's runs depend on the major versions the pool
    has, so it is listed as one run per available major version. A run with no coordinates (no
    condition, or a single value alone) is listed as an empty label.
    """
    if platform == "web":
        engines, listed = _listed(conditions.get("browser.engine") or engine)
        return [name if listed and name is not None else "" for name in engines]
    if platform not in _MODEL_FIELD or not conditions:
        return [""]
    models, listed = _listed(conditions.get(_MODEL_FIELD[platform]))
    version_field = _VERSION_FIELD[platform]
    raw_spec = conditions.get(version_field)
    spec = VersionSpec.parse(raw_spec) if isinstance(raw_spec, str) else None
    labels: list[str] = []
    for model in models:
        prefix = [model] if listed and model is not None else []
        if spec is None:
            labels.append(" / ".join(prefix))
            continue
        labels += [" / ".join([*prefix, _major_label(platform, m)]) for m in spec.majors()]
        if any(not alt.bounded for alt in spec.alternatives):
            labels.append(
                " / ".join([*prefix, f'{PER_AVAILABLE_MAJOR} in {version_field} "{spec}"'])
            )
    return labels


def unpack_conditions(
    value: Mapping[str, object],
) -> dict[str, str | list[str]]:
    """A `runsOn` block's condition fields, flattened to the keys `form_target_runs` reads.

    Device state (`locale`, `seedPhotos`) and how a target is driven (`kind`, `emulate`) are not
    conditions; an integer range (`apiLevel: 34`) becomes its text, as the target's model reads it.
    """
    out: dict[str, str | list[str]] = {}
    for key, raw in value.items():
        if key in ("locale", "seedPhotos", "kind", "emulate"):
            continue
        if isinstance(raw, Mapping):
            out.update({f"{key}.{sub}": _text(v) for sub, v in raw.items() if v is not None})
        elif raw is not None:
            out[key] = _text(raw)
    return out


def _text(value: object) -> str | list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return str(value)


def coordinates_key(coordinates: Mapping[str, Mapping[str, str]]) -> str:
    """One run's coordinates as a canonical string: `app: model=iPhone SE, os=iOS 17`.

    Targets and fields are sorted, so equal coordinates always spell the same key. Empty for a run
    no condition formed.
    """
    return "; ".join(
        f"{target}: " + ", ".join(f"{k}={v}" for k, v in sorted(fields.items()))
        for target, fields in sorted(coordinates.items())
    )


# The key of a stored run whose results ran on more than one set of coordinates: no single key
# speaks for it, and it must not merge with the coordinate-free history of every other run.
MIXED_COORDINATES = "*"


def coordinates_from_manifest(manifest: Mapping[str, object]) -> str:
    """The coordinates every scenario of a stored run shares, as `coordinates_key` spells them.

    The flakiness history keys a run by them beside the device OS, so a layout failure on one model
    never reads as a flaky scenario (BE-0452). A run whose scenarios ran on different coordinates
    keys as `MIXED_COORDINATES`. A manifest from before coordinates were recorded reads as empty:
    no run before them could carry a condition, so its history continues under the new key.
    """
    scenarios = manifest.get("scenarios")
    keys = {
        coordinates_key(raw if isinstance(raw := s.get("run_coordinates"), Mapping) else {})
        for s in (scenarios if isinstance(scenarios, list) else [])
        if isinstance(s, Mapping)
    }
    if len(keys) > 1:
        return MIXED_COORDINATES
    return next(iter(keys), "")
