"""The seam a named operator secret is written once and read through."""

from __future__ import annotations

from typing import Protocol


class SecretStore(Protocol):
    """Write-once storage for a named operator secret."""

    def set(self, name: str, value: str, *, updated_by: str | None = None) -> str | None:
        """Set or replace secret *name* to *value* (an empty *value* clears it).

        *updated_by* is best-effort audit metadata (who wrote it) the hosted store persists and the
        local store ignores. Returns the masked preview of what was stored, or None when cleared.
        """

    def describe(self, name: str) -> str | None:
        """The masked preview of secret *name*, or None when it is unset — never the plaintext."""

    def values_for_job(self) -> dict[str, str]:
        """Every secret this store holds, by logical name, in plaintext — for a job's environment.

        The one plaintext read (BE-0449). It exists so a worker's lease can carry the values a user
        set in the UI to the job it runs, since the server runs no job itself. It
        is called only on the job-execution path (`serve.jobs`), never by an HTTP handler, so no
        response can disclose a value (BE-0136); a test locks that boundary.
        """
