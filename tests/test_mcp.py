"""Tests for the MCP server tools and resources."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import el


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# --- server factory ---


def test_create_server_returns_fastmcp(tmp_path: Path) -> None:
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text("defaults: {}\ntargets:\n  demo:\n    bundleId: com.demo\n", encoding="utf-8")
    from bajutsu.mcp import create_server

    server = create_server(config, tmp_path / "runs")
    assert server.name == "bajutsu"


# --- doctor tool ---


def test_doctor_tool_returns_score(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "defaults: {}\ntargets:\n  demo:\n    bundleId: com.demo\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    from bajutsu.drivers.fake import FakeDriver
    from bajutsu.mcp.tools import register_tools

    screen = [el("home.title", "Home", ["button"]), el("home.btn", "Go", ["button"])]
    driver = FakeDriver(screen)

    from fastmcp import FastMCP

    mcp = FastMCP("test")
    register_tools(mcp, config)

    monkeypatch.setattr("bajutsu.mcp.tools.make_driver", lambda actuator, udid: driver)
    monkeypatch.setattr(
        "bajutsu.mcp.tools.select_actuator", lambda backends, available=None: "fake"
    )

    result = _run(mcp.call_tool("bajutsu_doctor", {"target": "demo"}))
    text = result.content[0].text
    assert "Ready" in text


# --- run tool ---


def test_run_tool_returns_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text("defaults: {}\ntargets:\n  demo:\n    bundleId: com.demo\n", encoding="utf-8")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    scenario = tmp_path / "test.yaml"
    scenario.write_text("- name: a\n  steps:\n    - tap: { id: ok }\n", encoding="utf-8")

    import subprocess as sp

    def fake_run(*args: object, **kwargs: object) -> sp.CompletedProcess[str]:
        return sp.CompletedProcess(args=[], returncode=0, stdout="done\n", stderr="")

    monkeypatch.setattr("bajutsu.mcp.tools.subprocess.run", fake_run)

    from fastmcp import FastMCP

    from bajutsu.mcp.tools import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, config)

    result = _run(mcp.call_tool("bajutsu_run", {"target": "demo", "scenario": str(scenario)}))
    text = result.content[0].text
    assert "PASS" in text


def test_run_tool_returns_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text("defaults: {}\ntargets:\n  demo:\n    bundleId: com.demo\n", encoding="utf-8")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    import subprocess as sp

    def fake_run(*args: object, **kwargs: object) -> sp.CompletedProcess[str]:
        return sp.CompletedProcess(args=[], returncode=1, stdout="", stderr="step 0 failed\n")

    monkeypatch.setattr("bajutsu.mcp.tools.subprocess.run", fake_run)

    from fastmcp import FastMCP

    from bajutsu.mcp.tools import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, config)

    result = _run(mcp.call_tool("bajutsu_run", {"target": "demo"}))
    text = result.content[0].text
    assert "FAIL" in text


# --- resources ---


def test_manifest_resource_reads_file(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run1"
    run_dir.mkdir(parents=True)
    manifest = {"runId": "run1", "ok": True}
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    from fastmcp import FastMCP

    from bajutsu.mcp.resources import register_resources

    mcp = FastMCP("test")
    register_resources(mcp, runs_dir)

    result = _run(mcp.read_resource("bajutsu://runs/run1/manifest.json"))
    text = result.contents[0].content
    assert json.loads(text) == manifest


def test_manifest_resource_errors_on_missing_run(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    from fastmcp import FastMCP

    from bajutsu.mcp.resources import register_resources

    mcp = FastMCP("test")
    register_resources(mcp, runs_dir)

    with pytest.raises(Exception, match="no manifest"):
        _run(mcp.read_resource("bajutsu://runs/nonexistent/manifest.json"))


def test_safe_run_path_rejects_traversal(tmp_path: Path) -> None:
    from bajutsu.mcp.resources import _safe_run_path

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    with pytest.raises(ValueError, match="invalid run_id"):
        _safe_run_path(runs_dir, "../secret", "manifest.json")


def test_safe_run_path_confines_to_the_run_directory(tmp_path: Path) -> None:
    # Symmetric with _safe_artifact_path: the resolved path must stay inside this run, so a
    # `..` in the filename can't reach a sibling run's manifest.
    from bajutsu.mcp.resources import _safe_run_path

    runs_dir = tmp_path / "runs"
    (runs_dir / "run1").mkdir(parents=True)
    (runs_dir / "run2").mkdir(parents=True)
    with pytest.raises(ValueError, match="invalid run_id"):
        _safe_run_path(runs_dir, "run1", "../run2/manifest.json")


def test_artifact_resource_reads_screenshot(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    step_dir = runs_dir / "run1" / "00-scenario" / "step0"
    step_dir.mkdir(parents=True)
    (step_dir / "after.png").write_bytes(b"\x89PNG fake")

    from fastmcp import FastMCP

    from bajutsu.mcp.resources import register_resources

    mcp = FastMCP("test")
    register_resources(mcp, runs_dir)

    result = _run(mcp.read_resource("bajutsu://runs/run1/artifact/00-scenario/step0/after.png"))
    assert result.contents[0].content is not None


def test_artifact_resource_reads_elements_json(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    step_dir = runs_dir / "run1" / "00-scenario" / "step0"
    step_dir.mkdir(parents=True)
    elements = [{"identifier": "ok", "label": "OK"}]
    (step_dir / "elements.json").write_text(json.dumps(elements), encoding="utf-8")

    from fastmcp import FastMCP

    from bajutsu.mcp.resources import register_resources

    mcp = FastMCP("test")
    register_resources(mcp, runs_dir)

    result = _run(mcp.read_resource("bajutsu://runs/run1/artifact/00-scenario/step0/elements.json"))
    text = result.contents[0].content
    assert json.loads(text) == elements


def test_artifact_resource_reads_network_json(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    sid_dir = runs_dir / "run1" / "00-scenario"
    sid_dir.mkdir(parents=True)
    (sid_dir / "network.json").write_text("[]", encoding="utf-8")

    from fastmcp import FastMCP

    from bajutsu.mcp.resources import register_resources

    mcp = FastMCP("test")
    register_resources(mcp, runs_dir)

    result = _run(mcp.read_resource("bajutsu://runs/run1/artifact/00-scenario/network.json"))
    assert result.contents[0].content == "[]"


def test_artifact_resource_rejects_traversal(tmp_path: Path) -> None:
    from bajutsu.mcp.resources import _safe_artifact_path

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    with pytest.raises(ValueError, match="invalid artifact path"):
        _safe_artifact_path(runs_dir, "run1", "../../etc/passwd")


def test_safe_artifact_path_rejects_cross_run(tmp_path: Path) -> None:
    from bajutsu.mcp.resources import _safe_artifact_path

    runs_dir = tmp_path / "runs"
    (runs_dir / "run1").mkdir(parents=True)
    (runs_dir / "run2").mkdir(parents=True)
    (runs_dir / "run2" / "secret.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid artifact path"):
        _safe_artifact_path(runs_dir, "run1", "../run2/secret.json")


def test_safe_artifact_path_rejects_run_id_traversal(tmp_path: Path) -> None:
    # A `..` in the run_id itself must not escape runs_dir — the run_id has to name a directory
    # under runs_dir before the artifact path is even joined.
    from bajutsu.mcp.resources import _safe_artifact_path

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    with pytest.raises(ValueError, match="invalid run_id"):
        _safe_artifact_path(runs_dir, "../secret", "x.json")


def test_artifact_resource_missing_file(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    (runs_dir / "run1").mkdir(parents=True)

    from fastmcp import FastMCP

    from bajutsu.mcp.resources import register_resources

    mcp = FastMCP("test")
    register_resources(mcp, runs_dir)

    with pytest.raises(Exception, match="not found"):
        _run(mcp.read_resource("bajutsu://runs/run1/artifact/nonexistent.json"))


def test_junit_resource_reads_file(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run1"
    run_dir.mkdir(parents=True)
    (run_dir / "junit.xml").write_text("<testsuites/>", encoding="utf-8")

    from fastmcp import FastMCP

    from bajutsu.mcp.resources import register_resources

    mcp = FastMCP("test")
    register_resources(mcp, runs_dir)

    result = _run(mcp.read_resource("bajutsu://runs/run1/junit.xml"))
    assert "<testsuites/>" in result.contents[0].content


def test_latest_manifest_finds_newest(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    for name in ["20260101T000000", "20260616T120000"]:
        d = runs_dir / name
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"runId": name}), encoding="utf-8")

    from fastmcp import FastMCP

    from bajutsu.mcp.resources import register_resources

    mcp = FastMCP("test")
    register_resources(mcp, runs_dir)

    result = _run(mcp.read_resource("bajutsu://runs/latest/manifest.json"))
    data = json.loads(result.contents[0].content)
    assert data["runId"] == "20260616T120000"
