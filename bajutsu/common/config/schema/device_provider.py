"""Where a target's devices come from, filled from the `--worker-config` in force (BE-0236, BE-0452)."""

from __future__ import annotations

from ._model import _Model


class DeviceProvider(_Model):
    """Where a target's devices come from (BE-0236).

    No longer a target key (BE-0452): which grid serves a device is infrastructure, so an `appium`
    environment in `worker.yaml` names it, and the command that drives the device fills this for the
    targets of the grid's platform. `kind` selects the provider adapter from the device-provider
    registry, defaulting to `local` — a locally-attached simulator / emulator / device, exactly
    today's `--udid` path. `endpoint` carries the grid's address for `appium`. Like the mailbox
    `kind`, an unknown value — or a required-but-missing endpoint — fails closed when the run
    resolves the provider, not here: the deterministic config must not import a cloud SDK (BE-0112).
    """

    kind: str = "local"
    endpoint: str | None = None
