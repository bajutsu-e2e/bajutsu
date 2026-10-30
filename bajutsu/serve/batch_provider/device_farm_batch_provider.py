"""The AWS Device Farm batch provider: package, schedule one run, collect the verdict."""

from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml

from bajutsu.common.capability.worker_capability import DEVICEFARM_HOST
from bajutsu.common.cloud.devicefarm import (
    APP_UPLOAD_TYPE,
    REQUIREMENTS_TXT,
    DeviceFarmClient,
    DeviceFarmError,
    Interrupt,
    Platform,
    Transfer,
    Verdict,
    build_package,
    collect_run,
    device_selection_for,
    render_test_spec,
    submit_and_collect,
)
from bajutsu.common.config.schema._merge import merge_defaults
from bajutsu.common.config.schema._templates import expand_extends
from bajutsu.common.config.schema.platform import PLATFORM_SCHEMAS
from bajutsu.common.config.schema.platform._shared import destination_problems
from bajutsu.common.devices.runs import unpack_conditions

from ._package_layout import _PackageLayout
from .batch_checkpoint import BatchCheckpoint
from .batch_lifecycle_hook import BatchContext, BatchLifecycleHook
from .batch_request import BatchRequest
from .xctest_runner import xctest_runner

_logger = logging.getLogger(__name__)

# Where a job's bundle sits inside a package composed from the source tree (BE-0448). Nested rather
# than packed at the root, so a bundle that carries its own `tests/` or `pyproject.toml` can never
# collide with the source tree's entries the test spec installs.
BUNDLE_ARCNAME = "bundle"
# The parts of the source tree a package needs: what the test spec `pip install`s (hatchling refuses
# to build without the readme and license files pyproject.toml names), and the `tests/` Device Farm's
# APPIUM_PYTHON_TEST_PACKAGE validation looks for at the root.
_SOURCE_ENTRIES = ("pyproject.toml", "DESIGN.md", "LICENSE", "NOTICE", "tests", "bajutsu")
# The worker file every run in a package takes (BE-0465). Device Farm installs the app upload before
# the test spec starts, so a `devicefarm-host` environment has the run launch that build instead of
# installing `subject.path`, on either platform. Its one driver is the backend the test spec runs.
_HOST_WORKER_ARCNAME = f"{DEVICEFARM_HOST}.worker.yaml"
_HOST_DRIVER: dict[Platform, str] = {"android": "adb", "ios": "xcuitest"}
# The layout `_package_entries` assumes when a caller names none: no runner build, so APPIUM_PYTHON.
_NO_RUNNER = _PackageLayout(runner=None)


def _check_launch_env_collisions(
    scenario_path: Path,
    injected: dict[str, str],
) -> None:
    """Raise loudly if a scenario's `preconditions.subject.launch.env` key collides with an injected one.

    On-device merge order is ``{**target_env, **preconditions.subject.launch.env}`` so a scenario key
    silently wins over the injected value — catching this at submit time makes the conflict visible
    before the run starts (BE-0435 Thread-8 decision).
    """
    text = scenario_path.read_text(encoding="utf-8")
    scenarios = yaml.safe_load(text)
    if not isinstance(scenarios, list):
        return
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        preconditions = scenario.get("preconditions") or {}
        if not isinstance(preconditions, dict):
            continue
        subject = preconditions.get("subject") or {}
        launch = subject.get("launch") if isinstance(subject, dict) else None
        launch_env = (launch.get("env") if isinstance(launch, dict) else None) or {}
        for key in launch_env:
            if key in injected:
                name = scenario.get("name", "<unnamed>")
                raise ValueError(
                    f"launch env collision: key {key!r} injected by a BatchLifecycleHook "
                    f"is also set in scenario {name!r} preconditions "
                    f"({scenario_path.name}). The scenario value wins on-device, "
                    f"silently overriding the injected one."
                )


