"""An encrypted, per-org SecretStore for the hosted backend (BE-0136 write-once secrets).

`EnvSecretStore` holds the value in the serve process's environment. `DbSecretStore` keeps the same
`SecretStore` seam but persists it — encrypted at rest with authenticated encryption (Fernet) and
scoped by ``org_id`` like the other server tables — so it survives a restart, is shared across the
control-plane replicas, and is scoped per org. As with the seam everywhere, there is no plaintext
read an HTTP handler can reach: `describe` decrypts internally only to compute the masked preview,
returning the mask alone, and `values_for_job` is called only on the job-execution path (BE-0449).
`cryptography` is a base dependency but is imported lazily, so the CLI path never loads it.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from bajutsu.serve.helpers import ServeStartupError, mask_secret

if TYPE_CHECKING:
    from cryptography.fernet import Fernet
    from sqlalchemy.engine import Engine

_logger = logging.getLogger(__name__)


class DbSecretStore:
    """A `SecretStore` backed by the ``secrets`` table, encrypting each value with *fernet* and
    scoping every row to *org_id* (BE-0136). One instance serves one org, mirroring how the
    object-storage seams are built per org."""

    def __init__(self, engine: Engine, org_id: str, fernet: Fernet) -> None:
        self._engine = engine
        self._org_id = org_id
        self._fernet = fernet

    def set(self, name: str, value: str, *, updated_by: str | None = None) -> str | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Secret

        with Session(self._engine) as session:
            row = session.get(Secret, {"org_id": self._org_id, "name": name})
            if not value:  # an empty value clears the secret
                if row is not None:
                    session.delete(row)
                    session.commit()
                return None
            ciphertext = self._fernet.encrypt(value.encode("utf-8")).decode("ascii")
            if row is None:
                session.add(
                    Secret(
                        org_id=self._org_id,
                        name=name,
                        ciphertext=ciphertext,
                        updated_by=updated_by,
                        updated_at=datetime.now(UTC),
                    )
                )
            else:  # overwrite in place — rotating a key never needs to read the old one back
                row.ciphertext = ciphertext
                row.updated_at = datetime.now(UTC)
                # Keep the last known writer when this caller has no identity (a token/Bearer
                # request): a rotation by an unidentified caller shouldn't erase the audit trail.
                if updated_by is not None:
                    row.updated_by = updated_by
            session.commit()
            return mask_secret(value)

    def describe(self, name: str) -> str | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Secret

        with Session(self._engine) as session:
            row = session.get(Secret, {"org_id": self._org_id, "name": name})
            if row is None:
                return None
            # Decrypt only to compute the masked preview; the plaintext never leaves this method.
            plaintext = self._fernet.decrypt(row.ciphertext.encode("ascii")).decode("utf-8")
            return mask_secret(plaintext)

    def values_for_job(self) -> dict[str, str]:
        """Every secret of this org, decrypted, by logical name — for a job's environment only.

        The `SecretStore` read that no HTTP handler calls (BE-0449). A row the current key cannot
        decrypt (the key was rotated without re-entering the value) is skipped with a warning, so
        one stale secret does not stop every job of the org.
        """
        from cryptography.fernet import InvalidToken
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import Secret

        out: dict[str, str] = {}
        with Session(self._engine) as session:
            rows = session.scalars(select(Secret).where(Secret.org_id == self._org_id)).all()
            for row in rows:
                try:
                    out[row.name] = self._fernet.decrypt(row.ciphertext.encode("ascii")).decode(
                        "utf-8"
                    )
                except InvalidToken:
                    _logger.warning(
                        "secret %r of org %r cannot be decrypted with the current key; "
                        "a job runs without it",
                        row.name,
                        self._org_id,
                    )
        return out


def fernet_from_env() -> Fernet | None:
    """A `Fernet` from ``BAJUTSU_SECRETS_KEY``, or None when it is unset. The key is a deployment
    secret provisioned outside the database (analogous to ``BAJUTSU_DATABASE_URL``), so the store
    can encrypt operator secrets at rest without the key ever living in the database it protects."""
    key = os.environ.get("BAJUTSU_SECRETS_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet

    try:
        return Fernet(key.encode("ascii"))
    except ValueError as e:
        # Name the offending variable so a malformed key is diagnosable at startup — still fails
        # loud, just with a message that points at BAJUTSU_SECRETS_KEY rather than a bare Fernet one.
        raise ServeStartupError(
            "BAJUTSU_SECRETS_KEY must be a url-safe base64-encoded 32-byte Fernet key "
            "(generate one with `python3 -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'`)"
        ) from e


def fernet_from_env_or_local(state_dir: Path) -> Fernet:
    """A `Fernet` from ``BAJUTSU_SECRETS_KEY`` when it is set, else from the key a local start
    generates and keeps in *state_dir* (BE-0449). The deployment setting wins so an operator who
    provisions a key is never overridden by a file on disk.

    Raises:
        ValueError: ``BAJUTSU_SECRETS_KEY`` is malformed, or the local key file is unsafe or holds
            something that is not a Fernet key (see `local_keys.secrets_key`).
    """
    from_env = fernet_from_env()
    if from_env is not None:
        return from_env
    from cryptography.fernet import Fernet

    from bajutsu.serve.server.local_keys import secrets_key

    return Fernet(secrets_key(state_dir))
