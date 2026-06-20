"""Server backend for ``bajutsu serve`` (BE-0015 hosted phase).

Implementations of the serve seams (`RunExecutor`, `LogBus`, `ArtifactStore`, `ScenarioStore`)
backed by hosted infrastructure — a queue/broker, object storage, a database — for the multi-user
public/self-hosted deployments described in BE-0015 / BE-0016.

**Containment:** the heavy dependencies these modules use (RQ/Redis, object-storage and database
clients, FastAPI) are declared in the ``server`` / ``worker`` optional-dependency groups and are
**imported lazily**, inside the functions that need them — never at module load. Nothing under the
default ``bajutsu.serve`` / CLI path imports this subpackage, so the default install and the Linux
gate stay server-free (enforced by ``tests/serve/test_import_guard.py``).
"""

from __future__ import annotations
