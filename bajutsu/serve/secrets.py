"""The SecretStore seam: how serve holds an operator credential (BE-0136 write-once secrets).

Shaped like the other serve seams (`artifacts.py`): a `Protocol` with a local implementation
(`EnvSecretStore`) and a hosted one (`server/secrets.py`), selected by whichever `ServeState`
carries. Two operations only — `set` and `describe` — and deliberately **no `get(name) -> value`**
an HTTP handler can reach: a secret is write-once, so no endpoint ever discloses the plaintext
again, matching how GitHub Actions Secrets work. The plaintext lives only where it is consumed
(a spawned record/run/crawl job inheriting it from the environment), never on a response path.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Protocol

from bajutsu.serve.helpers import mask_secret


class SecretStore(Protocol):
    """Write-once storage for a named operator secret."""

    def set(self, name: str, value: str, *, updated_by: str | None = None) -> str | None:
        """Set or replace secret *name* to *value* (an empty *value* clears it).

        *updated_by* is best-effort audit metadata (who wrote it) the hosted store persists and the
        local store ignores. Returns the masked preview of what was stored, or None when cleared.
        """

    def describe(self, name: str) -> str | None:
        """The masked preview of secret *name*, or None when it is unset — never the plaintext."""


class EnvSecretStore:
    """Holds a secret in the serve process's environment for its lifetime (in memory only, never
    written to disk) — today's local behavior, moved behind the seam. A logical secret *name* maps
    to an env var through *env_var_for* (honoring a bound config's ``ai.keyEnv``, BE-0097), so a
    spawned record/run job inherits the value under the name it expects."""

    def __init__(self, env_var_for: Callable[[str], str]) -> None:
        self._env_var_for = env_var_for

    def set(self, name: str, value: str, *, updated_by: str | None = None) -> str | None:
        var = self._env_var_for(name)
        if value:
            os.environ[var] = value
            return mask_secret(value)
        os.environ.pop(var, None)
        return None

    def describe(self, name: str) -> str | None:
        value = os.environ.get(self._env_var_for(name)) or None
        return mask_secret(value) if value is not None else None
