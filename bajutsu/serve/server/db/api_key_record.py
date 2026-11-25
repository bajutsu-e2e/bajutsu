"""An organization-scoped API key as the seam exchanges it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ApiKeyRecord:
    """One API key (BE-0509): who it acts as, until when, and the hash its secret is checked by."""

    id: str
    org_id: str
    name: str
    role: str
    secret_hash: str
    expires_at: datetime
    created_by: str | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
