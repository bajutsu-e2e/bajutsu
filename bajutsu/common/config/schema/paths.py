"""Where a target's files live, as written under `paths:` (BE-0452)."""

from __future__ import annotations

from ._model import _Model


class Paths(_Model):
    """`paths`: the directories a target reads, each relative to the config file (BE-0242).

    `scenarios` holds the scenario files `run` reads and `record` writes; `baselines`, `schemas`, and
    `goldens` override the default directory beside each scenario file for `visual`,
    `responseSchema`, and `golden` assertions.
    """

    scenarios: str | None = None
    baselines: str | None = None
    schemas: str | None = None
    goldens: str | None = None
