"""A ``file://`` `ObjectStore` rooted at a directory, with server-signed URLs (BE-0449 unit 1).

A local start has no bucket, yet a worker still uploads through the signed-URL interface of BE-0160
and the UI still reads artifacts through signed GET URLs. This store keeps the objects on the
server's own disk and hands out URLs to the server's ``/api/store/objects/`` routes, signed with an
HMAC key (`local_keys.signing_key`). The signature is the whole authorization for those routes, so it
binds the method, the object key, the content type, and the expiry, and every key is confined to the
store root before any file is touched.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote, urlencode

from bajutsu.common.run_meta.object_store import _PRESIGN_TTL, _PUT_TTL
from bajutsu.serve.gate import SIGNED_STORE_PREFIX
from bajutsu.serve.helpers import valid_relative_key

_DIR_MODE = 0o700
_FILE_MODE = 0o600
# Names a pending upload is staged under; `list_keys` skips them so a half-written object is never
# listed. The leading dot also keeps them out of a casual ``ls`` of a run tree.
_STAGING_PREFIX = ".bajutsu-put-"


def _canonical(key: str) -> bool:
    # A filesystem path collapses ``a//b``, ``a/./b``, ``./a/b`` and ``a/b/`` onto ``a/b``, while a
    # bucket keeps them distinct; only the one spelling `list_keys` returns is a key here, so a URL
    # signed for one spelling can never overwrite the object another names. A segment under the
    # staging prefix is reserved too: `list_keys` hides those names, so an object there could never
    # be listed, archived, or purged.
    return all(
        seg not in ("", ".") and not seg.startswith(_STAGING_PREFIX) for seg in key.split("/")
    )


class UrlSigner:
    """Signs and verifies the store's URLs with HMAC-SHA256 over the method, the object key, the
    content type, and the expiry."""

    def __init__(self, key: bytes, *, clock: Callable[[], float] = time.time) -> None:
        if not key:
            raise ValueError("a URL signing key must not be empty")
        self._key = key
        self._clock = clock

    def _mac(self, method: str, key: str, content_type: str, expires: int) -> str:
        # A JSON array frames each field unambiguously, so no choice of key or content type can
        # make two different requests share one signed message.
        message = json.dumps([method, key, content_type, expires], separators=(",", ":"))
        return hmac.new(self._key, message.encode("utf-8"), hashlib.sha256).hexdigest()

    def sign(self, method: str, key: str, content_type: str, ttl: int) -> tuple[int, str]:
        """The ``(expires, signature)`` pair that authorizes *method* on *key* for *ttl* seconds.

        Args:
            method: ``GET`` or ``PUT``.
            key: the object key the URL names.
            content_type: the MIME type a PUT must send (empty for a GET).
            ttl: seconds the URL stays valid.
        """
        expires = int(self._clock()) + ttl
        return expires, self._mac(method, key, content_type, expires)

    def verify(self, method: str, key: str, content_type: str, expires: int, sig: str) -> bool:
        """Whether *sig* authorizes *method* on *key* with *content_type*, and has not expired."""
        if expires < self._clock():
            return False
        expected = self._mac(method, key, content_type, expires)
        return hmac.compare_digest(expected.encode("ascii"), sig.encode("utf-8", "replace"))


class PendingPut:
    """An object being written: a staging file beside its destination, made visible under the key
    only by `commit`, so a reader never sees a partial object."""

    def __init__(self, final: Path) -> None:
        self._final = final
        fd, name = tempfile.mkstemp(prefix=_STAGING_PREFIX, dir=final.parent)
        os.fchmod(fd, _FILE_MODE)
        self._path = Path(name)
        self._file: BinaryIO = os.fdopen(fd, "wb")

    @property
    def file(self) -> BinaryIO:
        """The staging file, open for writing."""
        return self._file

    def write(self, chunk: bytes) -> None:
        """Append *chunk* to the staging file."""
        self._file.write(chunk)

    def commit(self) -> None:
        """Sync the staging file and atomically replace the object with it."""
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()
            self._path.replace(self._final)
        except BaseException:
            self._file.close()
            self._path.unlink(missing_ok=True)
            raise

    def abort(self) -> None:
        """Discard the staging file, leaving any existing object untouched."""
        self._file.close()
        self._path.unlink(missing_ok=True)


def _mount_prefix(prefix: str) -> str:
    """*prefix*, checked as a mount's key prefix: a safe relative key ending in ``/``."""
    if not prefix.endswith("/") or not valid_relative_key(prefix.rstrip("/")):
        raise ValueError(f"invalid mount prefix: {prefix!r}")
    return prefix


