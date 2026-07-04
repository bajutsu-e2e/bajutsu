"""An encrypted, per-org SecretStore for the hosted backend (BE-0136 write-once secrets).

`EnvSecretStore` holds the value in the serve process's environment. `DbSecretStore` keeps the same
`SecretStore` seam but persists it — encrypted at rest with authenticated encryption (Fernet) and
scoped by ``org_id`` like the other server tables — so it survives a restart, is shared across the
control-plane replicas, and is scoped per org. As with the seam everywhere, there is no plaintext
read an HTTP handler can reach: `describe` decrypts internally only to compute the masked preview,
returning the mask alone. `cryptography` is imported lazily (behind the `db` extra), so the default
serve/CLI path never loads it.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bajutsu.serve.helpers import mask_secret

if TYPE_CHECKING:
    from cryptography.fernet import Fernet
    from sqlalchemy.engine import Engine


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
                row.updated_by = updated_by
                row.updated_at = datetime.now(UTC)
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


def fernet_from_env() -> Fernet | None:
    """A `Fernet` from ``BAJUTSU_SECRETS_KEY``, or None when it is unset. The key is a deployment
    secret provisioned outside the database (analogous to ``BAJUTSU_DATABASE_URL``), so the store
    can encrypt operator secrets at rest without the key ever living in the database it protects."""
    key = os.environ.get("BAJUTSU_SECRETS_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet

    return Fernet(key.encode("ascii"))
