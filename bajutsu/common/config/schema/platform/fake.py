"""The test-only `fake` platform's minimal models (BE-0452).

A `fake` target declares no condition to check: its `runsOn` and `driver` take no fields, and its
`app` takes an optional `id` and the readiness and identifier contract every platform shares.
"""

from __future__ import annotations

from .._model import _Model
from ._shared import AppCommon, RunsOnGroup


class FakeApp(AppCommon):
    """`app` on the fake platform: an optional identifier, `startWhen`, and `idNamespaces`.

    The fake driver reads the identifier contract as any driver does (readiness, `doctor`'s
    scoring), so the shared fields stay; nothing that builds, installs, or launches an app does.
    """

    id: str | None = None


class FakeRunsOn(RunsOnGroup):
    """`runsOn` on the fake platform: no destination, since the fake driver has no device to read."""


class FakeDriver(_Model):
    """`driver` on the fake platform: no fields."""


class FakeRun(_Model):
    """The fake fields of `run`: none beyond the shared run policy."""
