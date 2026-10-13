"""The owner-only key files a local server start generates (BE-0449 unit 1).

Real files under tmp_path: creation mode, idempotent reload, refusal of every unsafe existing path,
agreement between concurrent creators, and the env-first secrets key.
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from bajutsu.serve.server import local_keys
from bajutsu.serve.server.local_keys import (
    ensure_state_dir,
    load_or_create_key,
    local_state_dir,
    secrets_key,
    signing_key,
)
from bajutsu.serve.server.secrets import fernet_from_env_or_local


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_state_dir_sits_beside_the_runs_directory(tmp_path: Path) -> None:
    assert local_state_dir(tmp_path / "runs") == tmp_path / ".bajutsu"


def test_creates_the_key_and_its_dir_owner_only_whatever_the_umask(tmp_path: Path) -> None:
    state = tmp_path / "state"
    old = os.umask(0)  # the most permissive umask: the modes must still come out owner-only
    try:
        key = load_or_create_key(state / "k.key", lambda: b"generated")
    finally:
        os.umask(old)
    assert key == b"generated"
    assert _mode(state) == 0o700
    assert _mode(state / "k.key") == 0o600
    assert (state / "k.key").read_bytes() == b"generated"
    # The staging file the key was linked from is gone.
    assert sorted(p.name for p in state.iterdir()) == [".gitignore", "k.key"]


def test_reload_returns_the_stored_key_without_regenerating(tmp_path: Path) -> None:
    path = tmp_path / "state" / "k.key"
    first = load_or_create_key(path, lambda: b"one")

    def _never() -> bytes:
        raise AssertionError("an existing key must not be regenerated")

    assert load_or_create_key(path, _never) == first


def test_refuses_a_symlinked_key_file(tmp_path: Path) -> None:
    state = ensure_state_dir(tmp_path / "state")
    target = tmp_path / "elsewhere"
    target.write_bytes(b"attacker")
    target.chmod(0o600)
    (state / "k.key").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        load_or_create_key(state / "k.key", lambda: b"x")


def test_refuses_a_group_readable_key_file(tmp_path: Path) -> None:
    state = ensure_state_dir(tmp_path / "state")
    (state / "k.key").write_bytes(b"leaky")
    (state / "k.key").chmod(0o640)
    with pytest.raises(ValueError, match="group or other"):
        load_or_create_key(state / "k.key", lambda: b"x")


def test_refuses_a_key_path_that_is_not_a_regular_file(tmp_path: Path) -> None:
    state = ensure_state_dir(tmp_path / "state")
    (state / "k.key").mkdir()
    with pytest.raises(ValueError, match="not a regular file"):
        load_or_create_key(state / "k.key", lambda: b"x")


def test_refuses_an_empty_key_file(tmp_path: Path) -> None:
    state = ensure_state_dir(tmp_path / "state")
    (state / "k.key").write_bytes(b"")
    (state / "k.key").chmod(0o600)
    with pytest.raises(ValueError, match="empty"):
        load_or_create_key(state / "k.key", lambda: b"x")


def test_refuses_a_key_file_owned_by_another_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state" / "k.key"
    load_or_create_key(path, lambda: b"mine")
    # Pretend to be another uid: the file (and the dir) now belong to "someone else".
    other = os.getuid() + 1
    monkeypatch.setattr(os, "getuid", lambda: other)
    with pytest.raises(ValueError, match="another account"):
        load_or_create_key(path, lambda: b"x")


def test_refuses_a_symlinked_state_dir(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "state").symlink_to(real)
    with pytest.raises(ValueError, match="symlinked state directory"):
        load_or_create_key(tmp_path / "state" / "k.key", lambda: b"x")


def test_refuses_a_state_dir_that_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "state").write_text("not a dir")
    with pytest.raises((ValueError, FileExistsError)):
        ensure_state_dir(tmp_path / "state")


def test_existing_state_dir_is_restricted_to_owner_only(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    state.chmod(0o755)
    ensure_state_dir(state)
    assert _mode(state) == 0o700


def test_the_loser_of_a_creation_race_reads_the_winners_key(tmp_path: Path) -> None:
    path = tmp_path / "state" / "k.key"

    def _lose() -> bytes:
        # Another start links its key into place while this one is still generating.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, b"winner")
        os.close(fd)
        return b"loser"

    assert load_or_create_key(path, _lose) == b"winner"
    assert sorted(p.name for p in path.parent.iterdir()) == [".gitignore", "k.key"]


def test_concurrent_creators_agree_on_one_key(tmp_path: Path) -> None:
    path = tmp_path / "state" / "k.key"
    barrier = threading.Barrier(8)
    results: list[bytes] = []
    lock = threading.Lock()

    def _start() -> None:
        barrier.wait()
        key = load_or_create_key(path, lambda: os.urandom(16))
        with lock:
            results.append(key)

    threads = [threading.Thread(target=_start) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 8
    assert len(set(results)) == 1
    assert results[0] == path.read_bytes()


def test_a_failing_generator_leaves_no_staging_file(tmp_path: Path) -> None:
    path = tmp_path / "state" / "k.key"

    def _boom() -> bytes:
        raise RuntimeError("no entropy")

    with pytest.raises(RuntimeError):
        load_or_create_key(path, _boom)
    assert [p.name for p in path.parent.iterdir()] == [".gitignore"]


def test_secrets_key_is_a_stable_fernet_key(tmp_path: Path) -> None:
    key = secrets_key(tmp_path)
    Fernet(key)  # a valid Fernet key
    assert secrets_key(tmp_path) == key
    assert _mode(tmp_path / local_keys.SECRETS_KEY_FILE) == 0o600


def test_secrets_key_rejects_a_file_that_is_not_a_fernet_key(tmp_path: Path) -> None:
    path = ensure_state_dir(tmp_path) / local_keys.SECRETS_KEY_FILE
    path.write_bytes(b"not-a-fernet-key")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="Fernet"):
        secrets_key(tmp_path)


def test_signing_key_is_32_stable_bytes(tmp_path: Path) -> None:
    key = signing_key(tmp_path)
    assert len(key) == 32
    assert signing_key(tmp_path) == key


def test_signing_key_rejects_a_wrong_length_file(tmp_path: Path) -> None:
    path = ensure_state_dir(tmp_path) / local_keys.SIGNING_KEY_FILE
    path.write_bytes(b"short")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="32-byte"):
        signing_key(tmp_path)


def test_env_secrets_key_wins_over_the_local_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_key = Fernet.generate_key()
    monkeypatch.setenv("BAJUTSU_SECRETS_KEY", env_key.decode("ascii"))
    token = fernet_from_env_or_local(tmp_path / "state").encrypt(b"v")
    assert Fernet(env_key).decrypt(token) == b"v"
    # The deployment key was used, so no local key was generated.
    assert not (tmp_path / "state").exists()


def test_without_env_the_local_secrets_key_is_generated_and_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BAJUTSU_SECRETS_KEY", raising=False)
    state = tmp_path / "state"
    token = fernet_from_env_or_local(state).encrypt(b"v")
    # A restart reads the same key back, so what it encrypted before still decrypts.
    assert fernet_from_env_or_local(state).decrypt(token) == b"v"
    assert Fernet(secrets_key(state)).decrypt(token) == b"v"


def test_refuses_a_key_file_owned_by_another_account_in_an_owned_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state" / "k.key"
    load_or_create_key(path, lambda: b"mine")
    other = os.getuid() + 1
    monkeypatch.setattr(local_keys, "ensure_state_dir", lambda p: p)  # the dir check passes
    monkeypatch.setattr(os, "getuid", lambda: other)
    with pytest.raises(ValueError, match=r"key file .* another account"):
        load_or_create_key(path, lambda: b"x")


def test_state_dir_ignores_itself_for_git(tmp_path: Path) -> None:
    state = ensure_state_dir(tmp_path / "state")
    ignore = state / ".gitignore"
    assert "*" in ignore.read_text().splitlines()
    ignore.write_text("*\n# edited\n")
    ensure_state_dir(state)  # an existing file is left as the user has it
    assert ignore.read_text() == "*\n# edited\n"
