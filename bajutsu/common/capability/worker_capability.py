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

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, get_args

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
from bajutsu.common.capability.command_policy import (
    COMMAND_EXECUTION_PREFIX,
    NAMED_COMMAND_NAME,
    NAMED_COMMAND_PREFIX,
    NAMED_EXECUTION_PREFIX,
    CommandExecution,
    NamedCommand,
)
from bajutsu.common.config.schema import DeviceProvider

DEFAULT_WORKER_CONFIG = Path(__file__).with_name("worker.yaml")

LOCAL = "local"

# A self-hosted or cloud Appium grid that serves one platform's devices behind a WebDriver endpoint
# (BE-0452). Unlike a batch provider it submits nothing: a run on this worker drives the grid's
# targets through the endpoint and its other targets on the host.
APPIUM = "appium"

# A worker whose devices a lease broker lends, one job at a time (BE-0511). It routes and runs like a
# `local` worker; only where a device comes from differs.
LEASED = "leased"

# The platforms a lease broker can serve. A web target drives a browser context and borrows nothing.
LeasedPlatform = Literal["ios", "android"]

# The placeholders a lease command may carry, replaced before it runs.
PLATFORM_PLACEHOLDER = "{platform}"
DEVICE_PLACEHOLDER = "{device}"
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)([sm])$")

# Every batch-provider kind Bajutsu can register (`serve/batch_provider`). The kinds are listed here
# rather than read from the provider registry, which the server package fills at runtime and the
# deterministic core may not import; a test keeps the two in step.
# Each kind maps to the platforms its devices run, which its worker advertises (BE-0484).
BATCH_PROVIDER_PLATFORMS: dict[str, tuple[str, ...]] = {"devicefarm": ("android", "ios")}
BATCH_PROVIDERS: frozenset[str] = frozenset(BATCH_PROVIDER_PLATFORMS)

# The run a Device Farm package carries, on the Device Farm host (BE-0465). Device Farm reserved the
# device and installed the uploaded app before the run starts, so every target takes a device that is
# booted with its app in place, whatever `subject.path` says.
DEVICEFARM_HOST = "devicefarm-host"

ENVIRONMENTS: frozenset[str] = frozenset(
    {LOCAL, LEASED, APPIUM, DEVICEFARM_HOST, *BATCH_PROVIDERS}
)

# The routing-token prefixes a worker fills in itself: `environment:<name>` for a provider it
# registered (BE-0448), `host:<os>` for the operating system it runs on (BE-0450), `machine:<id>`
# for the server machine it shares, whose jobs name files by their paths there (BE-0449),
# `device:<family>` for a real device connected to it (BE-0468), `job:run-intent` for the
# `run-set` jobs it can resolve, and `platform:<name>` / `kind:<kind>` for what its drivers or its
# provider can run (BE-0484). Reserved, so every hand-written
# token input — a worker's override — rejects them: a hand-written one could pull a job onto a
# worker that cannot run it, or route a job nowhere. Defined here, beside the environment names, so
# the core can check them without importing the server package.
ENVIRONMENT_ROUTING_PREFIX = "environment:"
HOST_ROUTING_PREFIX = "host:"
MACHINE_ROUTING_PREFIX = "machine:"
DEVICE_ROUTING_PREFIX = "device:"
# `cap:<route>:<capability>` and `setting:<route>:<key>` (BE-0467): what each environment route a
# worker drives can run and apply, computed from the support table, so no hand-written pin can claim
# a capability the route lacks.
CAP_ROUTING_PREFIX = "cap:"
SETTING_ROUTING_PREFIX = "setting:"
JOB_ROUTING_PREFIX = "job:"
PLATFORM_ROUTING_PREFIX = "platform:"
KIND_ROUTING_PREFIX = "kind:"
# `build:<platform>`: the platforms whose app this worker can build from `subject.build`, as its
# `worker.yaml` declares them, so a job that forces a build reaches a worker that can make it.
BUILD_ROUTING_PREFIX = "build:"
RESERVED_ROUTING_PREFIXES: tuple[str, ...] = (
    ENVIRONMENT_ROUTING_PREFIX,
    HOST_ROUTING_PREFIX,
    MACHINE_ROUTING_PREFIX,
    DEVICE_ROUTING_PREFIX,
    CAP_ROUTING_PREFIX,
    SETTING_ROUTING_PREFIX,
    JOB_ROUTING_PREFIX,
    PLATFORM_ROUTING_PREFIX,
    KIND_ROUTING_PREFIX,
    BUILD_ROUTING_PREFIX,
    # The command policy's tokens (BE-0492): a hand-written one could claim a source the worker's
    # `commandExecution` block denies.
    COMMAND_EXECUTION_PREFIX,
    NAMED_EXECUTION_PREFIX,
    NAMED_COMMAND_PREFIX,
)
RESERVED_ROUTING_REASON = (
    "the environment:, host:, machine:, device:, cap:, setting:, job:, platform:, kind:, build:, "
    "command-execution:, named-execution:, and named-command: prefixes are reserved: a worker "
    "advertises them itself, from its worker.yaml environment, drivers, builds, and command policy, "
    "the machine it runs on, the server machine it shares, the devices it can run on, and the jobs "
    "it can resolve"
)

