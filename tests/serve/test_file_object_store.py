"""The ``file://`` object store and its signed-URL routes (BE-0449 unit 1).

Covers the HMAC `UrlSigner` (round trip and every rejection), `FileObjectStore` against the
`ObjectStore` contract plus key confinement, and the FastAPI routes that serve its URLs, driven
through TestClient over a real `ServeState`. No network, no mocks of the store.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from _shared import FakeObjectStore, project
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from bajutsu import serve as srv
from bajutsu.common.run_meta.object_store import ObjectStore
from bajutsu.serve import gate
from bajutsu.serve.server.app import make_app
from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore
from bajutsu.serve.server.file_object_store import FileObjectStore, PendingPut, UrlSigner
from bajutsu.serve.state import SessionManager
from bajutsu.serve.uploads import bounded_zip_receiver

_KEY = b"k" * 32
_BASE = "http://testserver"


class _Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _store(tmp_path: Path, clock: _Clock | None = None) -> FileObjectStore:
    return FileObjectStore(tmp_path / "store", _BASE, UrlSigner(_KEY, clock=clock or _Clock()))


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


# --- UrlSigner ---


def test_signer_round_trip() -> None:
    signer = UrlSigner(_KEY, clock=_Clock())
    expires, sig = signer.sign("PUT", "a/b.png", "image/png", 60)
    assert expires == 1_000_060
    assert signer.verify("PUT", "a/b.png", "image/png", expires, sig)


@pytest.mark.parametrize(
    ("method", "key", "ct", "delta", "sig_suffix"),
    [
        ("GET", "a/b.png", "image/png", 0, ""),  # wrong method
        ("PUT", "a/c.png", "image/png", 0, ""),  # wrong key
        ("PUT", "a/b.png", "text/html", 0, ""),  # wrong content type
        ("PUT", "a/b.png", "image/png", 1, ""),  # extended expiry
        ("PUT", "a/b.png", "image/png", 0, "0"),  # tampered signature
    ],
)
def test_signer_rejects_any_changed_field(
    method: str, key: str, ct: str, delta: int, sig_suffix: str
) -> None:
    signer = UrlSigner(_KEY, clock=_Clock())
    expires, sig = signer.sign("PUT", "a/b.png", "image/png", 60)
    assert not signer.verify(method, key, ct, expires + delta, sig + sig_suffix)


def test_signer_rejects_an_expired_url() -> None:
    clock = _Clock()
    signer = UrlSigner(_KEY, clock=clock)
    expires, sig = signer.sign("GET", "k", "", 60)
    clock.now += 61
    assert not signer.verify("GET", "k", "", expires, sig)


def test_signer_rejects_another_keys_signature() -> None:
    expires, sig = UrlSigner(b"x" * 32, clock=_Clock()).sign("GET", "k", "", 60)
    assert not UrlSigner(_KEY, clock=_Clock()).verify("GET", "k", "", expires, sig)


def test_signer_refuses_an_empty_key() -> None:
    with pytest.raises(ValueError, match="empty"):
        UrlSigner(b"")


# --- FileObjectStore ---


def test_store_satisfies_the_object_store_protocol(tmp_path: Path) -> None:
    store: ObjectStore = _store(tmp_path)
    assert store.exists("x") is False


def test_store_crud_with_owner_only_modes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert _mode(store.root) == 0o700
    assert store.get_bytes("artifacts/r1/a.txt") is None
    store.put_bytes("artifacts/r1/a.txt", b"hello", content_type="text/plain")
    assert store.exists("artifacts/r1/a.txt")
    assert store.get_bytes("artifacts/r1/a.txt") == b"hello"
    assert _mode(store.root / "artifacts/r1/a.txt") == 0o600
    assert _mode(store.root / "artifacts") == 0o700
    assert _mode(store.root / "artifacts/r1") == 0o700
    store.put_bytes("artifacts/r1/a.txt", b"again")  # overwrite in place
    assert store.get_bytes("artifacts/r1/a.txt") == b"again"
    src = tmp_path / "video.mp4"
    src.write_bytes(b"\x00" * 1000)
    store.put_file("artifacts/r1/video.mp4", src, content_type="video/mp4")
    assert store.get_bytes("artifacts/r1/video.mp4") == b"\x00" * 1000
    store.delete_key("artifacts/r1/a.txt")
    store.delete_key("artifacts/r1/a.txt")  # idempotent
    assert not store.exists("artifacts/r1/a.txt")
    store.delete_keys(["artifacts/r1/video.mp4", "artifacts/r1/never"])
    assert store.list_keys("") == []


def test_store_directory_is_not_an_object(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put_bytes("d/f", b"x")
    assert store.exists("d") is False
    assert store.get_bytes("d") is None
    store.delete_key("d")  # a no-op, not an error
    assert store.exists("d/f")


def test_list_keys_matches_s3_prefix_semantics(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for key in ("artifacts/run-1/a", "artifacts/run-10/b", "artifacts/run-2/c", "scenarios/x"):
        store.put_bytes(key, b"x")
    assert store.list_keys("artifacts/run-1") == ["artifacts/run-1/a", "artifacts/run-10/b"]
    assert store.list_keys("artifacts/run-1/") == ["artifacts/run-1/a"]
    assert store.list_keys("") == [
        "artifacts/run-1/a",
        "artifacts/run-10/b",
        "artifacts/run-2/c",
        "scenarios/x",
    ]
    assert store.list_keys("nothing/") == []


def test_list_keys_skips_symlinks_and_staging_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put_bytes("a/real", b"x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"s")
    (store.root / "a" / "linkdir").symlink_to(outside)
    (store.root / "a" / "linkfile").symlink_to(outside / "secret")
    (store.root / "a" / ".bajutsu-put-abc").write_bytes(b"partial")
    assert store.list_keys("") == ["a/real"]
    (store.root / "top").symlink_to(outside)
    assert store.list_keys("top/") == []


@pytest.mark.parametrize("key", ["", "/abs", "../up", "a/../../up", "a\x00b", "a\\..\\up", "."])
def test_store_refuses_unsafe_keys(tmp_path: Path, key: str) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match=r"invalid object key|escapes"):
        store.put_bytes(key, b"x")
    with pytest.raises(ValueError, match=r"invalid object key|escapes"):
        store.presigned_url(key)


@pytest.mark.parametrize("key", ["", "/abs", "../up", "a/../../up", "a\x00b", "a\\..\\up", "."])
def test_store_reads_and_deletes_report_an_unsafe_key_as_absent(tmp_path: Path, key: str) -> None:
    # The same contract as S3/GCS: a key no object can live at is a miss, never an error.
    store = _store(tmp_path)
    assert store.exists(key) is False
    assert store.get_bytes(key) is None
    store.delete_key(key)
    store.delete_keys([key, "also-absent"])


def test_artifact_store_over_the_file_store_misses_a_backslash_traversal(tmp_path: Path) -> None:
    # `_key` screens only POSIX traversal, so this reaches the store, which must answer "absent".
    store = _store(tmp_path)
    artifacts = ObjectStorageArtifactStore(store, prefix="artifacts/")
    rel = "a\\..\\..\\x"
    assert artifacts.get(rel) is None
    assert artifacts.exists(rel) is False
    assert artifacts.open_bytes(rel) is None


def test_store_refuses_an_unsafe_list_prefix(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid object key prefix"):
        _store(tmp_path).list_keys("../")


def test_store_refuses_a_symlink_escape(tmp_path: Path) -> None:
    store = _store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"s")
    (store.root / "esc").symlink_to(outside)
    assert store.get_bytes("esc/secret") is None  # a read through the escape is a miss
    assert not store.exists("esc/secret")
    store.delete_key("esc/secret")
    assert (outside / "secret").exists()
    with pytest.raises(ValueError, match="escapes"):
        store.put_bytes("esc/new", b"x")
    assert not (outside / "new").exists()


def test_store_refuses_a_symlinked_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "store").symlink_to(real)
    with pytest.raises(ValueError, match="symlinked store root"):
        FileObjectStore(tmp_path / "store", _BASE, UrlSigner(_KEY))


def test_presigned_urls_point_at_the_store_routes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    get = urlsplit(store.presigned_url("artifacts/r 1/a.png"))
    assert f"{get.scheme}://{get.netloc}" == _BASE
    assert get.path == "/api/store/objects/artifacts/r%201/a.png"
    assert set(parse_qs(get.query)) == {"expires", "sig"}
    put = urlsplit(store.presigned_put_url("a.png", content_type="image/png", ttl=5))
    query = parse_qs(put.query)
    assert query["ct"] == ["image/png"]
    assert int(query["expires"][0]) == 1_000_005


def test_a_failed_put_file_leaves_no_staging_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.put_file("a/b", tmp_path / "missing")
    assert list((store.root / "a").iterdir()) == []


# --- routes ---


def _state(
    tmp_path: Path, store: ObjectStore | None, *, token: str | None = None
) -> srv.ServeState:
    _scn, cfg, runs = project(tmp_path)
    return srv.ServeState(
        config=cfg,
        runs_dir=runs,
        root=tmp_path,
        cwd=tmp_path,
        auth=SessionManager(token=token),
        object_store=store,
    )


def _client(tmp_path: Path, *, token: str | None = "s3cret") -> tuple[TestClient, FileObjectStore]:
    store = _store(tmp_path)
    # A token makes the routes prove they need no serve credential, only the signature.
    return TestClient(make_app(_state(tmp_path, store, token=token))), store


def _rel(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}"


def test_signed_get_serves_the_object(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    store.put_bytes("artifacts/r1/shot.png", b"PNG")
    resp = client.get(_rel(store.presigned_url("artifacts/r1/shot.png")))
    assert resp.status_code == 200
    assert resp.content == b"PNG"
    assert resp.headers["content-type"] == "image/png"
    # The shared hardening headers still apply to an open route.
    assert resp.headers["x-content-type-options"] == "nosniff"
    # Worker-written content is sandboxed so it cannot act as this origin.
    assert resp.headers["content-security-policy"] == "sandbox"


def test_signed_get_of_an_absent_object_is_404(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    assert client.get(_rel(store.presigned_url("nope.txt"))).status_code == 404


def test_signed_get_escaping_through_a_symlink_is_404(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "s").write_bytes(b"secret")
    (store.root / "esc").symlink_to(tmp_path / "outside")
    expires, sig = store.signer.sign("GET", "esc/s", "", 60)
    resp = client.get(f"/api/store/objects/esc/s?expires={expires}&sig={sig}")
    assert resp.status_code == 404


def test_tampered_expired_and_unsigned_gets_are_403(tmp_path: Path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    client = TestClient(make_app(_state(tmp_path, store, token="s3cret")))
    store.put_bytes("a.txt", b"x")
    url = _rel(store.presigned_url("a.txt"))
    assert client.get(url.replace("sig=", "sig=0")).status_code == 403
    assert client.get("/api/store/objects/a.txt").status_code == 403
    assert client.get("/api/store/objects/a.txt?expires=soon&sig=x").status_code == 403
    # A GET signature names another object only for that object.
    store.put_bytes("b.txt", b"y")
    assert client.get(url.replace("a.txt", "b.txt", 1)).status_code == 403
    clock.now += 10_000
    assert client.get(url).status_code == 403


def test_signed_put_stores_the_body(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    url = _rel(store.presigned_put_url("artifacts/r1/log.txt", content_type="text/plain"))
    resp = client.put(url, content=b"line\n", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 200
    assert store.get_bytes("artifacts/r1/log.txt") == b"line\n"
    assert _mode(store.root / "artifacts/r1/log.txt") == 0o600


def test_put_with_another_content_type_is_refused(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    url = _rel(store.presigned_put_url("a.png", content_type="image/png"))
    resp = client.put(url, content=b"<html>", headers={"Content-Type": "text/html"})
    assert resp.status_code == 403
    # Rewriting the bound type in the query breaks the signature instead.
    forged = url.replace("ct=image%2Fpng", "ct=text%2Fhtml")
    assert (
        client.put(forged, content=b"x", headers={"Content-Type": "text/html"}).status_code == 403
    )
    assert not store.exists("a.png")


def test_a_get_signature_does_not_authorize_a_put(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    url = _rel(store.presigned_url("a.txt"))
    assert client.put(url + "&ct=", content=b"x").status_code == 403


def test_oversized_put_is_413(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bounded_zip_receiver, "MAX_UPLOAD_BYTES", 4)
    client, store = _client(tmp_path)
    url = _rel(store.presigned_put_url("a.bin", content_type="application/octet-stream"))
    headers = {"Content-Type": "application/octet-stream"}
    assert client.put(url, content=b"12345", headers=headers).status_code == 413
    # A streamed body with no Content-Length is counted as it arrives.
    resp = client.put(url, content=iter([b"12", b"345"]), headers=headers)
    assert resp.status_code == 413
    assert not store.exists("a.bin")
    assert list(store.root.iterdir()) == []


def test_routes_are_absent_without_a_file_store(tmp_path: Path) -> None:
    for i, store in enumerate((None, FakeObjectStore())):
        (tmp_path / str(i)).mkdir()
        app = make_app(_state(tmp_path / str(i), store))
        paths = {r.path for r in app.routes if isinstance(r, APIRoute)}
        assert not any(p.startswith(gate.SIGNED_STORE_PREFIX) for p in paths)


def test_store_paths_stay_gated_without_a_file_store(tmp_path: Path) -> None:
    # With no file store the prefix is not open: an unauthenticated request meets the credential gate.
    client = TestClient(make_app(_state(tmp_path, None, token="s3cret")))
    assert client.get("/api/store/objects/a.txt?expires=1&sig=x").status_code == 401


def test_is_signed_store_route_covers_only_get_and_put_under_the_prefix() -> None:
    assert gate.is_signed_store_route("GET", "/api/store/objects/a")
    assert gate.is_signed_store_route("PUT", "/api/store/objects/a/b")
    assert not gate.is_signed_store_route("POST", "/api/store/objects/a")
    assert not gate.is_signed_store_route("DELETE", "/api/store/objects/a")
    assert not gate.is_signed_store_route("GET", "/api/store/other")


def test_put_whose_key_escapes_through_a_symlink_is_400(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    (tmp_path / "outside").mkdir()
    (store.root / "esc").symlink_to(tmp_path / "outside")
    expires, sig = store.signer.sign("PUT", "esc/new", "text/plain", 60)
    resp = client.put(
        f"/api/store/objects/esc/new?expires={expires}&sig={sig}&ct=text%2Fplain",
        content=b"x",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400
    assert not (tmp_path / "outside" / "new").exists()


@pytest.mark.parametrize(
    ("error", "status"),
    [(ClientDisconnect(), 400), (OSError("disk full"), 500), (RuntimeError(), 500)],
)
def test_a_failed_put_leaves_neither_object_nor_staging_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, status: int
) -> None:
    client, store = _client(tmp_path)

    def _fail(self: PendingPut, chunk: bytes) -> None:
        raise error

    monkeypatch.setattr(PendingPut, "write", _fail)
    url = _rel(store.presigned_put_url("d/a.txt", content_type="text/plain"))
    resp = client.put(url, content=b"x", headers={"Content-Type": "text/plain"})
    assert resp.status_code == status
    assert list((store.root / "d").iterdir()) == []


def test_put_bytes_failure_leaves_no_staging_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)

    def _fail(self: PendingPut, chunk: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(PendingPut, "write", _fail)
    with pytest.raises(OSError, match="disk full"):
        store.put_bytes("d/a.txt", b"x")
    assert list((store.root / "d").iterdir()) == []


def test_a_failed_commit_leaves_no_staging_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    (store.root / "d").mkdir()
    # Replacing an existing directory with a file fails at commit time.
    with pytest.raises(OSError):  # noqa: PT011 - the errno differs by platform
        store.put_bytes("d", b"x")
    assert sorted(p.name for p in store.root.iterdir()) == ["d"]


def test_put_whose_key_conflicts_with_an_existing_file_is_409(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    store.put_bytes("f", b"x")
    url = _rel(store.presigned_put_url("f/child", content_type="text/plain"))
    resp = client.put(url, content=b"y", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 409
    assert str(store.root) not in resp.text


def test_put_whose_key_is_too_long_for_the_filesystem_is_409(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    key = "d/" + "x" * 1000
    url = _rel(store.presigned_put_url(key, content_type="text/plain"))
    resp = client.put(url, content=b"y", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 409


@pytest.mark.parametrize("err", [errno.ENOSPC, errno.EACCES, errno.EIO])
def test_put_whose_begin_fails_with_a_server_side_io_error_is_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, err: int
) -> None:
    client, store = _client(tmp_path)

    def _fail(self: FileObjectStore, key: str) -> PendingPut:
        raise OSError(err, os.strerror(err))

    monkeypatch.setattr(FileObjectStore, "begin_put", _fail)
    url = _rel(store.presigned_put_url("k", content_type="text/plain"))
    resp = client.put(url, content=b"y", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 500
    assert str(store.root) not in resp.text


@pytest.mark.parametrize("err", [errno.EEXIST, errno.ENOTDIR, errno.ENAMETOOLONG])
def test_put_whose_begin_fails_on_the_key_is_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, err: int
) -> None:
    client, store = _client(tmp_path)

    def _fail(self: FileObjectStore, key: str) -> PendingPut:
        raise OSError(err, os.strerror(err))

    monkeypatch.setattr(FileObjectStore, "begin_put", _fail)
    url = _rel(store.presigned_put_url("k", content_type="text/plain"))
    resp = client.put(url, content=b"y", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 409


def test_list_keys_skips_an_entry_removed_during_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.put_bytes("a/gone", b"x")
    store.put_bytes("a/kept", b"y")
    real_lstat = os.lstat

    def _lstat(path: os.PathLike[str] | str, *args: object, **kwargs: object) -> os.stat_result:
        # A concurrent purge removes `gone` after os.walk listed it but before the check.
        if Path(path).name == "gone":
            raise FileNotFoundError(path)
        return real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "lstat", _lstat)
    assert store.list_keys("a/") == ["a/kept"]


def test_get_of_a_name_too_long_for_the_filesystem_is_404(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    assert client.get(_rel(store.presigned_url("x" * 1000))).status_code == 404


def test_list_keys_never_walks_through_a_symlinked_intermediate_dir(tmp_path: Path) -> None:
    store = _store(tmp_path)
    outside = tmp_path / "outside"
    (outside / "b").mkdir(parents=True)
    (outside / "b" / "secret").write_bytes(b"s")
    (store.root / "a").symlink_to(outside)
    assert store.list_keys("a/b/") == []
    assert store.list_keys("a/b/sec") == []


def test_list_keys_skips_a_regular_file_reached_outside_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.put_bytes("a/inside", b"x")
    outside = tmp_path / "outside"
    real_resolve = Path.resolve

    def _resolve(self: Path, strict: bool = False) -> Path:
        # Simulate an entry swapped for one that resolves elsewhere between the walk and the check.
        return outside if self.name == "inside" else real_resolve(self, strict)

    monkeypatch.setattr(Path, "resolve", _resolve)
    assert store.list_keys("a/") == []
