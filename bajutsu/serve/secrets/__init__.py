"""The SecretStore seam: how serve holds an operator credential (BE-0136 write-once secrets).

Shaped like the other serve seams (`artifacts.py`): a `Protocol` with a local implementation
(`EnvSecretStore`) and a hosted one (`server/secrets.py`), selected by whichever `ServeState`
carries. `set` and `describe` serve the HTTP handlers, and deliberately **no `get(name) -> value`**
is one an HTTP handler can reach: a secret is write-once, so no endpoint ever discloses the
plaintext again, matching how GitHub Actions Secrets work. The plaintext lives only where it is
consumed: `values_for_job` hands it to a job's environment on the job-execution path alone
(BE-0449), never on a response path.
"""

from .env_secret_store import EnvSecretStore
from .secret_store import SecretStore

__all__ = ["EnvSecretStore", "SecretStore"]