# The platforms whose app a `subject.build` command makes; a web target has no app binary to build.
BuildPlatform = Literal["ios", "android"]
BUILD_PLATFORMS: tuple[BuildPlatform, ...] = ("ios", "android")


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


class LeaseBroker(BaseModel):
    """The lease broker a `leased` environment borrows from, written under `lease:` (BE-0511).

    Attributes:
        command: The lease command's argv. It runs with no shell, stays alive while the device is
            held, and prints one line naming the device.
        acquire_timeout: Seconds the command may take to print that line.
        release_timeout: Seconds it may take to exit after the termination signal.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    command: tuple[str, ...] = Field(min_length=1)
    acquire_timeout: float = Field(default=60.0, alias="acquireTimeout", gt=0)
    release_timeout: float = Field(default=10.0, alias="releaseTimeout", gt=0)

    @field_validator("acquire_timeout", "release_timeout", mode="before")
    @classmethod
    def _seconds(cls, value: object) -> object:
        if isinstance(value, str):
            if (match := _DURATION.match(value.strip())) is None:
                raise ValueError(f"duration {value!r} must be a number followed by `s` or `m`")
            return float(match[1]) * (60 if match[2] == "m" else 1)
        return value

    @field_validator("command")
    @classmethod
    def _placeholders(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value[0].strip():
            raise ValueError("lease.command needs a program as its first element")
        known = (PLATFORM_PLACEHOLDER, DEVICE_PLACEHOLDER)
        for i, element in enumerate(value):
            if unknown := [p for p in _PLACEHOLDER.findall(element) if p not in known]:
                raise ValueError(
                    f"lease.command: unknown placeholder {unknown[0]!r}; known: {', '.join(known)}"
                )
            if DEVICE_PLACEHOLDER not in element:
                continue
            if element != DEVICE_PLACEHOLDER:
                raise ValueError(f"lease.command: {DEVICE_PLACEHOLDER} must be a whole element")
            # A job that names no device drops this element and the one before it, so the one
            # before it has to be the flag that takes it and nothing else.
            flag = value[i - 1] if i > 0 else ""
            if i < 2 or not flag.startswith("-") or _PLACEHOLDER.search(flag):
                raise ValueError(
                    f"lease.command: {DEVICE_PLACEHOLDER} must follow the flag that takes it"
                )
        return value


class WorkerCapability(BaseModel):
    """One worker's declared limits, loaded from `worker.yaml` (BE-0450).

    Attributes:
        version: The file's schema version; only `1` exists.
        environment: Where the worker runs jobs: `local`, `leased`, `appium`, or a device-cloud
            provider kind.
        appium: The grid an `appium` environment drives; required with that environment alone.
        lease: The lease broker a `leased` environment borrows from; required with it alone.
        platforms: The platforms that broker serves; required with a `leased` environment alone.
        max_job_concurrency: Jobs the worker may have in flight at once.
        max_targets_per_job: Targets one scenario may declare, counted across every driver; None
            means unbounded.
        drivers: The drivers this worker offers; a driver the list omits is unavailable.
        real_devices: Whether a local `xcuitest` worker also drives attached real iPhones, so it
            advertises the real-device route beside the Simulator's (BE-0467). None, the default,
            advertises it as well, since such a worker served real-device jobs before; `false`
            keeps a Simulator-only Mac off them.
        build: The platforms whose app this worker can build by running a target's
            `subject.build`. None, the default, means every platform with an app to build, since a
            worker built any app before the field existed; `[]` builds none, so a run that needs a
            build stops instead of starting one the machine has no toolchain for.
        command_execution: Whether a command a config declares runs, per config source (BE-0492).
        named_commands: The scripts the worker's operator installed, which a config may run by name.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    version: Literal[1]
    environment: str = LOCAL
    appium: AppiumGrid | None = None
    lease: LeaseBroker | None = None
    platforms: tuple[LeasedPlatform, ...] | None = None
    max_job_concurrency: PositiveInt = Field(default=1, alias="maxJobConcurrency")
    max_targets_per_job: PositiveInt | None = Field(default=None, alias="maxTargetsPerJob")
    drivers: tuple[str, ...]
    real_devices: bool | None = Field(default=None, alias="realDevices")
    build: tuple[BuildPlatform, ...] | None = None
    command_execution: CommandExecution = Field(
        default_factory=CommandExecution, alias="commandExecution"
    )
    named_commands: dict[str, NamedCommand] = Field(default_factory=dict, alias="namedCommands")

    @field_validator("named_commands")
    @classmethod
    def _named_command_names(cls, value: dict[str, NamedCommand]) -> dict[str, NamedCommand]:
        bad = sorted(n for n in value if not NAMED_COMMAND_NAME.match(n))
        if bad:
            raise ValueError(f"namedCommands name(s) {bad} must match {NAMED_COMMAND_NAME.pattern}")
        return value

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
    def _leased_names_its_broker(self) -> WorkerCapability:
        if self.environment == LEASED:
            if self.lease is None:
                raise ValueError(
                    "environment 'leased' needs a `lease:` block naming the lease command"
                )
            if not self.platforms:
                raise ValueError("environment 'leased' needs `platforms:` naming what it serves")
            if len(set(self.platforms)) != len(self.platforms):
                raise ValueError(f"platforms lists a platform twice: {list(self.platforms)}")
            return self
        for key, value in (("lease", self.lease), ("platforms", self.platforms)):
            if value is not None:
                raise ValueError(
                    f"a `{key}:` key belongs to environment 'leased', not '{self.environment}'"
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

    @field_validator("build")
    @classmethod
    def _no_repeated_build(
        cls, value: tuple[BuildPlatform, ...] | None
    ) -> tuple[BuildPlatform, ...] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError(f"build lists a platform twice: {list(value)}")
        return value

    @property
    def buildable(self) -> tuple[BuildPlatform, ...]:
        """The platforms whose app this worker can build, in a fixed order."""
        declared = BUILD_PLATFORMS if self.build is None else self.build
        return tuple(p for p in BUILD_PLATFORMS if p in declared)

    def can_build(self, platform: str) -> bool:
        """Whether this worker can build the app of a target on *platform* from `subject.build`."""
        return platform in self.buildable

    @property
    def is_local(self) -> bool:
        """Whether this worker drives devices on its own host, borrowed ones included (BE-0511)."""
        return self.environment in (LOCAL, LEASED)

    def serves(self, platform: str) -> bool:
        """Whether a target on *platform* can get a device from this worker's environment.

        A `leased` worker drives only the device platforms its broker lends; a platform that
        needs no device, web, runs on its own host.
        """
        if self.platforms is None:
            return True
        return platform in self.platforms or platform not in get_args(LeasedPlatform)

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

        An `appium` environment hands its grid to the targets of the grid's platform alone (BE-0452);
        a `devicefarm-host` one hands every target its preinstalled Device Farm device (BE-0465);
        a `leased` one borrows for the targets of the platforms its broker lends (BE-0511).
        """
        if self.environment == DEVICEFARM_HOST:
            return DeviceProvider(kind=DEVICEFARM_HOST)
        if self.appium is not None and platform == self.appium.platform:
            return DeviceProvider(kind=APPIUM, endpoint=self.appium.endpoint)
        if self.lease is not None and platform in (self.platforms or ()):
            return DeviceProvider(
                kind=LEASED,
                command=self.lease.command,
                acquire_timeout=self.lease.acquire_timeout,
                release_timeout=self.lease.release_timeout,
            )
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
