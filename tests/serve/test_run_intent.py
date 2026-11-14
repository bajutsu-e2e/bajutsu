"""A `run-set` job carries a run intent, which the worker that leases it turns into its own form
(BE-0484 unit 2)."""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

import pytest

from bajutsu.serve.capabilities import RUN_INTENT_CAPABILITY, can_serve
from bajutsu.serve.commands import run_command
from bajutsu.serve.server.worker_job import job_spec, resolve_run_intent
from bajutsu.serve.state import Job
from bajutsu.serve.state.job import RunIntent

_INTENT = RunIntent(
    scenario="scenarios/one.yaml",
    target="demo",
    config="bajutsu.config.yaml",
    platform="android",
    app_path="app.apk",
)


def _spec(**fields: object) -> dict[str, object]:
    return job_spec(Job(intent=_INTENT, label="nightly", **fields))  # type: ignore[arg-type]


def test_the_spec_carries_the_intent_and_no_batch_or_command() -> None:
    spec = _spec()
    assert spec["intent"] == {
        "scenario": "scenarios/one.yaml",
        "target": "demo",
        "config": "bajutsu.config.yaml",
        "platform": "android",
        "app_path": "app.apk",
        "upload_exec": "",
    }
    assert spec["batch"] is None and spec["cmd"] == []


def test_a_device_cloud_worker_submits_the_intent_as_its_own_providers_batch() -> None:
    resolved = resolve_run_intent(_spec(materialize_baselines=True), "devicefarm")
    assert resolved["batch"] == {
        "provider": "devicefarm",
        "scenario": "scenarios/one.yaml",
        "target": "demo",
        "config": "bajutsu.config.yaml",
        "platform": "android",
        "app_path": "app.apk",
    }
    assert resolved["materialize_baselines"] is False


@pytest.mark.parametrize("environment", ["local", "appium"])
def test_any_other_worker_runs_the_intent_as_the_command_a_plain_run_carries(
    environment: str,
) -> None:
    intent = replace(_INTENT, upload_exec="deny")
    resolved = resolve_run_intent(
        job_spec(Job(intent=intent, label="nightly", materialize_baselines=True)), environment
    )
    assert resolved["cmd"] == run_command(
        "scenarios/one.yaml",
        "demo",
        config="bajutsu.config.yaml",
        baselines="baselines",
        upload_exec="deny",
        label="nightly",
    )
    assert resolved["batch"] is None
    # A cancel lets the run write its manifest, as for a plain run (BE-0370).
    assert resolved["graceful_cancel"] is True


def test_an_intent_job_is_a_run_set_job() -> None:
    assert Job(intent=_INTENT).kind == "run-set"


def test_a_run_without_stored_baselines_names_none() -> None:
    resolved = resolve_run_intent(_spec(materialize_baselines=False), "local")
    assert "--baselines" not in resolved["cmd"]


def test_a_spec_without_an_intent_is_left_as_it_is() -> None:
    spec = job_spec(Job(cmd=["x"]))
    assert resolve_run_intent(spec, "devicefarm") is spec


def test_a_worker_that_does_not_advertise_the_intent_token_cannot_serve_the_job() -> None:
    required = ["environment:devicefarm", RUN_INTENT_CAPABILITY]
    assert not can_serve(required, ["environment:devicefarm", "host:linux"])
    assert can_serve(required, ["environment:devicefarm", "host:linux", RUN_INTENT_CAPABILITY])


def test_a_local_intent_job_places_its_application_in_a_workspace_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.serve.cli import worker as worker_mod

    placed: list[Path] = []
    monkeypatch.setattr(worker_mod, "_fetch_part", lambda _url, dest, _sha: placed.append(dest))
    spec = resolve_run_intent({**_spec(batch_app="a" * 64), "job_id": "j1"}, "local")

    tree = worker_mod._bundle_workspace(tmp_path, spec, None)
    worker_mod._place_batch_app(tree, spec, "https://signed/app")

    assert tree == tmp_path / ".batch" / "j1"
    assert placed == [tree / "app.apk"]


