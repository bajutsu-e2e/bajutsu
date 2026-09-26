"""The AWS Device Farm batch provider: package, schedule one run, collect the verdict."""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml

from bajutsu.common.cloud.devicefarm import (
    APP_UPLOAD_TYPE,
    REQUIREMENTS_TXT,
    DeviceFarmClient,
    Transfer,
    Verdict,
    build_package,
    collect_run,
    device_selection_for,
    render_test_spec,
    submit_and_collect,
)

from .batch_checkpoint import BatchCheckpoint
from .batch_lifecycle_hook import BatchContext, BatchLifecycleHook
from .batch_request import BatchRequest


def _check_launch_env_collisions(
    scenario_path: Path,
    injected: dict[str, str],
) -> None:
    """Raise loudly if any scenario preconditions.launchEnv key collides with an injected key.

    On-device merge order is ``{**target_env, **preconditions.launch_env}`` so a scenario key
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
        launch_env = preconditions.get("launchEnv") or {}
        for key in launch_env:
            if key in injected:
                name = scenario.get("name", "<unnamed>")
                raise ValueError(
                    f"launchEnv collision: key {key!r} injected by a BatchLifecycleHook "
                    f"is also set in scenario {name!r} preconditions "
                    f"({scenario_path.name}). The scenario value wins on-device, "
                    f"silently overriding the injected one."
                )


class DeviceFarmBatchProvider:
    """The AWS Device Farm concrete: package the project, schedule one run for one device, collect.

    Reserves a single device per run through a ``deviceSelectionConfiguration`` (`maxDevices` one)
    rather than a static device pool, so the Bajutsu-side budget `K` alone governs how many devices
    are held at once (BE-0336). The boto3 client and the presigned-URL transfer are injected so this
    logic runs against the in-memory fake in tests; production wires the real ones.

    `hooks` are server-side lifecycle hooks (BE-0435) wired at process start only — never from a
    request, config field, or BatchRequest field.
    """

    def __init__(
        self,
        *,
        client: DeviceFarmClient,
        transfer: Transfer,
        project_arn: str,
        sleep: Callable[[float], None] = time.sleep,
        hooks: Sequence[BatchLifecycleHook] = (),
    ) -> None:
        self._client = client
        self._transfer = transfer
        self._project_arn = project_arn
        self._sleep = sleep
        self._hooks = list(hooks)

    def submit(
        self,
        request: BatchRequest,
        *,
        work_dir: Path,
        dest: Path,
        checkpoint: BatchCheckpoint | None = None,
        job_id: str = "",
    ) -> Verdict:
        """Render the one-scenario spec, package `work_dir`, submit the run, and collect the verdict.

        A `checkpoint` carrying a run ARN means this job was already scheduled before a restart: resume
        polling that run and collect it — no re-upload, no reschedule — so the in-flight run and its
        reserved device are not orphaned (BE-0336 Unit 5).

        `hooks` (set at construction) run server-side lifecycle steps: `before_submit` may inject
        launch environment variables into the packaged config; `after_run` runs in a ``finally`` in
        reverse hook order so teardown always mirrors setup (BE-0435).
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
                )
            else:
                for hook in self._hooks:
                    hook.before_submit(ctx)

                extra_texts: dict[str, str] = {"requirements.txt": REQUIREMENTS_TXT}
                exclude_arcnames: set[str] = set()

                if ctx.launch_env:
                    config_path = work_dir / request.config
                    config_data: dict[str, Any] = yaml.safe_load(
                        config_path.read_text(encoding="utf-8")
                    ) or {}

                    # Collision guard: scenario preconditions.launchEnv wins on-device, so a key
                    # collision means the injected value is silently overridden — fail loudly here.
                    _check_launch_env_collisions(
                        scenario_path=work_dir / request.scenario,
                        injected=ctx.launch_env,
                    )

                    targets = config_data.setdefault("targets", {})
                    target = targets.setdefault(request.target, {})
                    # ctx.launch_env wins over pre-existing config values on collision.
                    target["launchEnv"] = {
                        **target.get("launchEnv", {}),
                        **ctx.launch_env,
                    }
                    extra_texts[request.config] = yaml.dump(config_data, allow_unicode=True)
                    exclude_arcnames.add(request.config)

                spec = render_test_spec(
                    [request.scenario],
                    target=request.target,
                    config=request.config,
                    platform=request.platform,
                )
                with tempfile.TemporaryDirectory() as staging_name:
                    staging = Path(staging_name)
                    spec_path = staging / "testspec.yml"
                    spec_path.write_text(spec, encoding="utf-8")
                    package_zip = staging / "devicefarm-package.zip"
                    build_package(
                        [(work_dir, ".")],
                        package_zip,
                        extra_texts=extra_texts,
                        exclude_arcnames=exclude_arcnames,
                    )
                    verdict = submit_and_collect(
                        self._client,
                        self._transfer,
                        project_arn=self._project_arn,
                        device_selection=device_selection_for(request.platform),
                        app_path=Path(request.app_path),
                        package_zip=package_zip,
                        spec_yaml=spec_path,
                        dest=dest,
                        app_upload_type=APP_UPLOAD_TYPE[request.platform],
                        sleep=self._sleep,
                        on_scheduled=(checkpoint.save if checkpoint is not None else None),
                    )
        finally:
            for hook in reversed(self._hooks):
                hook.after_run(ctx, verdict)

        assert verdict is not None  # exceptions take the other path
        return verdict
