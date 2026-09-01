"""Provider-generic batch-cloud seam serve dispatches one scenario through (BE-0336).

A *batch* cloud runs a deterministic Bajutsu run on a remote host that already holds a reserved
device, then hands back the ``runs/`` tree — the verdict still comes from Bajutsu's own
``manifest.json``, never the cloud's own classification (prime directive 1). serve's worker branches
to this seam for a cloud-batch job (`Job.batch` set) exactly where it would otherwise spawn a local
subprocess, so a job runs on a cloud device without the runner, drivers, or `run`/CI verdict path
changing.

The seam is deliberately provider-agnostic: `BatchProvider` names *what* (submit one scenario, return
its verdict), and a `kind` selects the concrete *how* from a fail-closed registry. AWS Device Farm is
the first concrete (`DeviceFarmBatchProvider`); the public dispatch surface never names it, so a
second provider is a new registry entry rather than an API change. The real AWS client and transfer
are injected, so the provider's own logic is unit-tested against the same in-memory fakes the CLI
submitter uses.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bajutsu.common.cloud.devicefarm import (
    APP_UPLOAD_TYPE,
    REQUIREMENTS_TXT,
    DeviceFarmClient,
    Platform,
    Transfer,
    Verdict,
    build_package,
    collect_run,
    device_selection_for,
    render_test_spec,
    submit_and_collect,
)


@dataclass(frozen=True)
class BatchRequest:
    """One scenario's worth of work for a batch cloud, with the target it runs against.

    `provider` selects the concrete `BatchProvider` from the registry; `scenario`, `target`, and
    `config` are the paths/name as they appear inside the packaged project; `platform` picks the
    device family (and, for Device Farm, the app upload type and the platform filter); `app_path` is
    the app artifact (an Android `.apk` or an iOS `.ipa`) to install on the reserved device.
    """

    provider: str
    scenario: str
    target: str
    config: str
    platform: Platform
    app_path: str


class BatchCheckpoint(Protocol):
    """A durable record of a cloud-batch run's scheduled ARN, so a re-leased worker resumes it (Unit 5).

    A batch run's poll can span the 150-minute hard cap. On the hosted DB backend a worker persists the
    run ARN through this seam the moment the run is scheduled; a worker that picks the same job up again
    after a restart loads it and resumes polling that run instead of resubmitting — the long poll
    survives a serve restart rather than orphaning the run (and its reserved device).
    """

    def load(self) -> str | None:
        """The scheduled run ARN persisted for this job, or None if it is not yet scheduled."""

    def save(self, run_arn: str) -> None:
        """Persist the scheduled run ARN so a restart resumes polling it."""


class BatchProvider(Protocol):
    """A batch cloud serve can run one scenario on: submit it, wait, and return Bajutsu's verdict.

    The verdict is read from the downloaded ``manifest.json`` tree (left under `dest`), so it is
    Bajutsu's own pass/fail, never the cloud's run classification — the provider stays off the
    `run`/CI verdict path.
    """

    def submit(
        self,
        request: BatchRequest,
        *,
        work_dir: Path,
        dest: Path,
        checkpoint: BatchCheckpoint | None = None,
    ) -> Verdict:
        """Package `work_dir`, run `request.scenario` on the cloud, download artifacts under `dest`.

        With a `checkpoint`, persist the scheduled run so a re-leased worker resumes polling it after a
        restart rather than resubmitting (BE-0336 Unit 5); None keeps the best-effort, non-durable path.
        """
        raise NotImplementedError


_PROVIDERS: dict[str, BatchProvider] = {}


def register(kind: str, provider: BatchProvider) -> None:
    """Register `provider` under `kind` so a `Job.batch` naming that `kind` dispatches to it."""
    _PROVIDERS[kind] = provider


def resolve(kind: str) -> BatchProvider:
    """Return the provider registered under `kind`, failing closed on an unknown one.

    Mirrors the mailbox / device-provider registries: an unknown `kind` is a clean config error raised
    here rather than a silent no-op that would let a cloud-batch job vanish.
    """
    if kind not in _PROVIDERS:
        allowed = ", ".join(repr(k) for k in _PROVIDERS) or "(none)"
        raise ValueError(f"unknown batch provider {kind!r}: registered kinds are {allowed}")
    return _PROVIDERS[kind]


class DeviceFarmBatchProvider:
    """The AWS Device Farm concrete: package the project, schedule one run for one device, collect.

    Reserves a single device per run through a ``deviceSelectionConfiguration`` (`maxDevices` one)
    rather than a static device pool, so the Bajutsu-side budget `K` alone governs how many devices
    are held at once (BE-0336). The boto3 client and the presigned-URL transfer are injected so this
    logic runs against the in-memory fake in tests; production wires the real ones.
    """

    def __init__(
        self,
        *,
        client: DeviceFarmClient,
        transfer: Transfer,
        project_arn: str,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._transfer = transfer
        self._project_arn = project_arn
        self._sleep = sleep

    def submit(
        self,
        request: BatchRequest,
        *,
        work_dir: Path,
        dest: Path,
        checkpoint: BatchCheckpoint | None = None,
    ) -> Verdict:
        """Render the one-scenario spec, package `work_dir`, submit the run, and collect the verdict.

        A `checkpoint` carrying a run ARN means this job was already scheduled before a restart: resume
        polling that run and collect it — no re-upload, no reschedule — so the in-flight run and its
        reserved device are not orphaned (BE-0336 Unit 5).
        """
        resume_arn = checkpoint.load() if checkpoint is not None else None
        if resume_arn is not None:
            return collect_run(
                self._client, self._transfer, run_arn=resume_arn, dest=dest, sleep=self._sleep
            )
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
                [(work_dir, ".")], package_zip, extra_texts={"requirements.txt": REQUIREMENTS_TXT}
            )
            return submit_and_collect(
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