class FileObjectStore:
    """`ObjectStore` over a directory, whose signed URLs point at this server's store routes.

    *mount*, when given, is a ``(key prefix, directory)`` pair: every key under that prefix lives
    under the directory instead of the root, with the prefix stripped. A local start mounts the
    runs tree at ``artifacts/`` (BE-0449), so the default org's run trees stay where ``bajutsu run``
    writes them while every other object keeps the bucket layout under the root. *mounts* adds more
    pairs the same way; the local start mounts the ``--baselines`` directory at ``baselines/``, so a
    worker downloads the baselines an approve wrote there. The store creates the *mount* directory
    but not a *mounts* one, which appears with its first object. No prefix may nest inside another.
    The key namespace is the bucket's either way, so a signature names one object and a key under a
    prefix can never reach the root or another mount, nor one outside it the mount.
    """

    def __init__(
        self,
        root: Path,
        base_url: str,
        signer: UrlSigner,
        *,
        presign_ttl: int = _PRESIGN_TTL,
        mount: tuple[str, Path] | None = None,
        mounts: Sequence[tuple[str, Path]] = (),
    ) -> None:
        self._root = _owner_only_dir(root)
        self._base_url = base_url.rstrip("/")
        self.signer = signer
        self._ttl = presign_ttl
        self._mount: tuple[str, Path] | None = None
        pairs: list[tuple[str, Path]] = []
        if mount is not None:
            # The mount is the operator's own runs tree, not a directory this store owns: create and
            # resolve it, but leave its mode alone (`make_run_dir` already restricts each run dir)
            # and follow a symlink (a runs tree moved to a larger disk). `path_for` confines every
            # key to the resolved directory, which is what protects it.
            mount[1].mkdir(parents=True, exist_ok=True)
            self._mount = (_mount_prefix(mount[0]), mount[1].resolve())
            pairs.append(self._mount)
        # The other mounts are the operator's directories too, resolved the same way. A missing one
        # is not created here, so a start leaves no empty directory in the project.
        pairs += [(_mount_prefix(prefix), directory.resolve()) for prefix, directory in mounts]
        for i, (a, _) in enumerate(pairs):
            for b, _ in pairs[i + 1 :]:
                if a.startswith(b) or b.startswith(a):
                    raise ValueError(f"mount prefixes overlap: {a!r} and {b!r}")
        self._mounts: tuple[tuple[str, Path], ...] = tuple(pairs)

    @property
    def root(self) -> Path:
        """The resolved directory every object outside the mount lives under."""
        return self._root

    @property
    def mount(self) -> tuple[str, Path] | None:
        """The ``(key prefix, resolved directory)`` mount, or None."""
        return self._mount

    @property
    def mounts(self) -> tuple[tuple[str, Path], ...]:
        """Every ``(key prefix, resolved directory)`` mount, *mount* first."""
        return self._mounts

    def _locate(self, key: str) -> tuple[Path, str]:
        """The directory *key* lives under, and its path relative to that directory."""
        for prefix, directory in self._mounts:
            if key.startswith(prefix):
                return directory, key[len(prefix) :]
        return self._root, key

    def path_for(self, key: str) -> Path:
        """The file *key* names, confined to the root, or to the mount for a key under its prefix.

        Raises:
            ValueError: *key* is not a safe relative key, is not in canonical form (see
                `_canonical`), or resolves (through ``..`` or a symlink) outside its directory or
                onto the directory itself.
        """
        if not valid_relative_key(key) or not _canonical(key):
            raise ValueError(f"invalid object key: {key!r}")
        base, rel = self._locate(key)
        resolved = (base / rel).resolve()
        if resolved == base or not resolved.is_relative_to(base):
            raise ValueError(f"object key escapes the store root: {key!r}")
        # No directory may be reached under another's names, whichever one nests: a root-side key
        # must not land in a mount (it would reach it under a name its prefix never covered), and a
        # mounted key must not land in the root or another mount. When *other* encloses *base*,
        # every key confined to *base* is inside it anyway, so that case is not an escape.
        for other in self._dirs():
            if other == base:
                continue
            lands_in_other = resolved == other or resolved.is_relative_to(other)
            if lands_in_other and not base.is_relative_to(other):
                raise ValueError(f"object key escapes the store root: {key!r}")
        return resolved

    def _dirs(self) -> tuple[Path, ...]:
        """The root and every mounted directory."""
        return (self._root, *(directory for _, directory in self._mounts))

    def _make_parents(self, path: Path) -> None:
        missing: list[Path] = []
        parent = path.parent
        stop = self._dirs()
        while parent not in stop and not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for d in reversed(missing):
            d.mkdir(exist_ok=True)
            d.chmod(_DIR_MODE)

    def begin_put(self, key: str) -> PendingPut:
        """Start writing the object at *key*; the caller writes, then commits or aborts.

        Raises:
            ValueError: *key* is invalid or escapes the root (see `path_for`).
            OSError: the key conflicts with the store (``ENOTDIR``/``EEXIST``: a component is an
                existing file; ``ENAMETOOLONG``: a name the filesystem rejects), or the server's
                own I/O failed (a full disk, a permission loss).
        """
        final = self.path_for(key)
        # A mounted directory the store did not create at start (see *mounts*) appears with its
        # first object, with the operator's default mode, as the local store would create it.
        self._locate(key)[0].mkdir(parents=True, exist_ok=True)
        self._make_parents(final)
        # Re-resolve now that the parents exist: a symlinked parent created by someone else in the
        # meantime must not move the write outside the root.
        if self.path_for(key) != final:
            raise ValueError(f"object key escapes the store root: {key!r}")
        # Probe the final name now, so a name the filesystem rejects (ENAMETOOLONG) fails before
        # any body is read rather than at the commit.
        with contextlib.suppress(FileNotFoundError):
            os.lstat(final)
        return PendingPut(final)

    def _read_path(self, key: str) -> Path | None:
        # The read and delete half reports an unsafe key as absent, as a bucket would, so a caller
        # gets a miss (404) rather than a 500. The write and signing half keeps raising.
        try:
            return self.path_for(key)
        except ValueError:
            return None

    def exists(self, key: str) -> bool:
        path = self._read_path(key)
        return path is not None and path.is_file()

    def get_bytes(self, key: str) -> bytes | None:
        path = self._read_path(key)
        if path is None:
            return None
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            return None

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        # The content type is derived from the key when the object is served, so nothing stores it.
        del content_type
        pending = self.begin_put(key)
        try:
            pending.write(data)
        except BaseException:
            pending.abort()
            raise
        pending.commit()

    def put_file(self, key: str, path: Path, *, content_type: str = "") -> None:
        del content_type
        pending = self.begin_put(key)
        try:
            with path.open("rb") as src:
                shutil.copyfileobj(src, pending.file)
        except BaseException:
            pending.abort()
            raise
        pending.commit()

    def _signed_url(self, method: str, key: str, content_type: str, ttl: int) -> str:
        self.path_for(key)  # never sign a URL the routes would refuse
        expires, sig = self.signer.sign(method, key, content_type, ttl)
        params = {"expires": str(expires), "sig": sig}
        if method == "PUT":
            params["ct"] = content_type
        return f"{self._base_url}{SIGNED_STORE_PREFIX}{quote(key)}?{urlencode(params)}"

    def presigned_url(self, key: str) -> str:
        return self._signed_url("GET", key, "", self._ttl)

    def presigned_put_url(self, key: str, *, content_type: str = "", ttl: int = _PUT_TTL) -> str:
        return self._signed_url("PUT", key, content_type, ttl)

    def list_keys(self, prefix: str) -> list[str]:
        if prefix and not valid_relative_key(prefix):
            raise ValueError(f"invalid object key prefix: {prefix!r}")
        for mount_prefix, mounted in self._mounts:
            if prefix.startswith(mount_prefix):
                return _walk(mounted, mount_prefix, prefix)
        # A shorter prefix spans several directories: the root's objects outside every mount's
        # prefix, plus each mount's objects whose full key still matches.
        keys = [
            k
            for k in _walk(self._root, "", prefix)
            if not any(k.startswith(p) for p, _ in self._mounts)
        ]
        for mount_prefix, mounted in self._mounts:
            if mount_prefix.startswith(prefix):
                keys += _walk(mounted, mount_prefix, mount_prefix)
        return sorted(keys) if self._mounts else keys

    def delete_key(self, key: str) -> None:
        path = self._read_path(key)
        # A directory is no object; leave it, as a bucket has none to delete.
        if path is not None and path.is_file():
            path.unlink(missing_ok=True)
            self._prune_empty_parents(path, self._locate(key)[0])

    def _prune_empty_parents(self, path: Path, base: Path) -> None:
        # A bucket has no directories, so a purged run must leave none behind either: an empty
        # `runs/<id>/` would still read as a run tree. Stops at *base*, which is never removed.
        parent = path.parent
        while parent != base and parent.is_relative_to(base):
            try:
                parent.rmdir()
            except OSError:  # not empty (or already gone): nothing further up can be empty either
                return
            parent = parent.parent

    def delete_keys(self, keys: Iterable[str]) -> None:
        for key in keys:
            self.delete_key(key)


