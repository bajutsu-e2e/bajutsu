"""The slice of the boto3 Device Farm client the submitter uses, so an in-memory fake fits."""

from __future__ import annotations

from typing import Any, Protocol

# ---------------------------------------------------------------------------
# AWS seams
# ---------------------------------------------------------------------------


class DeviceFarmClient(Protocol):
    """The slice of the boto3 ``devicefarm`` client the submitter uses (so an in-memory fake fits).

    Each method mirrors the boto3 call of the same name; the responses are the nested dicts boto3
    returns (``{"upload": {...}}``, ``{"run": {...}}``, ``{"artifacts": [...]}``).

    Each stub body is ``raise NotImplementedError``: a bare ``...`` here is a newly-added expression
    statement CodeQL flags as "no effect" (a new-file line can't inherit the dismissal the same idiom
    carries on ``main``, e.g. ``network.py``'s ``Collector``), while a docstring-only body would
    silently return ``None`` against the non-``None`` annotation if the Protocol were ever called
    directly. ``raise`` is CodeQL-clean and fails loud, so it closes both gaps at once.
    """

    def create_upload(self, *, projectArn: str, name: str, type: str) -> dict[str, Any]:  # noqa: N803 - boto3 kwargs
        """Register a new upload; boto3 returns ``{"upload": {...}}`` with the presigned PUT URL."""
        raise NotImplementedError

    def get_upload(self, *, arn: str) -> dict[str, Any]:
        """Fetch an upload's status (``INITIALIZED`` → ``SUCCEEDED`` / ``FAILED``)."""
        raise NotImplementedError

    def schedule_run(self, **kwargs: Any) -> dict[str, Any]:
        """Schedule a run from the uploaded app/test/spec; boto3 returns ``{"run": {...}}``."""
        raise NotImplementedError

    def get_run(self, *, arn: str) -> dict[str, Any]:
        """Fetch a run's current status; boto3 returns ``{"run": {...}}``."""
        raise NotImplementedError

    def list_artifacts(self, *, arn: str, type: str) -> dict[str, Any]:
        """List a run's artifacts of the given type; boto3 returns ``{"artifacts": [...]}``."""
        raise NotImplementedError

    def stop_run(self, *, arn: str) -> dict[str, Any]:
        """Ask Device Farm to stop a scheduled run; boto3 returns ``{"run": {...}}``."""
        raise NotImplementedError
