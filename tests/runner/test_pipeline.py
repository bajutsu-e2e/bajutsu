"""Tests for run_all / run_and_report (scenarios + leases -> results + report artifacts)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _runner import _eff, _el, _failing_lease, _fake_driver, _ios_eff, _lease

from bajutsu.config import Effective, XcuitestConfig
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import NullSink
from bajutsu.evidence.network import NetworkExchange, ScreenTransition
from bajutsu.orchestrator import RunResult
from bajutsu.runner import (
    Lease,
    run_all,
    run_and_report,
    run_matrix_and_report,
)
from bajutsu.scenario import Scenario


def test_run_all() -> None:
    scenarios = [
        Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]}),
        Scenario.model_validate({"name": "b", "steps": [{"tap": {"id": "missing"}}]}),
    ]
    results = run_all(_eff(), scenarios, _lease)
    assert [r.ok for r in results] == [True, False]


def test_scenario_runner_runs_one_in_isolation() -> None:
    """`_ScenarioRunner.run_one` runs a single scenario without run_all's setup (BE-0172).

    The promotion's payoff: the per-scenario runner is unit-testable directly, its shared context
    passed as explicit fields rather than reconstructed from all of `run_all`.
    """
    from bajutsu.evidence.redaction import Redactor
    from bajutsu.runner.pipeline import _ScenarioRunner

    runner = _ScenarioRunner(
        eff=_eff(),
        lease=_lease,
        redactor=Redactor(None),
        mailbox=None,
        caps=None,
        total=2,
    )
    ok = runner.run_one(0, Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]}))
    bad = runner.run_one(
        1, Scenario.model_validate({"name": "b", "steps": [{"tap": {"id": "missing"}}]})
    )
    assert ok.ok and ok.sid == "00-a"
    assert not bad.ok and bad.sid == "01-b"


def test_preflight_fails_unsupported_scenario_before_leasing() -> None:
    # A selectOption (native <select>) needs the web-only selectOption capability, which xcuitest
    # lacks — the preflight fails the scenario up front, so the lease (device work) is never reached
    # (BE-0082).
    scenarios = [
        Scenario.model_validate(
            {"name": "z", "steps": [{"selectOption": {"sel": {"id": "m"}, "option": "x"}}]}
        )
    ]

    def lease_must_not_run(eff: Effective, s: Scenario) -> Lease:
        raise AssertionError("lease must not be called when the preflight rejects the scenario")

    results = run_all(_eff(), scenarios, lease_must_not_run, actuator="xcuitest")
    assert len(results) == 1 and not results[0].ok
    assert results[0].backend == "xcuitest"
    assert "selectOption" in (results[0].failure or "")


def test_preflight_allows_supported_scenario_on_xcuitest() -> None:
    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]
    results = run_all(_eff(), scenarios, _lease, actuator="xcuitest")
    assert results[0].ok


def test_real_device_narrowing_reaches_the_preflight_from_run_all() -> None:
    # BE-0238 Unit 3: the fixed-`actuator` call site must thread `eff` into `capabilities_for_run`,
    # so a real iOS device drops the simctl-backed capabilities. A setLocation scenario is skipped up
    # front (no lease), guarding against a refactor that reintroduces the eff-less `capabilities_for`.
    scenarios = [
        Scenario.model_validate(
            {"name": "loc", "steps": [{"setLocation": {"lat": 1.0, "lon": 2.0}}]}
        )
    ]

    def lease_must_not_run(eff: Effective, s: Scenario) -> Lease:
        raise AssertionError("lease must not be called when the preflight rejects the scenario")

    dev = _ios_eff(xcuitest=XcuitestConfig(test_runner="Runner.xctestrun", device_type="device"))
    results = run_all(dev, scenarios, lease_must_not_run, actuator="xcuitest")
    assert len(results) == 1 and not results[0].ok
    assert results[0].backend == "xcuitest"
    assert "deviceControl.setLocation" in (results[0].failure or "")


def test_simulator_still_leases_a_device_control_scenario_on_xcuitest() -> None:
    # The narrowing is real-device-only: on the Simulator the same setLocation scenario clears the
    # preflight and reaches the lease (device work) — the counterpart that proves the wiring narrows
    # nothing by default. Asserting the lease is reached (not the fake's runtime outcome) keeps the
    # test about the preflight, not FakeDriver's device-control support.
    scenarios = [
        Scenario.model_validate(
            {"name": "loc", "steps": [{"setLocation": {"lat": 1.0, "lon": 2.0}}]}
        )
    ]
    leased: list[str] = []

    def recording_lease(eff: Effective, s: Scenario) -> Lease:
        leased.append(s.name)
        return _lease(eff, s)

    sim = _ios_eff(xcuitest=XcuitestConfig(test_runner="Runner.xctestrun", device_type="simulator"))
    results = run_all(sim, scenarios, recording_lease, actuator="xcuitest")
    assert leased == ["loc"]
    assert "deviceControl.setLocation" not in (results[0].failure or "")


def test_resolve_actuator_preflights_per_scenario_and_fails_fast() -> None:
    # BE-0240: with a per-scenario resolver, the scenario's own actuator decides the capability set.
    # A selectOption resolved to xcuitest fails the preflight up front (xcuitest lacks it) — no lease.
    scenarios = [
        Scenario.model_validate(
            {"name": "z", "steps": [{"selectOption": {"sel": {"id": "m"}, "option": "x"}}]}
        )
    ]

    def lease_must_not_run(eff: Effective, s: Scenario) -> Lease:
        raise AssertionError("lease must not be called when the preflight rejects the scenario")

    results = run_all(_eff(), scenarios, lease_must_not_run, resolve_actuator=lambda s: "xcuitest")
    assert len(results) == 1 and not results[0].ok
    assert results[0].backend == "xcuitest" and "selectOption" in (results[0].failure or "")


def test_resolve_actuator_escalates_to_a_capable_actuator() -> None:
    # BE-0240: the same pinch resolved to xcuitest clears the preflight (xcuitest has multiTouch), so
    # the scenario is leased and executed rather than failed up front. (It still fails on the fake
    # driver at the pinch step — a runtime miss, not the capability rejection we're asserting is gone.)
    scenarios = [
        Scenario.model_validate(
            {"name": "z", "steps": [{"pinch": {"sel": {"id": "m"}, "scale": 2.0}}]}
        )
    ]
    leased: list[str] = []

    def lease(eff: Effective, s: Scenario) -> Lease:
        leased.append(s.name)
        return _lease(eff, s)

    results = run_all(_eff(), scenarios, lease, resolve_actuator=lambda s: "xcuitest")
    assert leased == ["z"]  # the lease was reached: no capability fail-fast
    assert "unsupported on backend" not in (results[0].failure or "")


def test_run_all_rejects_both_actuator_and_resolve_actuator() -> None:
    # BE-0240: the fixed actuator and the per-scenario resolver answer the same question; passing
    # both is a caller bug, failed loudly rather than silently letting the resolver win.
    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]
    with pytest.raises(ValueError, match="not both"):
        run_all(
            _eff(), scenarios, _lease, actuator="xcuitest", resolve_actuator=lambda s: "xcuitest"
        )


def test_resolve_actuator_no_available_actuator_fails_cleanly() -> None:
    # BE-0240: when no iOS actuator is even available the resolver raises; the pipeline turns that
    # into a clean per-scenario failure (no lease, no crash aborting the whole run).
    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]

    def resolver(s: Scenario) -> str:
        raise RuntimeError("no available actuator among ['xcuitest']")

    def lease_must_not_run(eff: Effective, s: Scenario) -> Lease:
        raise AssertionError("lease must not be called when no actuator is available")

    results = run_all(_eff(), scenarios, lease_must_not_run, resolve_actuator=resolver)
    assert len(results) == 1 and not results[0].ok
    assert results[0].backend == "" and "no available actuator" in (results[0].failure or "")


def test_run_all_parallel_preserves_order_and_releases() -> None:
    scenarios = [
        Scenario.model_validate({"name": n, "steps": [{"tap": {"id": "ok"}}]})
        for n in ("a", "b", "c")
    ]
    released: list[str] = []

    def lease(eff: Effective, s: Scenario) -> Lease:
        return Lease(
            driver=_fake_driver(),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: released.append(s.name),
        )

    results = run_all(_eff(), scenarios, lease, workers=2)
    assert [r.scenario for r in results] == ["a", "b", "c"]  # order preserved despite concurrency
    assert all(r.ok for r in results)
    assert len(released) == 3 and set(released) == {"a", "b", "c"}  # every leased device released


def test_run_all_releases_after_each_scenario() -> None:
    scenarios = [
        Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]}),
        Scenario.model_validate({"name": "b", "steps": [{"tap": {"id": "ok"}}]}),
    ]
    released: list[str] = []

    def lease(eff: Effective, s: Scenario) -> Lease:
        return Lease(
            driver=_fake_driver(),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: released.append(s.name),
        )

    run_all(_eff(), scenarios, lease)
    assert released == ["a", "b"]  # release runs after every scenario, including the last


def test_run_all_alert_guard_for_selects_per_scenario() -> None:
    # The factory picks each scenario's guard from its dismissAlerts: the guarded scenario
    # recovers from a blocked tap and passes; the one that disabled it fails.
    from bajutsu.orchestrator import AlertEvent, AlertGuardConfig

    scenarios = [
        Scenario.model_validate(
            {"name": "guarded", "dismissAlerts": True, "steps": [{"tap": {"id": "later"}}]}
        ),
        Scenario.model_validate(
            {"name": "bare", "dismissAlerts": False, "steps": [{"tap": {"id": "later"}}]}
        ),
    ]

    def recover(d: base.Driver) -> AlertEvent:
        assert isinstance(d, FakeDriver)
        d.screen = [_el("later", "Later", ["button"])]  # "dismiss the alert": target appears
        return AlertEvent(label="x")

    def alert_guard_for(s: Scenario) -> AlertGuardConfig | None:
        cfg = s.dismiss_alerts
        return None if cfg is not None and not cfg.enabled else AlertGuardConfig(vision=recover)

    results = run_all(_eff(), scenarios, _lease, alert_guard_for=alert_guard_for)
    assert [r.ok for r in results] == [True, False]


def test_run_all_attributes_each_scenario_to_its_device() -> None:
    scenarios = [
        Scenario.model_validate({"name": n, "steps": [{"tap": {"id": "ok"}}]}) for n in ("a", "b")
    ]

    def lease(eff: Effective, s: Scenario) -> Lease:
        return Lease(
            driver=_fake_driver(),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
            udid=f"DEV-{s.name}",
        )

    results = run_all(_eff(), scenarios, lease, workers=2)
    # Each result records the device that ran it, so the report can show the parallel split.
    assert {r.scenario: r.device for r in results} == {"a": "DEV-a", "b": "DEV-b"}


def test_run_and_report(tmp_path: Path) -> None:
    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]
    results, manifest = run_and_report(_eff(), scenarios, _lease, tmp_path / "runs", "run1")
    assert results[0].ok
    assert manifest.exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["runId"] == "run1"
    assert (tmp_path / "runs" / "run1" / "junit.xml").exists()
    # The executed scenario is kept alongside its results.
    scn_file = tmp_path / "runs" / "run1" / "scenario.yaml"
    assert scn_file.exists() and "name: a" in scn_file.read_text(encoding="utf-8")
    # The run is stamped with provenance (BE-0049): a fingerprint of the executed scenario YAML
    # (taken pre-redaction) plus the tool version, so accumulated runs group by identity. With no
    # secret_values here nothing is scrubbed, so the stamp also equals a hash of the saved file.
    import hashlib

    from bajutsu import __version__

    prov = data["provenance"]
    expected = "sha256:" + hashlib.sha256(scn_file.read_text(encoding="utf-8").encode()).hexdigest()
    assert prov["scenarioHash"] == expected
    assert prov["toolVersion"] == __version__
    assert "configSource" not in prov  # a local config records no Git source


# --- cross-browser matrix run (BE-0076 Phase 2): run-per-engine -> assemble -> report-once ---


def test_run_matrix_and_report_writes_one_report_with_a_matrix(tmp_path: Path) -> None:
    # Two engines, one scenario: each engine pass writes its evidence under run_dir/<engine>, and
    # the run assembles ONE manifest whose matrix aggregates the per-engine verdicts.
    scenarios = [Scenario.model_validate({"name": "login", "steps": [{"tap": {"id": "ok"}}]})]
    seen: list[tuple[str, Path]] = []

    def run_pass(engine: str, run_dir: Path) -> list[RunResult]:
        seen.append((engine, run_dir))
        # webkit fails the scenario; chromium passes it — a machine-detected incompatibility.
        return run_all(
            _eff(), scenarios, _lease if engine == "chromium" else _failing_lease, run_dir=run_dir
        )

    results, manifest = run_matrix_and_report(
        _eff(), scenarios, ["chromium", "webkit"], run_pass, tmp_path / "runs", "run1"
    )
    # Each engine pass was handed its own run_dir/<engine> subtree, in order.
    assert seen == [
        ("chromium", tmp_path / "runs" / "run1" / "chromium"),
        ("webkit", tmp_path / "runs" / "run1" / "webkit"),
    ]
    # Results are concatenated and tagged with their engine.
    assert [(r.scenario, r.engine, r.ok) for r in results] == [
        ("login", "chromium", True),
        ("login", "webkit", False),
    ]
    # ONE report at run_dir (no per-engine manifest); its matrix block aggregates both verdicts.
    assert manifest == tmp_path / "runs" / "run1" / "manifest.json"
    assert not (tmp_path / "runs" / "run1" / "chromium" / "manifest.json").exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["ok"] is False  # all-must-pass: webkit's failure fails the whole run
    matrix = data["matrix"]
    assert matrix["engines"] == ["chromium", "webkit"]
    assert matrix["cells"]["login"]["chromium"]["ok"] is True
    assert matrix["cells"]["login"]["webkit"]["ok"] is False
    # The matrix cell points at the engine-prefixed evidence dir the pass wrote under.
    assert matrix["cells"]["login"]["chromium"]["sid"] == "chromium/00-login"


def test_reroot_evidence_prefixes_paths_with_engine() -> None:
    # Each engine pass writes evidence under <engine>/<sid>/, but artifact/visual paths are recorded
    # relative to that pass's run_dir. The matrix assembles one report at the top run_dir, so the
    # paths must be re-rooted under the engine subtree or the report's links resolve wrong (BE-0076).
    from bajutsu.assertions import AssertionResult, VisualEvidence
    from bajutsu.evidence import Artifact
    from bajutsu.orchestrator import StepOutcome
    from bajutsu.runner.pipeline import _reroot_evidence

    r = RunResult(
        scenario="login",
        ok=True,
        steps=[
            StepOutcome(
                index=0,
                action="tap",
                ok=True,
                artifacts=[Artifact("00-login/after.png", "screenshot", "driver")],
            )
        ],
        artifacts=[Artifact("00-login/video.webm", "video", "collector")],
        expect_results=[
            AssertionResult(
                ok=True,
                kind="visual",
                detail="",
                visual=VisualEvidence(
                    baseline_name="home.png",
                    actual="00-login/visual-actual.png",
                    baseline="00-login/visual-baseline.png",
                    diff="00-login/visual-diff.png",
                ),
            )
        ],
    )
    _reroot_evidence(r, "webkit")
    assert r.artifacts[0].name == "webkit/00-login/video.webm"
    assert r.steps[0].artifacts[0].name == "webkit/00-login/after.png"
    v = r.expect_results[0].visual
    assert v is not None
    assert v.actual == "webkit/00-login/visual-actual.png"
    assert v.baseline == "webkit/00-login/visual-baseline.png"
    assert v.diff == "webkit/00-login/visual-diff.png"


def test_run_matrix_and_report_green_only_when_every_engine_passes(tmp_path: Path) -> None:
    scenarios = [Scenario.model_validate({"name": "login", "steps": [{"tap": {"id": "ok"}}]})]

    def run_pass(engine: str, run_dir: Path) -> list[RunResult]:
        return run_all(_eff(), scenarios, _lease, run_dir=run_dir)

    _, manifest = run_matrix_and_report(
        _eff(), scenarios, ["chromium", "firefox", "webkit"], run_pass, tmp_path / "runs", "run1"
    )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["ok"] is True  # every engine passed every scenario


def test_run_and_report_records_git_config_source(tmp_path: Path) -> None:
    # A run from a Git config source stamps which repo@sha it executed into the manifest (BE-0063).
    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]
    src = {"host": "github.com", "owner": "acme", "repo": "tests", "ref": "main", "sha": "deadbeef"}
    _, manifest = run_and_report(
        _eff(), scenarios, _lease, tmp_path / "runs", "run1", config_source=src
    )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["provenance"]["configSource"] == src


def test_run_and_report_records_upload_exec_decision(tmp_path: Path) -> None:
    # BE-0090: an upload-governed run stamps the launchServer policy decision into the manifest.
    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]
    decision = {
        "decision": "sandboxed",
        "field": "launchServer",
        "source": "dockerImage",
        "image": "img",
    }
    _, manifest = run_and_report(
        _eff(), scenarios, _lease, tmp_path / "runs", "run1", exec_provenance=decision
    )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["provenance"]["uploadExec"] == decision


def test_run_and_report_omits_upload_exec_for_ungoverned_run(tmp_path: Path) -> None:
    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]
    _, manifest = run_and_report(_eff(), scenarios, _lease, tmp_path / "runs", "run1")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert "uploadExec" not in data["provenance"]  # None decision → no key


def test_git_revision_maps_failure_and_blank_to_none(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # The subprocess is an external dependency, so it's the one place a stub is warranted. A
    # non-zero exit, a thrown error, and a 0-exit-but-blank stdout (a shimmed `git`) all mean
    # "unknown revision" — None, never an empty stamp. The helper lives with run_provenance
    # (report.manifest) so the pool's wait-timeout diagnostic and the report share it (BE-0231).
    import subprocess as sp

    from bajutsu.report import manifest

    def fake(result: sp.CompletedProcess[str] | Exception):  # type: ignore[no-untyped-def]
        def run(*a: object, **k: object) -> sp.CompletedProcess[str]:
            if isinstance(result, Exception):
                raise result
            return result

        return run

    monkeypatch.setattr(manifest.subprocess, "run", fake(sp.CompletedProcess([], 128, "", "fatal")))
    assert manifest.git_revision() is None  # not a repo
    monkeypatch.setattr(manifest.subprocess, "run", fake(sp.CompletedProcess([], 0, "   \n", "")))
    assert manifest.git_revision() is None  # 0 exit but blank stdout → unknown, not ""
    monkeypatch.setattr(manifest.subprocess, "run", fake(FileNotFoundError("git")))
    assert manifest.git_revision() is None  # git absent
    monkeypatch.setattr(
        manifest.subprocess, "run", fake(sp.CompletedProcess([], 0, "abc123\n", ""))
    )
    assert manifest.git_revision() == "abc123"  # normal: trimmed sha


def test_run_and_report_forwards_baselines_dir(tmp_path: Path) -> None:
    # A visual expect with the baselines dir forwarded builds a VisualContext, so a missing
    # baseline reports "baseline not found" — not "no visual context" (the unforwarded bug).
    scenarios = [
        Scenario.model_validate(
            {
                "name": "vis",
                "steps": [{"tap": {"id": "ok"}}],
                "expect": [{"visual": {"baseline": "home.png"}}],
            }
        )
    ]
    results, _ = run_and_report(
        _eff(),
        scenarios,
        _lease,
        tmp_path / "runs",
        "run1",
        baselines_dir=tmp_path / "baselines",
    )
    ev = results[0].expect_results[0]
    assert ev.kind == "visual"
    assert "baseline not found" in ev.reason
    assert ev.visual is not None and ev.visual.missing


def test_run_and_report_forwards_schemas_dir(tmp_path: Path) -> None:
    # A responseSchema expect with schemas_dir forwarded builds a SchemaContext, so the failure
    # gets past the "no schema context" guard (here it fails later, on no matching exchange) —
    # proving the dir was threaded, not dropped.
    scenarios = [
        Scenario.model_validate(
            {
                "name": "rs",
                "steps": [{"tap": {"id": "ok"}}],
                "expect": [
                    {"responseSchema": {"request": {"path": "/api/items"}, "schema": "items.json"}}
                ],
            }
        )
    ]
    results, _ = run_and_report(
        _eff(), scenarios, _lease, tmp_path / "runs", "run1", schemas_dir=tmp_path / "schemas"
    )
    ev = results[0].expect_results[0]
    assert ev.kind == "responseSchema"
    assert "no schema context" not in ev.reason  # context was forwarded


def test_run_and_report_scrubs_secret_values_from_artifacts(tmp_path: Path) -> None:
    """The run-level scrub is the final safety net: a secret that reaches result text (here a
    failing assertion's expected value, interpolated from a binding) must not survive into any
    written artifact, even though the scenario definition only ever holds the token."""
    secret = "S3CR3T-TOKEN"
    scenarios = [
        Scenario.model_validate(
            {
                "name": "a",
                "steps": [{"tap": {"id": "ok"}}],
                "expect": [{"value": {"sel": {"id": "ok"}, "equals": "${secrets.token}"}}],
            }
        )
    ]
    results, _ = run_and_report(
        _eff(),
        scenarios,
        _lease,
        tmp_path / "runs",
        "run1",
        bindings={"secrets.token": secret},
        secret_values=[secret],
    )
    # The assertion failed, so the secret value really did reach the in-memory result text.
    assert not results[0].ok
    assert results[0].failure is not None and secret in results[0].failure
    # ...but it is scrubbed out of every written artifact.
    run_dir = tmp_path / "runs" / "run1"
    for name in ("manifest.json", "junit.xml", "scenario.yaml"):
        assert secret not in (run_dir / name).read_text(encoding="utf-8")


def test_run_and_report_masks_literal_totp_seed_in_artifacts(tmp_path: Path) -> None:
    # A literal base32 TOTP seed written into a scenario is durable credential material, not a
    # one-time code — it must never survive into the run's evidence bundle (BE-0152).
    seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    scenarios = [
        Scenario.model_validate(
            {"name": "a", "steps": [{"totp": {"secret": seed, "into": {"var": "code"}}}]}
        )
    ]
    run_and_report(_eff(), scenarios, _lease, tmp_path / "runs", "run1")
    run_dir = tmp_path / "runs" / "run1"
    for name in ("scenario.yaml", "manifest.json", "report.html"):
        assert seed not in (run_dir / name).read_text(encoding="utf-8")
    assert "<redacted>" in (run_dir / "scenario.yaml").read_text(encoding="utf-8")


def test_run_and_report_keeps_totp_reference_and_scrubs_the_resolved_seed(tmp_path: Path) -> None:
    # A `${secrets.*}` reference stays in the snapshot (reviewable, and not itself the seed), while
    # its resolved value never reaches any artifact — confirming BE-0032 already covers the
    # resolved case that BE-0152's snapshot masking complements.
    seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    scenarios = [
        Scenario.model_validate(
            {
                "name": "a",
                "steps": [{"totp": {"secret": "${secrets.SEED}", "into": {"var": "code"}}}],
            }
        )
    ]
    run_and_report(
        _eff(),
        scenarios,
        _lease,
        tmp_path / "runs",
        "run1",
        bindings={"secrets.SEED": seed},
        secret_values=[seed],
    )
    run_dir = tmp_path / "runs" / "run1"
    assert "${secrets.SEED}" in (run_dir / "scenario.yaml").read_text(encoding="utf-8")
    for name in ("scenario.yaml", "manifest.json", "report.html"):
        assert seed not in (run_dir / name).read_text(encoding="utf-8")


def test_run_and_report_writes_owner_only_artifacts(tmp_path: Path) -> None:
    # BE-0131: a fresh run's directory and its sensitive files (scenario.yaml, network.json) must
    # land owner-only (0700 dir, 0600 files), not world-readable under the ambient umask — evidence
    # can carry secrets, and a shared CI runner is exactly where another local account can read it.
    import stat

    from bajutsu.evidence import FileSink

    run_dir = tmp_path / "runs" / "run1"
    ex = NetworkExchange(method="GET", path="/items", status=200)
    scn = Scenario.model_validate(
        {"name": "net", "steps": [{"assert": [{"request": {"method": "GET", "path": "/items"}}]}]}
    )

    def lease(eff: Effective, s: Scenario) -> Lease:
        return Lease(
            driver=FakeDriver([_el("ok", "OK")]),
            sink=FileSink(run_dir),
            relaunch=None,
            control=None,
            collector=_ConstantCollector([ex]),
            release=lambda: None,
        )

    run_and_report(_eff(), [scn], lease, tmp_path / "runs", "run1")

    def mode(p: Path) -> int:
        return stat.S_IMODE(p.stat().st_mode)

    assert mode(run_dir) == 0o700
    assert mode(run_dir / "scenario.yaml") == 0o600
    net = run_dir / "00-net" / "network.json"
    assert net.exists() and mode(net) == 0o600


def test_write_network_stamps_the_given_provider(tmp_path: Path) -> None:
    from bajutsu.evidence.redaction import Redactor
    from bajutsu.runner.pipeline import _write_network

    ex = NetworkExchange(method="GET", path="/a", status=200)
    art = _write_network(
        [(ex, 1.0)], 0.0, tmp_path, "00-s", Redactor(None), provider="fake (fallback)"
    )
    assert art is not None and art.provider == "fake (fallback)"


class _ConstantCollector:
    """A Collector that always reports the same exchanges (clear is a no-op) — test scaffolding so
    provenance/threading can be checked without live traffic during a fake run (BE-0020)."""

    def __init__(self, exchanges: list[NetworkExchange]) -> None:
        self._ex = list(exchanges)

    def snapshot(self) -> list[NetworkExchange]:
        return list(self._ex)

    def snapshot_timed(self) -> list[tuple[NetworkExchange, float]]:
        return [(e, 0.0) for e in self._ex]

    def transitions_snapshot_timed(self) -> list[tuple[ScreenTransition, float]]:
        return []

    def clear(self) -> None:
        pass

    def stop(self) -> None:
        pass


def test_run_all_threads_collector_provider_and_discloses_skips(tmp_path: Path) -> None:
    from bajutsu.evidence import FileSink
    from bajutsu.orchestrator import SkippedCapture

    ex = NetworkExchange(method="GET", path="/items", status=200)
    scn = Scenario.model_validate(
        {"name": "net", "steps": [{"assert": [{"request": {"method": "GET", "path": "/items"}}]}]}
    )

    def lease(eff: Effective, s: Scenario) -> Lease:
        return Lease(
            driver=FakeDriver([_el("ok", "OK")]),
            sink=FileSink(tmp_path),
            relaunch=None,
            control=None,
            collector=_ConstantCollector([ex]),
            release=lambda: None,
            collector_provider="fake (fallback)",
            skipped_captures=[SkippedCapture("video", "no provider")],
        )

    r = run_all(_eff(), [scn], lease, run_dir=tmp_path)[0]
    assert r.ok
    assert [s.kind for s in r.skipped_captures] == ["video"]
    net = [a for a in r.artifacts if a.kind == "network"]
    assert net and net[0].provider == "fake (fallback)"
    assert (tmp_path / net[0].name).exists()


def test_pipeline_uses_the_single_orchestrator_no_op_network_source() -> None:
    # The runner shares the orchestrator's one no-op NetworkSource rather than owning a copy, so
    # the default "no network was collected" value lives in one place (BE-0251).
    from bajutsu.orchestrator.types import _no_network
    from bajutsu.runner import pipeline, types

    assert pipeline._no_network is _no_network
    assert _no_network() == []
    assert not hasattr(types, "_no_net")
