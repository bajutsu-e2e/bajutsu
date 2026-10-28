"""A value an `eval` step derives from a secret is masked in its own scenario's evidence only (BE-0472)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from _runner import _eff, _el

from bajutsu.common.config import Effective
from bajutsu.common.drivers import base
from bajutsu.common.drivers.base import BackendCrashError
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import FileSink, NullSink
from bajutsu.common.evidence.redaction import Redactor
from bajutsu.common.runner import Lease, run_all, run_and_report
from bajutsu.common.scenario import Scenario

_SECRET = "abcdefgh-ijkl-mnop"
_PREFIX = "abcdefgh"  # what scenario A derives; scenario B's screen shows the same text


def _scenarios() -> list[Scenario]:
    derive = {
        "eval": {
            "from": "${secrets.API_TOKEN}",
            "ops": [{"substring": {"start": 0, "end": 8}}],
            "into": {"var": "tokenPrefix"},
        }
    }
    copy = {"eval": {"from": "${vars.tokenPrefix}", "ops": ["trim"], "into": {"var": "again"}}}
    count = {
        "eval": {
            "from": "${secrets.API_TOKEN}",
            "ops": [{"split": "-"}, "count"],
            "into": {"var": "n"},
        }
    }
    tap = {"tap": {"id": "ok"}, "capture": ["elements"]}
    return [
        Scenario.model_validate({"name": "a", "steps": [derive, copy, count, tap]}),
        Scenario.model_validate({"name": "b", "steps": [tap]}),
    ]


def _lease(run_dir: Path) -> Callable[[Effective, Scenario], Lease]:
    def lease(eff: Effective, scenario: Scenario) -> Lease:
        # The screen echoes the derived prefix, so both scenarios' element trees carry the text.
        driver = FakeDriver([_el("ok", "OK", ["button"]), _el("echo", _PREFIX)])
        return Lease(
            driver=driver,
            sink=FileSink(run_dir, secrets=[_SECRET]),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
        )

    return lease


def _texts(root: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.json")))


def test_a_derived_secret_is_masked_in_its_scenario_and_nowhere_else(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    run_dir = runs / "run1"
    results, manifest = run_and_report(
        _eff(),
        _scenarios(),
        _lease(run_dir),
        runs,
        "run1",
        workers=2,
        bindings={"secrets.API_TOKEN": _SECRET},
        secret_values=[_SECRET],
    )
    a, b = results
    assert a.ok, a.failure
    assert b.ok, b.failure
    # The run used the real value, but even the in-memory result every later consumer reads (CI
    # annotations, a webhook) carries it masked.
    assert a.steps[0].generated == "[REDACTED]"
    assert a.derived_secrets == [_PREFIX]
    # ...the count over the secret is not registered: masking "3" everywhere would ruin the report.
    assert a.steps[2].generated == "3"

    # Scenario A's own evidence — its element tree and result.json — masks the prefix.
    a_text = _texts(run_dir / a.sid)
    assert a_text
    assert _PREFIX not in a_text
    assert '"generated": "3"' in a_text or '"generated":"3"' in a_text
    # Scenario B never derived it, so its element tree still shows the screen as it was.
    assert _PREFIX in _texts(run_dir / b.sid)

    # The manifest (one file for every scenario) masks it, and never lists the raw set.
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert _PREFIX not in json.dumps(data)
    assert all("derived_secrets" not in s for s in data["scenarios"])


def test_with_values_widens_a_copy_and_leaves_the_original_alone() -> None:
    base = Redactor(None, values=["one"])
    wider = base.with_values(["two", "", "one"])
    assert wider.redact_text("one two") != "one two"
    assert "two" not in wider.redact_text("one two")
    assert base.redact_text("two") == "two"
    # The encoded variants follow the new value too (BE-0153).
    assert "a b" not in base.with_values(["a b"]).redact_text("q=a%20b")
    assert "a%20b" not in base.with_values(["a b"]).redact_text("q=a%20b")


class _CrashingOnTap(FakeDriver):
    def tap(self, sel: base.Selector) -> None:
        raise BackendCrashError("runner crashed mid-run (test)")


def test_a_value_derived_before_a_crash_is_masked_in_the_crash_evidence(tmp_path: Path) -> None:
    # The scenario raises past `run_scenario` on every attempt, so no `RunResult` ever carries the
    # derived value back; the runner's per-scenario `DerivedSecrets` must reach the crash writers.
    def lease(eff: Effective, scenario: Scenario) -> Lease:
        return Lease(
            driver=_CrashingOnTap([_el("ok", "OK", ["button"])]),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
            crash_artifacts=lambda: [("runner.log", f"last value: {_PREFIX}".encode())],
        )

    derive, *_ = _scenarios()[0].steps
    scenario = Scenario.model_validate(
        {
            "name": "crash",
            "steps": [derive.model_dump(by_alias=True, exclude_none=True), {"tap": {"id": "ok"}}],
        }
    )
    run_dir = tmp_path / "runs" / "run1"
    (result,) = run_all(
        _eff(),
        [scenario],
        lease,
        run_dir=run_dir,
        bindings={"secrets.API_TOKEN": _SECRET},
        secret_values=[_SECRET],
        crash_retries=1,
        trace_driver=True,
    )
    assert not result.ok
    assert result.derived_secrets == [_PREFIX]
    written = [p for p in (run_dir / result.sid).rglob("*") if p.is_file()]
    assert any(p.name == "runner.log" for p in written)
    assert any(p.name == "driver_trace.json" for p in written)
    for path in written:
        assert _PREFIX not in path.read_text(encoding="utf-8", errors="replace"), path


def test_a_file_sink_masks_values_added_mid_scenario(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run1"
    sink = FileSink(run_dir, secrets=[_SECRET])
    shared = sink.redactor
    sink.add_secret_values([_PREFIX])
    sink.capture(FakeDriver([_el("echo", _PREFIX)]), "00-s/step01", ["elements"])
    tree = next(run_dir.rglob("*.json")).read_text(encoding="utf-8")
    assert _PREFIX not in tree
    # The sink and its writer agree on the widened redactor; the one built at start is untouched.
    assert sink.redactor.redact_text(_PREFIX) != _PREFIX
    assert shared.redact_text(_PREFIX) == _PREFIX


def test_a_one_character_derived_value_leaves_every_json_artifact_parseable(tmp_path: Path) -> None:
    # A derived value can be one character. Replaced inside serialized JSON it would turn
    # `"duration_s": 1.5` into `[REDACTED].5`; it masks string values only, so the files still parse.
    pick = {
        "eval": {
            "from": "${secrets.API_TOKEN}",
            "ops": [{"split": "-"}, "count"],
            "into": {"var": "n"},
        }
    }
    one = {
        "eval": {
            "from": "${secrets.API_TOKEN}",
            "ops": [{"substring": {"start": 0, "end": 1}}],
            "into": {"var": "c"},
        }
    }
    scenario = Scenario.model_validate(
        {
            "name": "short",
            "steps": [pick, one, {"tap": {"id": "ok"}, "capture": ["elements"]}],
            "expect": [{"value": {"sel": {"id": "echo"}, "equals": "${vars.c}"}}],
        }
    )
    runs = tmp_path / "runs"
    (result,), manifest = run_and_report(
        _eff(),
        [scenario],
        _lease(runs / "run1"),
        runs,
        "run1",
        bindings={"secrets.API_TOKEN": _SECRET},
        secret_values=[_SECRET],
    )
    assert result.derived_secrets == ["a"]
    for path in (runs / "run1").rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))  # every artifact still parses
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entry = data["scenarios"][0]
    assert isinstance(entry["duration_s"], float)
    # The failed `expect` quotes the derived value; the masked text is what every reader sees.
    assert "[REDACTED]" in (result.failure or "")
    assert '"a"' not in json.dumps(entry["expect_results"])
