"""`bajutsu worker --worker-config` (BE-0450): validated at startup, handed to each leased `run`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import typer

from bajutsu.serve.cli import worker as worker_mod
from bajutsu.serve.server.worker_job import _with_worker_config


def test_no_flag_leaves_every_run_on_the_default() -> None:
    path, cap = worker_mod._worker_file_or_exit(None)
    assert path is None
    assert cap.is_local


def test_a_valid_file_is_resolved_to_an_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "w.yaml").write_text("version: 1\ndrivers: [playwright]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert worker_mod._worker_file_or_exit(Path("w.yaml"))[0] == tmp_path.resolve() / "w.yaml"


def test_an_invalid_file_stops_the_worker_before_it_leases(tmp_path: Path) -> None:
    path = tmp_path / "w.yaml"
    path.write_text("version: 1\ndrivers: [selenium]\n", encoding="utf-8")
    with pytest.raises(typer.BadParameter, match="unknown driver"):
        worker_mod._worker_file_or_exit(path)


def test_a_device_cloud_file_loads_with_its_environment(tmp_path: Path) -> None:
    # The Device Farm dispatch runs on such a worker (BE-0448), so the file is no longer refused.
    path = tmp_path / "w.yaml"
    path.write_text("version: 1\nenvironment: devicefarm\ndrivers: [adb]\n", encoding="utf-8")
    assert worker_mod._worker_file_or_exit(path)[1].environment == "devicefarm"


def test_the_flag_is_inserted_into_a_leased_run(tmp_path: Path) -> None:
    cmd = [sys.executable, "-m", "bajutsu", "run", "--target", "demo"]
    path = tmp_path / "w.yaml"
    assert _with_worker_config(cmd, path) == [
        sys.executable,
        "-m",
        "bajutsu",
        "run",
        "--worker-config",
        str(path.resolve()),
        "--target",
        "demo",
    ]


@pytest.mark.parametrize("subcommand", ["record", "crawl", "triage"])
def test_other_commands_are_left_alone(tmp_path: Path, subcommand: str) -> None:
    cmd = [sys.executable, "-m", "bajutsu", subcommand, "--target", "demo"]
    assert _with_worker_config(cmd, tmp_path / "w.yaml") == cmd


def test_without_a_file_the_argv_is_unchanged() -> None:
    cmd = [sys.executable, "-m", "bajutsu", "run"]
    assert _with_worker_config(cmd, None) == cmd


def test_a_spec_supplied_flag_cannot_override_the_workers_file(tmp_path: Path) -> None:
    path = tmp_path / "w.yaml"
    for injected in (["--worker-config", "/elsewhere.yaml"], ["--worker-config=/elsewhere.yaml"]):
        cmd = [sys.executable, "-m", "bajutsu", "run", *injected, "--target", "demo"]
        out = _with_worker_config(cmd, path)
        assert out[4:6] == ["--worker-config", str(path.resolve())]
        assert "/elsewhere.yaml" not in " ".join(out)
        assert out[6:] == ["--target", "demo"]


def test_a_default_worker_strips_a_spec_supplied_flag() -> None:
    # A worker with no file runs under the shipped default; the spec must not swap in its own.
    for injected in (["--worker-config", "/elsewhere.yaml"], ["--worker-config=/elsewhere.yaml"]):
        cmd = [sys.executable, "-m", "bajutsu", "run", *injected, "--target", "demo"]
        assert _with_worker_config(cmd, None) == [
            sys.executable,
            "-m",
            "bajutsu",
            "run",
            "--target",
            "demo",
        ]


def test_an_unrecognised_run_argv_fails_instead_of_running_on_the_default(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unrecognised run argv"):
        _with_worker_config(["bajutsu", "run", "--target", "demo"], tmp_path / "w.yaml")


def test_execute_job_spec_hands_the_real_run_argv_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.serve.commands import run_command
    from bajutsu.serve.server import worker_job

    seen: list[list[str]] = []
    monkeypatch.setattr(worker_job, "run_job", lambda state, job, **_kw: seen.append(job.cmd))
    path = tmp_path / "w.yaml"
    worker_job.execute_job_spec(
        {"job_id": "j", "cmd": run_command("s.yaml", "demo")}, cwd=tmp_path, worker_config=path
    )
    assert seen and seen[0][2:5] == ["bajutsu.run.internal", "--worker-config", str(path.resolve())]


def test_the_worker_loop_hands_its_file_to_every_leased_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Stop(Exception):
        pass

    monkeypatch.chdir(tmp_path)
    (tmp_path / "w.yaml").write_text("version: 1\ndrivers: [xcuitest]\n", encoding="utf-8")
    leases = 0

    def fake_post(url: str, body: dict[str, object], *, token: str | None = None) -> object:
        nonlocal leases
        if url.endswith("/lease"):
            leases += 1
            if leases == 1:
                return 200, {"job_id": "j1", "spec": {"cmd": "run"}}
            raise _Stop
        return 200, {}

    seen: list[object] = []

    class _Job:
        def view(self) -> dict[str, object]:
            return {"ok": True}

    def fake_execute(*_a: object, **kwargs: object) -> _Job:
        seen.append(kwargs.get("worker_config"))
        return _Job()

    monkeypatch.setattr(worker_mod, "_post_json", fake_post)
    monkeypatch.setattr(worker_mod, "execute_job_spec", fake_execute)
    monkeypatch.setattr(worker_mod, "_advertised_capabilities", lambda *_a: ["platform:ios"])
    with pytest.raises(_Stop):
        worker_mod.worker(
            server_url="http://cp",
            poll_interval=1,
            heartbeat_interval=5,
            worker_id="w1",
            worker_config=Path("w.yaml"),
        )
    assert seen == [tmp_path.resolve() / "w.yaml"]


# --- the internal run module the spec builders emit (BE-0449) ------------------------------------

_INTERNAL = [sys.executable, "-m", "bajutsu.run.internal"]


def test_the_flag_is_inserted_into_the_internal_run_module(tmp_path: Path) -> None:
    path = tmp_path / "w.yaml"
    assert _with_worker_config([*_INTERNAL, "--target", "demo"], path) == [
        *_INTERNAL,
        "--worker-config",
        str(path.resolve()),
        "--target",
        "demo",
    ]


def test_the_internal_run_module_strips_a_spec_supplied_flag(tmp_path: Path) -> None:
    for injected in (["--worker-config", "/elsewhere.yaml"], ["--worker-config=/elsewhere.yaml"]):
        cmd = [*_INTERNAL, *injected, "--target", "demo"]
        assert _with_worker_config(cmd, None) == [*_INTERNAL, "--target", "demo"]
        out = _with_worker_config(cmd, tmp_path / "w.yaml")
        assert "/elsewhere.yaml" not in " ".join(out)


def test_an_unrecognised_internal_run_argv_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unrecognised run argv"):
        _with_worker_config(
            ["python", "bajutsu.run.internal", "--target", "x"], tmp_path / "w.yaml"
        )


def test_a_device_cloud_worker_without_a_provider_stops_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Leasing jobs it could only fail is worse than not starting (BE-0448).
    monkeypatch.delenv("DEVICEFARM_PROJECT_ARN", raising=False)
    with pytest.raises(typer.BadParameter, match="registered no provider"):
        worker_mod._register_environment_or_exit("devicefarm")