def _owner_only_dir(path: Path) -> Path:
    """Create *path* owner-only (``0700``) and return it resolved; a symlink is refused, since it
    would move every object (and the ``chmod``) to a directory someone else chose."""
    if path.is_symlink():
        raise ValueError(f"refusing to use a symlinked store root: {path}")
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(_DIR_MODE)
    return path.resolve()


def _walk(base: Path, key_prefix: str, prefix: str) -> list[str]:
    """The keys under *base* (each named *key_prefix* + its path) that start with *prefix*."""
    # Walk only the directory the prefix ends in, then filter on the full string, which keeps S3's
    # semantics for a prefix that stops mid-name (``artifacts/run-1`` matches ``run-10``).
    rel_prefix = prefix[len(key_prefix) :]
    start = (base / rel_prefix.rpartition("/")[0]).resolve()
    # A symlinked directory anywhere along the prefix would move the walk outside the base.
    if not start.is_relative_to(base) or not start.is_dir():
        return []
    keys: list[str] = []
    for dirpath, dirnames, filenames in os.walk(start):
        here = Path(dirpath)
        # os.walk does not descend into symlinked directories; drop them from the listing too.
        dirnames[:] = [d for d in dirnames if not (here / d).is_symlink()]
        for name in filenames:
            full = here / name
            if name.startswith(_STAGING_PREFIX):
                continue
            try:
                if not stat.S_ISREG(os.lstat(full).st_mode):
                    continue
            except FileNotFoundError:  # removed between the walk and the check: not listed
                continue
            if not full.resolve().is_relative_to(base):
                continue
            key = key_prefix + full.relative_to(base).as_posix()
            if key.startswith(prefix):
                keys.append(key)
    return sorted(keys)
