"""`<sid>/result.json`: each scenario's verdict, persisted the moment that scenario finishes."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from _runner import _eff, _el, _failing_lease, _lease

from bajutsu.common.config import Effective
from bajutsu.common.drivers import base
from bajutsu.common.drivers.base import BackendCrashError
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import NullSink
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.report import manifest_dict
from bajutsu.common.report.manifest import SCHEMA_VERSION
from bajutsu.common.runner import Lease, run_all
from bajutsu.common.scenario import Redact, Scenario


def _scenario(name: str) -> Scenario:
    return Scenario.model_validate({"name": name, "steps": [{"tap": {"id": "ok"}}]})


def _read(run_dir: Path, sid: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((run_dir / sid / "result.json").read_text(encoding="utf-8"))
    return data


def test_each_scenario_writes_its_own_result_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run1"
    results = run_all(_eff(), [_scenario("a"), _scenario("b")], _lease, run_dir=run_dir)

    for r in results:
        doc = _read(run_dir, r.sid)
        assert doc["schemaVersion"] == SCHEMA_VERSION
        assert doc["scenario"]["scenario"] == r.scenario
        assert doc["scenario"]["ok"] is True


def test_result_json_matches_the_manifest_entry(tmp_path: Path) -> None:
    # The same shape `manifest.json` carries under `scenarios`, so a reader of a crashed run's
    # partial records needs no second parser.
    run_dir = tmp_path / "runs" / "run1"
    results = run_all(_eff(), [_scenario("a")], _failing_lease, run_dir=run_dir)

    doc = _read(run_dir, results[0].sid)
    assert doc["scenario"]["ok"] is False
    assert doc["scenario"]["failure"]
    manifest_entry = json.loads(json.dumps(manifest_dict("run1", results)["scenarios"]))[0]
    assert doc["scenario"] == manifest_entry


def test_config_redact_fields_do_not_mask_the_entrys_own_keys(tmp_path: Path) -> None:
    # `redact.fields` masks matching JSON keys in app evidence; `manifest.json` is written without
    # it, so `result.json` must be too, or a field named like an entry key (`reason`) would be
    # masked in one record and not the other.
    eff = replace(_eff(), redact=Redact(fields=["reason"]))
    run_dir = tmp_path / "runs" / "run1"
    results = run_all(eff, [_scenario("a")], _failing_lease, run_dir=run_dir)

    doc = _read(run_dir, results[0].sid)
    assert "reason" in doc["scenario"]["steps"][0]
    manifest_entry = json.loads(json.dumps(manifest_dict("run1", results)["scenarios"]))[0]
    assert doc["scenario"] == manifest_entry


class _Crashing(FakeDriver):
    def tap(self, sel: base.Selector) -> None:
        raise BackendCrashError("runner crashed mid-run (test)")


def test_crash_recovered_scenario_records_the_final_attempt(tmp_path: Path) -> None:
    calls = {"n": 0}

    def lease(eff: Effective, scenario: Scenario) -> Lease:
        calls["n"] += 1
        driver = (_Crashing if calls["n"] == 1 else FakeDriver)([_el("ok", "OK", ["button"])])
        return Lease(
            driver=driver,
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
        )

    run_dir = tmp_path / "runs" / "run1"
    results = run_all(_eff(), [_scenario("a")], lease, run_dir=run_dir, crash_retries=1)

    assert calls["n"] == 2
    assert results[0].ok, results[0].failure
    assert _read(run_dir, results[0].sid)["scenario"]["ok"] is True


def test_result_json_is_on_disk_before_the_next_scenario_starts(tmp_path: Path) -> None:
    # The point of the file: a run killed during scenario N still leaves scenarios 1..N-1's verdicts.
    run_dir = tmp_path / "runs" / "run1"
    seen_before_lease: list[list[str]] = []

    def lease(eff: Effective, scenario: Scenario) -> Lease:
        seen_before_lease.append(sorted(p.parent.name for p in run_dir.glob("*/result.json")))
        return _lease(eff, scenario)

    results = run_all(_eff(), [_scenario("a"), _scenario("b")], lease, run_dir=run_dir)

    assert seen_before_lease == [[], [results[0].sid]]


def test_parallel_workers_each_write_their_own_result_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run1"
    scenarios = [_scenario(name) for name in ("a", "b", "c")]
    results = run_all(_eff(), scenarios, _lease, run_dir=run_dir, workers=2)

    assert [_read(run_dir, r.sid)["scenario"]["scenario"] for r in results] == ["a", "b", "c"]


def test_trace_driver_run_still_writes_result_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run1"
    results = run_all(_eff(), [_scenario("a")], _lease, run_dir=run_dir, trace_driver=True)

    assert _read(run_dir, results[0].sid)["scenario"]["ok"] is True
    assert (run_dir / results[0].sid / "driver_trace.json").is_file()


def test_trace_driver_run_writes_result_json_before_the_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The verdict must not wait on the diagnostic trace's serialization: a process killed while
    # writing `driver_trace.json` would otherwise lose a scenario that had already finished.
    written: list[str] = []
    original = RunArtifactWriter.write_json

    def _record(self: RunArtifactWriter, name: str, data: object) -> Path:
        written.append(Path(name).name)
        return original(self, name, data)

    monkeypatch.setattr(RunArtifactWriter, "write_json", _record)
    run_all(_eff(), [_scenario("a")], _lease, run_dir=tmp_path / "run1", trace_driver=True)

    assert written.index("result.json") < written.index("driver_trace.json")


def test_no_run_dir_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    results = run_all(_eff(), [_scenario("a")], _lease)

    assert results[0].ok, results[0].failure
    assert not list(tmp_path.rglob("result.json"))


def test_write_failure_is_warned_about_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A partial-progress record, not the verdict: `manifest.json` still carries the result, so a
    # failed write must not end the run.
    def _boom(self: RunArtifactWriter, name: str, data: object) -> Path:
        raise OSError("disk full (test)")

    monkeypatch.setattr(RunArtifactWriter, "write_json", _boom)
    run_dir = tmp_path / "runs" / "run1"
    results = run_all(_eff(), [_scenario("a")], _lease, run_dir=run_dir)

    assert results[0].ok, results[0].failure
    assert not (run_dir / results[0].sid / "result.json").exists()
    assert "writing result.json failed" in caplog.text
