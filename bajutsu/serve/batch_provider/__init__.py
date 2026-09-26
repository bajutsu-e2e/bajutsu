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

from ._functions import _PROVIDERS as _PROVIDERS
from ._functions import register, resolve
from .batch_checkpoint import BatchCheckpoint
from .batch_lifecycle_hook import BatchContext, BatchLifecycleHook
from .batch_provider import BatchProvider
from .batch_request import BatchRequest
from .device_farm_batch_provider import DeviceFarmBatchProvider

__all__ = [
    "BatchCheckpoint",
    "BatchContext",
    "BatchLifecycleHook",
    "BatchProvider",
    "BatchRequest",
    "DeviceFarmBatchProvider",
    "register",
    "resolve",
]
