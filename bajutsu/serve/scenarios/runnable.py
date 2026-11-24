"""A scenario ready to run, named the way `bajutsu run --scenario` expects."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Runnable:
    """A scenario ready to run via ``bajutsu run --scenario <arg>``.

    `arg` is the value to pass — a trusted absolute path on the local backend, or a
    workspace-relative path on the server backend. `materials` maps workspace-relative paths to
    file contents the run host must write **before** running (empty locally, where the files
    already exist on disk; on the server it carries the scenario text so a remote worker can
    materialize it — never a path a client controls)."""

    arg: str
    materials: dict[str, str] = field(default_factory=dict)
    # Whether a client wrote the text rather than the config's own source providing it (BE-0501), so
    # its hook commands answer to the `uploaded` policy whatever the config's source is.
    client_written: bool = False
    # The text serve reads for routing alone, when no scope holds it: an override's entry.
    text: str | None = None
