"""The worker capability: what one worker can run, declared in `worker.yaml` (BE-0450).

A worker is the machine or process that runs scenarios. Its capability file names the worker's
environment, how many jobs it may have in flight, how many targets one job may hold, and which
drivers it offers. Only facts that no probe can answer belong here: the host operating system is
read from the machine (`backends.current_host_os`), and the runtime and device-class tokens a
server worker advertises come from its device inventory.

The file is replaced whole, never merged: `--worker-config <path>` names the one file in force,
and without it the default shipped beside this module applies. There is no environment variable
and no lookup in the working directory, so the file in force is always the one on the command line.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)

from bajutsu.common import _yaml
from bajutsu.common.backends import IMPLEMENTED, PLATFORMS
from bajutsu.common.config.schema import DeviceProvider

DEFAULT_WORKER_CONFIG = Path(__file__).with_name("worker.yaml")

LOCAL = "local"

# A self-hosted or cloud Appium grid that serves one platform's devices behind a WebDriver endpoint
# (BE-0452). Unlike a batch provider it submits nothing: a run on this worker drives the grid's
# targets through the endpoint and its other targets on the host.
APPIUM = "appium"

# Every batch-provider kind Bajutsu can register (`serve/batch_provider`). The kinds are listed here
# rather than read from the provider registry, which the server package fills at runtime and the
# deterministic core may not import; a test keeps the two in step.
BATCH_PROVIDERS: frozenset[str] = frozenset({"devicefarm"})

ENVIRONMENTS: frozenset[str] = frozenset({LOCAL, APPIUM, *BATCH_PROVIDERS})

# The routing-token prefixes a worker fills in itself: `environment:<name>` for a provider it
# registered (BE-0448), `host:<os>` for the operating system it runs on (BE-0450), and
# `machine:<id>` for the server machine it shares, whose jobs name files by their paths there
# (BE-0449). Reserved, so every hand-written token input — a worker's override — rejects them: a
# hand-written one could pull a job onto a worker that cannot run it, or route a job nowhere. Defined
# here, beside the environment names, so the core can check them without importing the server
# package.
ENVIRONMENT_ROUTING_PREFIX = "environment:"
HOST_ROUTING_PREFIX = "host:"
MACHINE_ROUTING_PREFIX = "machine:"
RESERVED_ROUTING_PREFIXES: tuple[str, ...] = (
    ENVIRONMENT_ROUTING_PREFIX,
    HOST_ROUTING_PREFIX,
    MACHINE_ROUTING_PREFIX,
)
RESERVED_ROUTING_REASON = (
    "the environment:, host:, and machine: prefixes are reserved: a worker advertises them itself, "
    "from its worker.yaml environment, the machine it runs on, and the server machine it shares"
)


def reserved_routing_tokens(tokens: Iterable[str]) -> list[str]:
    """The tokens among *tokens* that carry a reserved routing prefix, sorted."""
    return sorted(t for t in tokens if t.startswith(RESERVED_ROUTING_PREFIXES))


class WorkerCapabilityError(ValueError):
    """A `worker.yaml` that cannot be read or does not match the schema."""


class AppiumGrid(BaseModel):
    """The grid an `appium` environment drives, as written under `appium:` in `worker.yaml` (BE-0452).

    Attributes:
        endpoint: The grid's Appium / WebDriver address, handed to the run as its device.
        platform: The platform whose targets go to the grid; the others of a scenario stay local.
            `ios` is the one value today.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    endpoint: str = Field(min_length=1)
    platform: Literal["ios"] = "ios"

    @field_validator("endpoint")
    @classmethod
    def _url(cls, value: str) -> str:
        if not value.strip().startswith(("http://", "https://")):
            raise ValueError(f"appium.endpoint {value!r} is not an http(s) URL")
        return value.strip()


