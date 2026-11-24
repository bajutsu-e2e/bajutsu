"""The `hooks.sandbox` block of a target, beside the entry model its command lists share (BE-0491).

The entry model itself lives with the scenario models, which declare the same entries (BE-0501) and
may not import `config`; it is re-exported here for the config side's readers.
"""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from bajutsu.common.scenario.models.hook_command import (
    HOOK_NAME,
    HookCommand,
    HookOutcome,
    invalid_hook_name,
    reject_outcome_on_before,
)

from ._model import _Model

__all__ = [
    "HOOK_NAME",
    "HookCommand",
    "HookOutcome",
    "HookSandbox",
    "invalid_hook_name",
    "reject_outcome_on_before",
]


class HookSandbox(_Model):
    """`hooks.sandbox`: how the container a target's hook commands run in is built (BE-0491).

    A worker whose `commandExecution` sets `sandbox` for the config's source (BE-0492) runs every
    entry of this target inside one container that lives for the whole run, so a server a `beforeRun`
    script starts keeps serving until `afterRun`. Exactly one of `dockerImage` / `dockerfile` builds
    it; `ports` are published to the worker's loopback interface, where the browser or the app
    reaches them.
    """

    docker_image: str | None = Field(default=None, alias="dockerImage")
    # A path inside the config's own directory, so an uploaded bundle cannot build from elsewhere on
    # the host; checked where the container is started, against the directory the worker prepared.
    dockerfile: str | None = None
    ports: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_image_source(self) -> Self:
        if (self.docker_image is None) == (self.dockerfile is None):
            both = self.docker_image is not None
            raise ValueError(
                "hooks.sandbox needs exactly one of `dockerImage` or `dockerfile`, got "
                f"{'both' if both else 'neither'}"
            )
        bad = [p for p in self.ports if not (1 <= p <= 65535)]
        if bad:
            raise ValueError(f"hooks.sandbox.ports must be in 1..65535, got {bad}")
        return self
