"""How to bring up the app's target server for a run, as written under `launchServer:`."""

from __future__ import annotations

from pydantic import Field, field_validator

from ._model import _Model


class LaunchServer(_Model):
    """How to bring up the app's target server (the host behind `baseUrl`) for a run.

    Unlike `mockServer` (which stubs the dependencies the app *calls*), this hosts the app *under
    test* itself — e.g. the static page for `demos/web`, or the inner `serve` the WebUI dogfood
    drives. `run` probes `readyUrl` first: if it already answers it reuses it (started externally),
    else it runs `cmd`, waits on the readiness probe (a condition wait, never a fixed sleep), and
    tears the process down afterwards.
    """

    cmd: str  # shell command that starts the server (run in its own process group)
    ready_url: str | None = Field(default=None, alias="readyUrl")  # probe target; default: baseUrl
    ready_timeout: float = Field(default=30.0, alias="readyTimeout")  # seconds before giving up
    cwd: str | None = None  # working directory (default: the run's cwd)
    env: dict[str, str] = Field(default_factory=dict)  # extra environment for the server
    # Sandbox runtime for `cmd` (BE-0090). A worker whose `commandExecution` sets `sandbox` for the
    # config's source (BE-0492) runs `cmd` inside a throwaway container instead of on the host;
    # exactly one of these declares how
    # the container is built (enforced at the sandbox decision point, not here — a non-sandbox
    # config legitimately ships neither).
    docker_image: str | None = Field(
        default=None, alias="dockerImage"
    )  # a Docker image reference: [registry/]repo[:tag|@digest]
    dockerfile: str | None = None  # bundle-relative Dockerfile, built via `docker build`
    port: int | None = None  # in-container listen port, published to a loopback host port

    @field_validator("port")
    @classmethod
    def _port_in_range(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 65535):
            raise ValueError("launchServer.port must be in 1..65535")
        return v
