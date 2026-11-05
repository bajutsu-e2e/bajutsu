"""One scenario's worth of work for a batch cloud, with the target it runs against."""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.common.cloud.devicefarm import Platform


@dataclass(frozen=True)
class BatchRequest:
    """One scenario's worth of work for a batch cloud, with the target it runs against.

    `provider` selects the concrete `BatchProvider` from the registry; `scenario` and `config` are
    paths relative to the job's bundle directory, and `target` names the config entry; `platform`
    picks the device family (and, for Device Farm, the app upload type and the platform filter).
    `app_path` is a logical reference to the app artifact (an Android `.apk` or an iOS `.ipa`) to
    install on the reserved device: the target's `appPath` as its config names it, so a relative one
    resolves against the bundle directory on whichever machine runs the job, never a path on the
    server's host (BE-0448).
    """

    provider: str
    scenario: str
    target: str
    config: str
    platform: Platform
    app_path: str