def _check_no_conditions(config_path: Path, target: str, *, root: Path | None = None) -> None:
    """Raise when the job's target declares a `runsOn` condition (BE-0452).

    A Device Farm device is reserved by the worker, not read from a pool, so no condition can be
    matched against it; the worker refuses the job before it submits anything rather than letting
    the condition go ignored. `locale` and `seedPhotos` are device state, so they stay allowed. A
    scenario declares no condition since BE-0469, so the target is the one place to look. A config
    file the job does not carry declares nothing here; the run on the device reports it missing. A
    template file the target extends must be in the bundle, since its `runsOn` cannot be known
    otherwise: a missing one fails here. *root* is the bundle root a template file must stay inside,
    None meaning the config's directory.
    """
    config = (
        yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else None
    ) or {}
    targets = config.get("targets") if isinstance(config, dict) else None
    defaults = config.get("defaults") if isinstance(config, dict) else None
    raw = targets.get(target) if isinstance(targets, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    # The loader's own expansion and merge, so a team-wide `defaults.platforms.<platform>.runsOn`
    # or a template's `runsOn` (BE-0471) reaches the target as it does at load, and a `null` clears it.
    layers = expand_extends(
        target,
        raw,
        config.get("templates") if isinstance(config, dict) else None,
        config_path=config_path,
        root=root,
    )
    merged = merge_defaults(defaults if isinstance(defaults, dict) else None, *layers)
    runs_on = merged.get("runsOn")
    declared: dict[str, object] = runs_on if isinstance(runs_on, dict) else {}
    # A flat key from before BE-0469 holds no destination block, so `unpack_conditions` would skip
    # it; it fails here, with its new path, rather than reaching the reserved device.
    platform = merged.get("platform")
    schema = PLATFORM_SCHEMAS.get(platform) if isinstance(platform, str) else None
    if schema is not None and (moved := destination_problems(schema.runs_on, declared)):
        raise ValueError(f"target {target!r}: {'; '.join(moved)}")
    found = [f"target {target!r} runsOn.{name}" for name in unpack_conditions(declared)]
    if found:
        raise ValueError(
            f"{', '.join(found)}: a Device Farm device has no pool to match a runsOn condition "
            "against; remove the condition, or run the job locally"
        )


class DeviceFarmBatchProvider:
    """The AWS Device Farm concrete: package the project, schedule one run for one device, collect.

    Reserves a single device per run through a ``deviceSelectionConfiguration`` (`maxDevices` one)
    rather than a static device pool, so the Bajutsu-side budget `K` alone governs how many devices
    are held at once (BE-0336). The boto3 client and the presigned-URL transfer are injected so this
    logic runs against the in-memory fake in tests; production wires the real ones.

    `hooks` are lifecycle hooks (BE-0435) wired at process start only — never from a request,
    config field, or BatchRequest field.

    With a `source_root`, each job's package is composed per job (BE-0448): the `_SOURCE_ENTRIES`
    of the source tree, plus the job's bundle directory under `BUNDLE_ARCNAME`. Nothing is written
    into either tree. Without one, the bundle directory alone is packed at the root, the shape the
    in-memory tests drive. Either way, an iOS job's package is an XCTEST_UI one: the runner app its
    target names sits at the package root, and the rest moves under `XCTEST_PACKAGE_DIR` (BE-0465).
    """

    def __init__(
        self,
        *,
        client: DeviceFarmClient,
        transfer: Transfer,
        project_arn: str,
        sleep: Callable[[float], None] = time.sleep,
        hooks: Sequence[BatchLifecycleHook] = (),
        source_root: Path | None = None,
    ) -> None:
        self._client = client
        self._transfer = transfer
        self._project_arn = project_arn
        self._sleep = sleep
        self._hooks = list(hooks)
        self._source_root = source_root

    def submit(
        self,
        request: BatchRequest,
        *,
        work_dir: Path,
        dest: Path,
        checkpoint: BatchCheckpoint | None = None,
        job_id: str = "",
        interrupt: Callable[[], Interrupt | None] | None = None,
    ) -> Verdict:
        """Render the one-scenario spec, package `work_dir`, submit the run, and collect the verdict.

        `work_dir` is the job's bundle directory: `request.scenario` and `request.config` are
        relative to it, and so is `request.app_path` unless it is absolute (BE-0448).

        A `checkpoint` carrying a run ARN means this job was already scheduled before a restart: resume
        polling that run and collect it — no re-upload, no reschedule — so the in-flight run and its
        reserved device are not orphaned (BE-0336 Unit 5). The run is named after `job_id`, so an
        operator can find the run of a job on Device Farm (BE-0448). `interrupt` is asked before the
        run is scheduled and before every poll, and an answer raises `RunInterrupted` (BE-0448).

        `hooks` (set at construction) run lifecycle steps on the worker: `before_submit` may inject
        launch environment variables into the packaged config; `after_run` runs in a ``finally`` in
        reverse hook order so teardown always mirrors setup (BE-0435). No `after_run` failure is
        dropped: each is logged, except that when no verdict was collected the last-registered
        hook that raised propagates its exception instead — so a teardown failure never discards
        a collected run.
        """
        resume_arn = checkpoint.load() if checkpoint is not None else None
        ctx = BatchContext(request=request, work_dir=work_dir, job_id=job_id)
        verdict: Verdict | None = None

        try:
            if resume_arn is not None:
                # Checkpoint resume: the run is already scheduled and uploaded — skip before_submit
                # and config merge, but still run after_run so teardown fires (BE-0435 Thread-7).
                verdict = collect_run(
                    self._client,
                    self._transfer,
                    run_arn=resume_arn,
                    dest=dest,
                    sleep=self._sleep,
                    interrupt=interrupt,
                )
            else:
                _check_no_conditions(work_dir / request.config, request.target, root=work_dir)
                for hook in self._hooks:
                    hook.before_submit(ctx)

                app_path = _application(request, work_dir)
                # An iOS runner goes up as an XCTEST_UI package, which Device Farm re-signs for the
                # device it reserves (BE-0465).
                layout = _PackageLayout(
                    runner=(
                        xctest_runner(work_dir, request.config, request.target)
                        if request.platform == "ios"
                        else None
                    ),
                    bundle=f"{BUNDLE_ARCNAME}/" if self._source_root is not None else "",
                )
                config_arc = f"{layout.bundle}{request.config}"
                extra_texts: dict[str, str] = {
                    layout.place("requirements.txt"): REQUIREMENTS_TXT,
                    layout.place(_HOST_WORKER_ARCNAME): (
                        f"version: 1\nenvironment: {DEVICEFARM_HOST}\n"
                        f"drivers: [{_HOST_DRIVER[request.platform]}]\n"
                    ),
                }
                # The binary travels as the run's app upload, which Device Farm installs before the
                # run, so a copy inside the bundle would only double the package. So does the runner
                # build: Device Farm hands the test spec a re-signed copy of it.
                left_out = [app_path, *(layout.runner.build_files if layout.runner else ())]
                exclude_arcnames = {
                    layout.place(arc)
                    for path in left_out
                    for arc in _inside(path, work_dir, layout.bundle)
                }
                if ctx.launch_env:
                    extra_texts[layout.place(config_arc)] = _with_launch_env(
                        request, work_dir, ctx.launch_env
                    )
                    exclude_arcnames.add(layout.place(config_arc))

                spec = render_test_spec(
                    [f"{layout.bundle}{request.scenario}"],
                    target=request.target,
                    config=config_arc,
                    platform=request.platform,
                    worker_config=_HOST_WORKER_ARCNAME,
                    xctest_runner=layout.xctest_runner,
                )
                with tempfile.TemporaryDirectory() as staging_name:
                    staging = Path(staging_name)
                    spec_path = staging / "testspec.yml"
                    spec_path.write_text(spec, encoding="utf-8")
                    package_zip = staging / "devicefarm-package.zip"
                    build_package(
                        self._package_entries(work_dir, layout),
                        package_zip,
                        extra_texts=extra_texts,
                        exclude_arcnames=exclude_arcnames,
                    )
                    verdict = submit_and_collect(
                        self._client,
                        self._transfer,
                        project_arn=self._project_arn,
                        device_selection=device_selection_for(request.platform),
                        app_path=app_path,
                        package_zip=package_zip,
                        spec_yaml=spec_path,
                        dest=dest,
                        app_upload_type=APP_UPLOAD_TYPE[request.platform],
                        test_type=layout.test_type,
                        run_name=job_id or "bajutsu",
                        sleep=self._sleep,
                        on_scheduled=(checkpoint.save if checkpoint is not None else None),
                        interrupt=interrupt,
                    )
        finally:
            hook_errors: list[tuple[BatchLifecycleHook, Exception]] = []
            for hook in reversed(self._hooks):
                try:
                    hook.after_run(ctx, verdict)
                except Exception as exc:
                    hook_errors.append((hook, exc))
            # A collected verdict means the run and its artifacts exist, so a teardown failure is
            # logged rather than raised: raising would replace the `return` and discard both.
            raise_first = bool(hook_errors) and verdict is None
            for failed_hook, err in hook_errors[1:] if raise_first else hook_errors:
                _logger.error(
                    "BatchLifecycleHook %s.after_run failed during teardown of job %r",
                    type(failed_hook).__name__,
                    job_id,
                    exc_info=err,
                )
            if raise_first:
                raise hook_errors[0][1]

        assert verdict is not None  # exceptions take the other path
        return verdict

    def _package_entries(
        self, work_dir: Path, layout: _PackageLayout = _NO_RUNNER
    ) -> list[tuple[Path, str]]:
        """The `(source, arcname)` pairs of one job's package (see the class docstring)."""
        if self._source_root is None:
            entries = [(work_dir, ".")]
        else:
            entries = [
                *((self._source_root / name, name) for name in _SOURCE_ENTRIES),
                (work_dir, BUNDLE_ARCNAME),
            ]
        placed = [(source, layout.place(arc)) for source, arc in entries]
        if layout.runner is None:
            return placed
        # Device Farm finds the runner app at the package root.
        return [(layout.runner.app, layout.runner.app.name), *placed]


def _with_launch_env(request: BatchRequest, work_dir: Path, launch_env: dict[str, str]) -> str:
    """The job's config text with *launch_env* overlaid on its target's `subject.launch.env`.

    The overlay lands where BE-0452 and BE-0470 put a target's launch environment.

    Raises:
        ValueError: A scenario's `preconditions.subject.launch.env` sets an injected key, which would
            silently win on the device (see `_check_launch_env_collisions`).
    """
    config_path = work_dir / request.config
    config_data: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    _check_launch_env_collisions(scenario_path=work_dir / request.scenario, injected=launch_env)
    targets = config_data.setdefault("targets", {})
    target = targets.setdefault(request.target, {})
    launch = target.setdefault("subject", {}).setdefault("launch", {})
    # The injected value wins over a pre-existing config value on collision.
    launch["env"] = {**launch.get("env", {}), **launch_env}
    return yaml.dump(config_data, allow_unicode=True)


def _application(request: BatchRequest, work_dir: Path) -> Path:
    """The binary *request* names, resolved against the job's bundle directory (BE-0448).

    Raises:
        DeviceFarmError: No file is there, which would otherwise surface as an opaque upload error.
    """
    app = Path(request.app_path)
    resolved = app if app.is_absolute() else work_dir / app
    if not resolved.is_file():
        raise DeviceFarmError(
            f"application {request.app_path!r} is not in this job's bundle ({resolved})"
        )
    return resolved


def _inside(path: Path, root: Path, prefix: str) -> list[str]:
    """*path*'s arcname in a package that holds *root* under *prefix*, or none when outside it."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return []
    return [f"{prefix}{rel.as_posix()}"]
