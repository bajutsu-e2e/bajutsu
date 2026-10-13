"""The test-only `fake` platform's minimal models (BE-0452).

A `fake` target declares no condition to check: its `runsOn` and `driver` take no fields, and its
`app` takes an optional `id` alone.
"""

from __future__ import annotations

from .._model import _Model


class FakeApp(_Model):
    """`app` on the fake platform: an optional identifier and nothing else."""

    id: str | None = None


class FakeRunsOn(_Model):
    """`runsOn` on the fake platform: no fields, since the fake driver has no device to read."""


class FakeDriver(_Model):
    """`driver` on the fake platform: no fields."""


class FakeRun(_Model):
    """The fake fields of `run`: none beyond the shared run policy."""
