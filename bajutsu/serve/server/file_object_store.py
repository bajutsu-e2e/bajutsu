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
from collections.abc import Callable, Iterable
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


class FileObjectStore:
    """`ObjectStore` over a directory, whose signed URLs point at this server's store routes."""

    def __init__(
        self, root: Path, base_url: str, signer: UrlSigner, *, presign_ttl: int = _PRESIGN_TTL
    ) -> None:
        if root.is_symlink():
            raise ValueError(f"refusing to use a symlinked store root: {root}")
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(_DIR_MODE)
        self._root = root.resolve()
        self._base_url = base_url.rstrip("/")
        self.signer = signer
        self._ttl = presign_ttl

    @property
    def root(self) -> Path:
        """The resolved directory every object lives under."""
        return self._root

    def path_for(self, key: str) -> Path:
        """The file *key* names, confined to the store root.

        Raises:
            ValueError: *key* is not a safe relative key, is not in canonical form (see
                `_canonical`), or resolves (through ``..`` or a symlink) outside the root or onto
                the root itself.
        """
        if not valid_relative_key(key) or not _canonical(key):
            raise ValueError(f"invalid object key: {key!r}")
        resolved = (self._root / key).resolve()
        if resolved == self._root or not resolved.is_relative_to(self._root):
            raise ValueError(f"object key escapes the store root: {key!r}")
        return resolved

    def _make_parents(self, path: Path) -> None:
        missing: list[Path] = []
        parent = path.parent
        while parent != self._root and not parent.exists():
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
        # Walk only the directory the prefix ends in, then filter on the full string, which keeps
        # S3's semantics for a prefix that stops mid-name (``artifacts/run-1`` matches ``run-10``).
        base = (self._root / prefix.rpartition("/")[0]).resolve()
        # A symlinked directory anywhere along the prefix would move the walk outside the root.
        if not base.is_relative_to(self._root) or not base.is_dir():
            return []
        keys: list[str] = []
        for dirpath, dirnames, filenames in os.walk(base):
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
                if not full.resolve().is_relative_to(self._root):
                    continue
                key = full.relative_to(self._root).as_posix()
                if key.startswith(prefix):
                    keys.append(key)
        return sorted(keys)

    def delete_key(self, key: str) -> None:
        path = self._read_path(key)
        if (
            path is not None and path.is_file()
        ):  # a directory is no object; leave it, as a bucket has none to delete
            path.unlink(missing_ok=True)

    def delete_keys(self, keys: Iterable[str]) -> None:
        for key in keys:
            self.delete_key(key)
