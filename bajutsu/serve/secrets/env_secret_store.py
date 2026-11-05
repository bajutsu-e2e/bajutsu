"""The process-environment secret store, in memory for the server's lifetime only."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable

from bajutsu.serve.helpers import mask_secret


class EnvSecretStore:
    """Holds a secret in the serve process's environment for its lifetime (in memory only, never
    written to disk) — today's local behavior, moved behind the seam. A logical secret *name* maps
    to an env var through *env_var_for* (honoring a bound config's ``ai.keyEnv``, BE-0097), so a
    spawned record/run job inherits the value under the name it expects."""

    def __init__(self, env_var_for: Callable[[str], str]) -> None:
        self._env_var_for = env_var_for
        # The names written through this store, so `values_for_job` can name them. The values stay
        # in the environment alone. Request threads write it while a job thread reads it.
        self._names: set[str] = set()
        self._names_lock = threading.Lock()

    def set(self, name: str, value: str, *, updated_by: str | None = None) -> str | None:  # noqa: ARG002  # SecretStore shape
        var = self._env_var_for(name)
        if value:
            os.environ[var] = value
            with self._names_lock:
                self._names.add(name)
            return mask_secret(value)
        os.environ.pop(var, None)
        with self._names_lock:
            self._names.discard(name)
        return None

    def describe(self, name: str) -> str | None:
        value = os.environ.get(self._env_var_for(name)) or None
        return mask_secret(value) if value is not None else None

    def values_for_job(self) -> dict[str, str]:
        """The secrets written through this store that are still set, by logical name.

        A spawned job inherits them from the environment already; this answers the same values for
        a caller that builds a job's environment explicitly (BE-0449).
        """
        with self._names_lock:
            names = sorted(self._names)
        out: dict[str, str] = {}
        for name in names:
            value = os.environ.get(self._env_var_for(name))
            if value:
                out[name] = value
        return out
