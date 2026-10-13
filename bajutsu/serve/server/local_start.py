"""The defaults the server supplies for itself when no cloud service is configured (BE-0449).

A hosted deployment names its database, its secrets key, and its bucket in the environment. A
local ``bajutsu serve`` names none of them, so the server fills each gap from the
state directory beside the runs tree (`local_keys.local_state_dir`): a SQLite queue, a generated
secrets key, and a ``file://`` store that reads the default org's runs from the runs tree. Each helper here decides one of those
defaults; ``_build_server_state`` wires them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from bajutsu.serve.helpers import ServeStartupError
from bajutsu.serve.server.file_object_store import FileObjectStore, UrlSigner
from bajutsu.serve.server.local_keys import signing_key
from bajutsu.serve.server.object_store import (
    ObjectStore,
    artifact_prefix,
    baseline_prefix,
    object_store_from_env,
)
from bajutsu.serve.server.secrets import fernet_from_env, fernet_from_env_or_local

if TYPE_CHECKING:
    from cryptography.fernet import Fernet

# The default queue database's file name inside the state directory.
QUEUE_DB_FILE = "queue.db"
# The directory inside the state directory that holds every stored object but the default org's runs.
LOCAL_OBJECTS_DIR = "objects"

_TRUE = frozenset({"true", "1"})
_FALSE = frozenset({"false", "0"})
# Binds that listen on every interface; a local worker reaches such a server over loopback.
_WILDCARD_HOSTS = frozenset({"", "0.0.0.0", "::"})  # noqa: S104 — matching a bind, not binding


def default_database_url(state_dir: Path) -> str:
    """The SQLite URL of the queue database a local start keeps in *state_dir*."""
    return f"sqlite:///{state_dir / QUEUE_DB_FILE}"


def hosted_from_env(*, server_store_set: bool) -> bool:
    """Whether this deployment is hosted, from ``BAJUTSU_HOSTED``.

    Hosted is a deployment setting that a repository does not imply (BE-0449): a local start keeps a
    database and still offers the file browser of BE-0108 and the device takeover of BE-0185. Unset,
    it follows the artifact store, so a deployment with ``BAJUTSU_SERVER_STORE`` set stays hosted as
    it was before this setting existed, and a local start does not.

    Raises:
        ValueError: ``BAJUTSU_HOSTED`` is set to anything but true, false, 1, or 0.
    """
    raw = os.environ.get("BAJUTSU_HOSTED")
    if raw is None or raw == "":
        return server_store_set
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ServeStartupError(f"BAJUTSU_HOSTED must be one of true, false, 1, 0; got {raw!r}")


def loopback_base_url(host: str, port: int) -> str:
    """The URL this server is reachable at from the same machine, for the store's signed URLs.

    A wildcard bind has no single name to give, and the spec's local worker reaches it over
    loopback, so it uses ``127.0.0.1``. An IPv6 literal is bracketed.
    """
    name = "127.0.0.1" if host in _WILDCARD_HOSTS else host
    if ":" in name and not name.startswith("["):
        name = f"[{name}]"
    return f"http://{name}:{port}"


def local_file_store(
    runs_dir: Path,
    state_dir: Path,
    *,
    host: str,
    port: int,
    baselines_dir: Path | None = None,
) -> FileObjectStore:
    """The ``file://`` store a local start keeps its objects in.

    Its keys follow the bucket layout. The default org's run artifacts (``artifacts/<runId>/…``)
    live in the runs tree, mounted at that prefix, so a run that ``bajutsu run`` wrote from the
    command line, and every run already under ``runs/``, resolves where it lies. Everything else
    (another org's prefix, scenarios, baselines, uploads) lives under ``<state_dir>/objects``, out
    of the runs tree: the default org's artifact store sees only its own runs, so no run id it can
    be asked for names another tenant's tree or a store folder. The default org's baselines
    (``baselines/…``) live in *baselines_dir* when one is given: the `--baselines` directory an
    approve writes and a run reads, so a worker's signed download reads the same files.
    Signed with the local HMAC key.

    Raises:
        ValueError: *port* is 0, *baselines_dir* is or encloses the runs or the state directory, the
            runs directory is a symlink, or the signing key file is unsafe.
    """
    if port == 0:
        # The signed URLs are fixed when the store is built, before any socket is bound, so an
        # ephemeral port would leave them pointing at port 0.
        raise ServeStartupError(
            "a server without BAJUTSU_SERVER_STORE signs URLs to its own port, "
            "so it needs a fixed --port (not 0)"
        )
    if baselines_dir is not None:
        _refuse_enclosing_baselines(baselines_dir, runs_dir=runs_dir, state_dir=state_dir)
    return FileObjectStore(
        state_dir / LOCAL_OBJECTS_DIR,
        loopback_base_url(host, port),
        UrlSigner(signing_key(state_dir)),
        mount=(artifact_prefix(), runs_dir),
        mounts=[(baseline_prefix(), baselines_dir)] if baselines_dir is not None else [],
    )


def _refuse_enclosing_baselines(baselines_dir: Path, *, runs_dir: Path, state_dir: Path) -> None:
    """Refuse a ``--baselines`` directory that is, or holds, the runs tree or the state directory.

    The store serves every file under the baselines mount to anyone holding a signed URL, and an
    override run's lease signs one per file there. A baselines directory enclosing the state
    directory would hand out the signing key, the secrets key, and the queue database; one
    enclosing the runs tree would list every run's files as baselines.

    Raises:
        ValueError: *baselines_dir* is or encloses *runs_dir* or *state_dir*.
    """
    baselines = baselines_dir.resolve()
    for label, directory in (("runs", runs_dir), ("state", state_dir)):
        if directory.resolve().is_relative_to(baselines):
            raise ServeStartupError(
                f"--baselines {baselines_dir} is or contains the {label} directory {directory}; "
                "a local start serves the baselines directory over signed URLs, so point "
                "--baselines at a directory of its own"
            )


def artifact_store(
    runs_dir: Path,
    state_dir: Path,
    *,
    host: str,
    port: int,
    baselines_dir: Path | None = None,
) -> tuple[ObjectStore, str, bool]:
    """The server's artifact store, its key prefix, and whether it is a local start's file store.

    The bucket ``BAJUTSU_SERVER_STORE`` names when it is set. Otherwise `local_file_store`, and the
    flag is True (BE-0449). The key layout is the same either way.

    Raises:
        ValueError: ``BAJUTSU_SERVER_STORE`` is malformed, or a local default is unsafe.
        ImportError: the bucket's SDK is not installed.
    """
    server_store = object_store_from_env()
    if server_store is not None:
        store, prefix = server_store
        return store, prefix, False
    store = local_file_store(runs_dir, state_dir, host=host, port=port, baselines_dir=baselines_dir)
    return store, "", True


def secrets_fernet(state_dir: Path, *, local_database: bool) -> Fernet:
    """The key that encrypts operator secrets at rest in the database (BE-0136).

    Every start has a database (BE-0449). ``BAJUTSU_SECRETS_KEY`` is the key when it is set.
    Otherwise only the default SQLite of a local start (*local_database*) gets a key generated into
    *state_dir*: an operator's own database never has one minted behind their back, so it fails at
    startup instead of degrading to plaintext in memory.

    Raises:
        ValueError: a configured database has no ``BAJUTSU_SECRETS_KEY``, the key is malformed, or
            the local key file is unsafe.
    """
    if local_database:
        return fernet_from_env_or_local(state_dir)
    fernet = fernet_from_env()
    if fernet is None:
        raise ServeStartupError(
            "BAJUTSU_SECRETS_KEY is required with a configured BAJUTSU_DATABASE_URL"
        )
    return fernet
