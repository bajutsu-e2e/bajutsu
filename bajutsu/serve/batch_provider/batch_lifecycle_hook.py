"""Server-side lifecycle hooks around one Device Farm batch submit (BE-0435)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from bajutsu.common.cloud.devicefarm import Verdict

from .batch_request import BatchRequest


@dataclass
class BatchContext:
    """State shared between before_submit and after_run for one batch run.

    `job_id` is the stable per-job identifier (serve checkpoint key) so an after_run hook can
    retrieve durable per-job state minted by before_submit even after a restart (Thread-7 design).
    `launch_env` is populated by before_submit hooks and merged into the packaged run config
    before upload — the only path that reaches the app's launch environment on-device.
    """

    request: BatchRequest
    work_dir: Path
    job_id: str
    launch_env: dict[str, str] = field(default_factory=dict)


class BatchLifecycleHook(Protocol):
    """Server-side lifecycle hook around one Device Farm batch submit (BE-0435).

    Hooks are wired at process start only (via ``BAJUTSU_BATCH_HOOKS``); no ``serve`` endpoint,
    config field, or ``BatchRequest`` field may select or configure a hook (same trust boundary
    as BE-0432's ``pre_test_commands``).

    Explicitly subclass this protocol to inherit no-op defaults for unimplemented steps.
    Structural conformance (duck typing without subclassing) requires both methods to be defined.
    """

    def before_submit(self, ctx: BatchContext) -> None:
        """Run in the serve process before packaging; may populate ``ctx.launch_env``."""
        ...

    def after_run(self, ctx: BatchContext, verdict: Verdict | None) -> None:
        """Run after the verdict is collected (``verdict=None`` on pre-verdict failure)."""
        ...
