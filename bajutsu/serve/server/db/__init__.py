"""The `Repository` seam: the hosted backend's system of record (BE-0015 7a).

Shaped like the other server seams (`object_store.py`): a `Protocol`, a SQLAlchemy implementation,
and an env-driven factory — with SQLAlchemy and the ORM models lazy-imported inside the functions
that need them, so the default `serve`/CLI path never loads them (the import guard locks this).
7a ships the `runs` methods (`RunRecord`); the orgs, users, jobs, and secrets methods extend the
same seam with their own boundary types. ORM rows never leak past the seam — only the boundary
types cross."""

from ._functions import _age_seconds as _age_seconds
from ._functions import _as_utc as _as_utc
from ._functions import _positive_env as _positive_env
from ._functions import _to_org as _to_org
from ._functions import _to_record as _to_record
from ._functions import engine_from_url, repository_from_env
from ._shared import (
    DEFAULT_LEASE_MAX_ATTEMPTS,
    DEFAULT_RUN_LIMIT,
    TERMINAL_JOB_STATUSES,
    cancelled_job_result,
)
from .job_metrics import JobMetrics
from .leased_job import LeasedJob
from .org_record import OrgRecord
from .repository import Repository
from .run_record import RunRecord
from .sql_repository import DEFAULT_LEASE_TIMEOUT_SECONDS, SqlRepository

__all__ = [
    "DEFAULT_LEASE_MAX_ATTEMPTS",
    "DEFAULT_LEASE_TIMEOUT_SECONDS",
    "DEFAULT_RUN_LIMIT",
    "TERMINAL_JOB_STATUSES",
    "JobMetrics",
    "LeasedJob",
    "OrgRecord",
    "Repository",
    "RunRecord",
    "SqlRepository",
    "cancelled_job_result",
    "engine_from_url",
    "repository_from_env",
]
