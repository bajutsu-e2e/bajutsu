"""`bajutsu runner build --device` (BE-0456): argument handling and error reporting."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.common.platform_lifecycle.environments.device_runner import build, staging
from bajutsu.common.platform_lifecycle.environments.device_runner.errors import DeviceRunnerError
from bajutsu.common.platform_lifecycle.environments.device_runner.signing import SIGNING_FILE_ENV

runner = CliRunner()


@pytest.fixture
def signing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "signing.yaml"
    path.write_text("bundleIdPrefix: com.acme\nteamId: TEAM1\n")
    monkeypatch.delenv(SIGNING_FILE_ENV, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(staging, "runner_source_root", lambda: tmp_path / "sources")
    return path


def _fake_build(products: Path, calls: list[dict[str, Any]]) -> Any:
    def fake(signing: Any, **kwargs: Any) -> Path:
        calls.append({"signing": signing, **kwargs})
        products.mkdir(parents=True, exist_ok=True)
        runner_path = products / build.RUNNER_NAME
        runner_path.write_text("<plist/>")
        (products / "Debug-iphoneos").mkdir(exist_ok=True)
        return runner_path

    return fake


def test_build_without_device_is_a_usage_error() -> None:
    result = runner.invoke(app, ["runner", "build"])
    assert result.exit_code == 2
    assert "--device" in result.output


def test_build_prints_the_cached_xctestrun(
    tmp_path: Path, signing_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    products = tmp_path / "cache" / "KEY" / "Products"
    monkeypatch.setattr(build, "build_device_runner", _fake_build(products, calls))

    result = runner.invoke(
        app, ["runner", "build", "--device", "--signing", str(signing_file), "--force"]
    )

    assert result.exit_code == 0, result.output
    assert str(products / build.RUNNER_NAME) in result.output
    assert calls[0]["force"] is True
    assert calls[0]["source_root"] == tmp_path / "sources"
    assert calls[0]["signing"].host_bundle_id == "com.acme.bajutsu.runner-host"


def test_out_copies_the_whole_products_directory(
    tmp_path: Path, signing_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    products = tmp_path / "cache" / "KEY" / "Products"
    monkeypatch.setattr(build, "build_device_runner", _fake_build(products, []))
    out = tmp_path / "out"

    result = runner.invoke(
        app, ["runner", "build", "--device", "--signing", str(signing_file), "--out", str(out)]
    )

    assert result.exit_code == 0, result.output
    assert (out / build.RUNNER_NAME).is_file()
    assert (out / "Debug-iphoneos").is_dir()


def test_out_refuses_a_non_empty_directory(
    tmp_path: Path, signing_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(build, "build_device_runner", _fake_build(tmp_path / "P", calls))
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale.txt").write_text("old")
    result = runner.invoke(
        app, ["runner", "build", "--device", "--signing", str(signing_file), "--out", str(out)]
    )
    assert result.exit_code == 1
    assert "must be an empty or absent directory" in result.output
    assert calls == []


def test_out_refuses_a_regular_file(tmp_path: Path, signing_file: Path) -> None:
    out = tmp_path / "out.txt"
    out.write_text("x")
    result = runner.invoke(
        app, ["runner", "build", "--device", "--signing", str(signing_file), "--out", str(out)]
    )
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "must be an empty or absent directory" in result.output


def test_build_reports_a_missing_signing_file(signing_file: Path) -> None:
    del signing_file  # only its environment setup matters: no file is found anywhere
    result = runner.invoke(app, ["runner", "build", "--device"])
    assert result.exit_code == 1
    assert "no signing file found" in result.output


def test_build_reports_an_invalid_signing_file(tmp_path: Path, signing_file: Path) -> None:
    signing_file.write_text("teamId: TEAM1\n")
    result = runner.invoke(app, ["runner", "build", "--device", "--signing", str(signing_file)])
    assert result.exit_code == 1
    assert "invalid signing file" in result.output


def test_build_reports_a_build_failure(signing_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: Any, **__: Any) -> Path:
        raise DeviceRunnerError("cannot build the device runner: xcodegen is missing")

    monkeypatch.setattr(build, "build_device_runner", fail)
    result = runner.invoke(app, ["runner", "build", "--device", "--signing", str(signing_file)])
    assert result.exit_code == 1
    assert "error: cannot build the device runner: xcodegen is missing" in result.output


def test_build_reports_missing_runner_sources(
    signing_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(staging, "runner_source_root", lambda: None)
    result = runner.invoke(app, ["runner", "build", "--device", "--signing", str(signing_file)])
    assert result.exit_code == 1
    assert "ships no XCUITest runner sources" in result.output