@pytest.mark.parametrize("environment", ["local", "appium", "devicefarm"])
@pytest.mark.parametrize(
    "intent",
    [
        {"scenario": "a.yaml"},
        {**asdict(_INTENT), "app_path": None},
        {**asdict(_INTENT), "scenario": ""},
    ],
)
def test_a_malformed_intent_is_refused_by_name_before_anything_runs(
    environment: str, intent: dict[str, object]
) -> None:
    # The worker resolves nothing from it, so the environment check is what stops the job.
    from bajutsu.serve.server.one_shot import environment_refusal

    spec = resolve_run_intent({**_spec(), "intent": intent}, environment)
    reason = environment_refusal(spec, environment)
    assert reason is not None and "`intent`" in reason


@pytest.mark.parametrize("environment", ["local", "devicefarm"])
def test_the_resolved_job_records_the_environment_it_runs_in(environment: str) -> None:
    resolved = resolve_run_intent(_spec(provenance={"bundle": "b"}), environment)
    assert resolved["provenance"] == {"bundle": "b", "workerEnvironment": environment}


def test_a_web_intent_runs_with_no_application() -> None:
    # A browser installs nothing, so a web target names no `subject.path` (BE-0484).
    web = replace(_INTENT, platform="web", app_path="")
    resolved = resolve_run_intent(job_spec(Job(intent=web)), "local")
    assert resolved["cmd"] == run_command(
        "scenarios/one.yaml", "demo", config="bajutsu.config.yaml"
    )


def test_a_device_intent_without_an_application_is_still_refused() -> None:
    from bajutsu.serve.server.worker_job import intent_problem

    spec = job_spec(Job(intent=replace(_INTENT, app_path="")))
    assert intent_problem(spec) == "the spec's `intent` must name app_path as non-empty strings"


@pytest.mark.parametrize(("environment", "form"), [("local", "cmd"), ("devicefarm", "batch")])
def test_worker_once_runs_the_intent_in_its_own_environments_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: str, form: str
) -> None:
    import json

    from bajutsu.serve.cli import worker as worker_mod
    from bajutsu.serve.server import one_shot

    ran: list[dict[str, object]] = []

    def run_spec_once(spec: dict[str, object], **_kw: object) -> int:
        ran.append(spec)
        return 0

    monkeypatch.setattr(one_shot, "run_spec_once", run_spec_once)
    monkeypatch.chdir(tmp_path)
    spec = _spec(materialize_baselines=False)
    spec["materials"] = {}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")

    assert worker_mod._run_once(path, None, environment) == 0

    (resolved,) = ran
    assert resolved[form]


@pytest.mark.parametrize(
    ("environment", "form", "checkpointed"),
    [("devicefarm", "batch", True), ("local", "cmd", False)],
)
def test_a_leased_intent_job_runs_in_its_workers_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: str, form: str, checkpointed: bool
) -> None:
    # Only a batch stores its run's ARN, so a re-lease resumes it (BE-0448).
    from bajutsu.serve.cli import worker as worker_mod
    from bajutsu.serve.logbus import InMemoryLogBus

    ran: dict[str, object] = {}

    def execute(spec: dict[str, object], **kw: object) -> Job:
        ran.update(spec=spec, checkpoint=kw.get("batch_checkpoint"))
        return Job()

    monkeypatch.setattr(worker_mod, "_post_json", lambda *_a, **_k: (200, {}))
    monkeypatch.setattr(worker_mod, "_workspace_or_failure", lambda work, *_a, **_k: (work, None))
    monkeypatch.setattr(worker_mod, "execute_job_spec", execute)

    worker_mod._run_with_heartbeat(
        {**_spec(), "job_id": "j1"},
        job_id="j1",
        work=tmp_path,
        bundle_urls=None,
        bus=InMemoryLogBus(),
        url="http://cp",
        wid="w1",
        auth_token=None,
        heartbeat_interval=0.02,
        environment=environment,
    )

    spec = ran["spec"]
    assert isinstance(spec, dict) and spec[form]
    assert (ran["checkpoint"] is not None) is checkpointed