class WorkerCapability(BaseModel):
    """One worker's declared limits, loaded from `worker.yaml` (BE-0450).

    Attributes:
        version: The file's schema version; only `1` exists.
        environment: Where the worker runs jobs: `local`, `appium`, or a device-cloud provider kind.
        appium: The grid an `appium` environment drives; required with that environment alone.
        max_job_concurrency: Jobs the worker may have in flight at once.
        max_targets_per_job: Targets one scenario may declare, counted across every driver; None
            means unbounded.
        drivers: The drivers this worker offers; a driver the list omits is unavailable.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    version: Literal[1]
    environment: str = LOCAL
    appium: AppiumGrid | None = None
    max_job_concurrency: PositiveInt = Field(default=1, alias="maxJobConcurrency")
    max_targets_per_job: PositiveInt | None = Field(default=None, alias="maxTargetsPerJob")
    drivers: tuple[str, ...]

    @field_validator("environment")
    @classmethod
    def _known_environment(cls, value: str) -> str:
        if value not in ENVIRONMENTS:
            raise ValueError(
                f"unknown environment {value!r}; expected one of {sorted(ENVIRONMENTS)}"
            )
        return value

    @field_validator("drivers")
    @classmethod
    def _known_drivers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - IMPLEMENTED)
        if unknown:
            raise ValueError(f"unknown driver(s) {unknown}; expected any of {sorted(IMPLEMENTED)}")
        return value

    @model_validator(mode="after")
    def _appium_names_its_grid(self) -> WorkerCapability:
        if self.environment == APPIUM and self.appium is None:
            raise ValueError("environment 'appium' needs an `appium:` block naming its endpoint")
        if self.environment != APPIUM and self.appium is not None:
            raise ValueError(
                f"an `appium:` block belongs to environment 'appium', not '{self.environment}'"
            )
        return self

    @model_validator(mode="after")
    def _local_runs_one_job(self) -> WorkerCapability:
        # Several local jobs at once need a device allocated to each job, which is separate work;
        # `run --workers` lanes are a different mechanism and stay unaffected.
        if self.is_local and self.max_job_concurrency > 1:
            raise ValueError(
                "maxJobConcurrency above 1 needs a device-cloud environment; a local worker "
                "runs one job at a time"
            )
        return self

    @property
    def is_local(self) -> bool:
        """Whether this worker drives devices on its own host."""
        return self.environment == LOCAL

    @property
    def is_batch(self) -> bool:
        """Whether this worker submits to a device-cloud batch provider instead of running jobs."""
        return self.environment in BATCH_PROVIDERS

    def drives_on_host(self, actuator: str) -> bool:
        """Whether *actuator*'s driver runs on this worker's own host, so the host rule applies.

        A batch provider runs every driver on its own host; an `appium` grid runs its platform's
        driver, and the scenario's other targets stay on this host (BE-0452).
        """
        if self.is_batch:
            return False
        return self.appium is None or actuator not in PLATFORMS.get(self.appium.platform, ())

    def device_provider_for(self, platform: str) -> DeviceProvider | None:
        """The device provider a target of *platform* takes from this worker, or None for local.

        An `appium` environment hands its grid to the targets of the grid's platform alone (BE-0452).
        """
        if self.appium is not None and platform == self.appium.platform:
            return DeviceProvider(kind=APPIUM, endpoint=self.appium.endpoint)
        return None


def parse_worker_capability(text: str, *, source: str = "worker.yaml") -> WorkerCapability:
    """Parse and validate one `worker.yaml` document.

    Args:
        text: The YAML text.
        source: The file name quoted in an error.

    Raises:
        WorkerCapabilityError: The text is not valid YAML or does not match the schema.
    """
    try:
        data = _yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise WorkerCapabilityError(f"{source}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise WorkerCapabilityError(f"{source}: expected a mapping at the top level")
    try:
        return WorkerCapability.model_validate(data)
    except ValidationError as e:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
            for err in e.errors()
        )
        raise WorkerCapabilityError(f"{source}: {details}") from e


def load_worker_capability(path: Path | None = None) -> WorkerCapability:
    """Load the worker capability in force: *path*, or the shipped default when it is None.

    Raises:
        WorkerCapabilityError: The file cannot be read or does not match the schema.
    """
    target = DEFAULT_WORKER_CONFIG if path is None else path
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise WorkerCapabilityError(f"{target}: cannot read worker config: {e}") from e
    return parse_worker_capability(text, source=str(target))
