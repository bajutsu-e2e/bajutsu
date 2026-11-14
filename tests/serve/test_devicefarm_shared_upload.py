"""The Device Farm provider shares uploads between the jobs of a run-set group (BE-0484 unit 4).

Driven against an in-memory fake client, so no real AWS. Jobs share an upload only while they are in
flight together, so most tests hold every run open until all of them are scheduled.
"""

from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

from bajutsu.common.cloud.devicefarm import DeviceFarmError
from bajutsu.serve import batch_provider as bp
from bajutsu.serve.batch_provider._shared_uploads import SharedUploads


def _zip(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buffer.getvalue()


class _RejectedUpload(Exception):
    """Shaped like botocore's ClientError, which carries the service's error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _Client:
    """Holds every run RUNNING until *runs* schedules were attempted, so their jobs overlap."""

    def __init__(self, *, runs: int = 1, fail_type: str | None = None) -> None:
        self._runs = runs
        self._fail_type = fail_type
        self._lock = threading.Lock()
        self.uploads: list[tuple[str, str]] = []
        self.scheduled: list[dict[str, Any]] = []
        self.attempts = 0
        self.reject_reused: str | None = None
        self.reject_all: str | None = None

    def create_upload(self, *, projectArn: str, name: str, type: str) -> dict[str, Any]:  # noqa: N803 - boto3 kwargs
        with self._lock:
            self.uploads.append((type, name))
            arn = f"arn:upload/{len(self.uploads)}"
        return {"upload": {"arn": arn, "url": f"https://s3/{arn}"}}

    def get_upload(self, *, arn: str) -> dict[str, Any]:
        failed = self._fail_type is not None and any(
            t == self._fail_type and arn == f"arn:upload/{i}"
            for i, (t, _) in enumerate(self.uploads, 1)
        )
        return {"upload": {"arn": arn, "status": "FAILED" if failed else "SUCCEEDED"}}

    def schedule_run(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self.attempts += 1
            refuse = self.reject_all
        if refuse:
            # Held until a second job attempts too, so the two jobs' uploads overlap.
            give_up = time.monotonic() + 30
            while self.attempts < 2 and time.monotonic() < give_up:
                time.sleep(0.01)
            raise _RejectedUpload(refuse)
        with self._lock:
            used = {kwargs["appArn"], kwargs["test"]["testPackageArn"]}
            if self.reject_reused and any(
                used & {s["appArn"], s["test"]["testPackageArn"]} for s in self.scheduled
            ):
                raise _RejectedUpload(self.reject_reused)
            self.scheduled.append(kwargs)
            return {"run": {"arn": f"arn:run/{len(self.scheduled)}"}}

    def get_run(self, *, arn: str) -> dict[str, Any]:
        done = self.attempts >= self._runs
        return {"run": {"arn": arn, "status": "COMPLETED" if done else "RUNNING"}}

    def list_artifacts(self, *, arn: str, type: str) -> dict[str, Any]:
        return {"artifacts": [{"name": "runs", "extension": "zip", "url": "https://s3/runs.zip"}]}

    def stop_run(self, *, arn: str) -> dict[str, Any]:
        return {"run": {"arn": arn}}

    def count(self, kind: str) -> int:
        return sum(1 for t, _ in self.uploads if t == kind)


class _Transfer:
    def __init__(self) -> None:
        self.packages: list[set[str]] = []

    def upload(self, url: str, path: Path) -> None:
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as zf:
                self.packages.append(set(zf.namelist()))

    def download(self, url: str) -> bytes:
        manifest = json.dumps({"ok": True, "scenarios": [{"scenario": "s", "ok": True}]})
        return _zip({"runs/20260101-1/manifest.json": manifest})


def _work(tmp_path: Path, *scenarios: str) -> Path:
    work = tmp_path / "-".join(scenarios)
    work.mkdir()
    (work / "app.apk").write_bytes(b"apk")
    for name in scenarios:
        (work / name).write_text("- name: s\n  steps: []\n", encoding="utf-8")
    return work


def _request(scenario: str) -> bp.BatchRequest:
    return bp.BatchRequest(
        provider="devicefarm",
        scenario=scenario,
        target="demo",
        config="bajutsu.config.yaml",
        platform="android",
        app_path="app.apk",
    )


def _provider(client: _Client, transfer: _Transfer, **kwargs: Any) -> bp.DeviceFarmBatchProvider:
    return bp.DeviceFarmBatchProvider(
        client=client,
        transfer=transfer,
        project_arn="arn:project/1",
        sleep=lambda _: None,
        **kwargs,
    )


def _together(
    provider: bp.DeviceFarmBatchProvider,
    tmp_path: Path,
    jobs: list[tuple[Path, str, bp.BatchShare | None]],
) -> list[BaseException | None]:
    """Submit *jobs* on threads of their own, as a slot worker does; each one's error, or None."""
    errors: list[BaseException | None] = [None] * len(jobs)

    def run(i: int, work: Path, scenario: str, share: bp.BatchShare | None) -> None:
        try:
            provider.submit(
                _request(scenario),
                work_dir=work,
                dest=tmp_path / f"d{i}",
                job_id=f"j{i}",
                share=share,
            )
        except BaseException as exc:
            errors[i] = exc

    threads = [threading.Thread(target=run, args=(i, *job)) for i, job in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a submit never returned"
    return errors


_SHARE = bp.BatchShare(group_id="g1", compat_key="c1")


def _source_tree(root: Path) -> Path:
    """The parts of a source checkout a package takes, so the scenarios sit under `bundle/`."""
    (root / "tests").mkdir(parents=True)
    (root / "bajutsu").mkdir()
    for name in ("pyproject.toml", "DESIGN.md", "LICENSE", "NOTICE"):
        (root / name).write_text("", encoding="utf-8")
    return root


@pytest.mark.parametrize("from_source", [False, True])
def test_jobs_of_one_group_in_flight_together_upload_the_app_and_package_once(
    tmp_path: Path, from_source: bool
) -> None:
    client = _Client(runs=3)
    work = _work(tmp_path, "a.yaml", "b.yaml", "c.yaml")
    source = {"source_root": _source_tree(tmp_path / "src")} if from_source else {}

    errors = _together(
        _provider(client, _Transfer(), **source),
        tmp_path,
        [(work, s, _SHARE) for s in ("a.yaml", "b.yaml", "c.yaml")],
    )

    assert errors == [None, None, None]
    assert client.count("ANDROID_APP") == 1
    assert client.count("APPIUM_PYTHON_TEST_PACKAGE") == 1
    assert client.count("APPIUM_PYTHON_TEST_SPEC") == 3
    assert len({s["test"]["testSpecArn"] for s in client.scheduled}) == 3


def test_jobs_without_a_share_upload_everything_per_job(tmp_path: Path) -> None:
    client = _Client(runs=2)
    work = _work(tmp_path, "a.yaml", "b.yaml")

    _together(
        _provider(client, _Transfer()), tmp_path, [(work, "a.yaml", None), (work, "b.yaml", None)]
    )

    assert client.count("ANDROID_APP") == 2
    assert client.count("APPIUM_PYTHON_TEST_PACKAGE") == 2


def test_another_group_with_the_same_compatibility_key_shares_the_app_but_not_the_package(
    tmp_path: Path,
) -> None:
    client = _Client(runs=2)
    work = _work(tmp_path, "a.yaml")

    _together(
        _provider(client, _Transfer()),
        tmp_path,
        [(work, "a.yaml", _SHARE), (work, "a.yaml", bp.BatchShare("g2", "c1"))],
    )

    assert client.count("ANDROID_APP") == 1
    assert client.count("APPIUM_PYTHON_TEST_PACKAGE") == 2


def test_a_package_that_lacks_the_jobs_scenario_is_not_reused(tmp_path: Path) -> None:
    client = _Client(runs=2)
    transfer = _Transfer()
    provider = _provider(client, transfer)
    only_a = _work(tmp_path, "a.yaml")
    both = _work(tmp_path, "a.yaml", "b.yaml")
    first = threading.Thread(
        target=provider.submit,
        args=(_request("a.yaml"),),
        kwargs={"work_dir": only_a, "dest": tmp_path / "d0", "share": _SHARE},
    )
    first.start()
    give_up = time.monotonic() + 30
    while not client.scheduled:
        assert first.is_alive() and time.monotonic() < give_up, "the first job never scheduled"
        time.sleep(0.01)

    errors = _together(provider, tmp_path, [(both, "b.yaml", _SHARE)])
    first.join(timeout=30)

    assert errors == [None]
    assert client.count("ANDROID_APP") == 1
    assert client.count("APPIUM_PYTHON_TEST_PACKAGE") == 2
    assert "b.yaml" in transfer.packages[-1]


def test_a_hook_that_sets_a_launch_env_keeps_the_package_per_job(tmp_path: Path) -> None:
    class _Env(bp.BatchLifecycleHook):
        def before_submit(self, ctx: bp.BatchContext) -> None:
            ctx.launch_env["PROXY"] = ctx.job_id

    client = _Client(runs=2)
    work = _work(tmp_path, "a.yaml", "b.yaml")
    (work / "bajutsu.config.yaml").write_text("targets: {demo: {}}\n", encoding="utf-8")

    errors = _together(
        _provider(client, _Transfer(), hooks=[_Env()]),
        tmp_path,
        [(work, "a.yaml", _SHARE), (work, "b.yaml", _SHARE)],
    )

    assert errors == [None, None]
    assert client.count("ANDROID_APP") == 1
    assert client.count("APPIUM_PYTHON_TEST_PACKAGE") == 2


def test_a_shared_upload_is_dropped_once_no_job_holds_its_key(tmp_path: Path) -> None:
    client = _Client()
    provider = _provider(client, _Transfer())
    work = _work(tmp_path, "a.yaml")

    provider.submit(_request("a.yaml"), work_dir=work, dest=tmp_path / "d0", share=_SHARE)
    provider.submit(_request("a.yaml"), work_dir=work, dest=tmp_path / "d1", share=_SHARE)

    assert client.count("ANDROID_APP") == 2


def test_a_failed_shared_upload_fails_each_job_that_waited_on_it(tmp_path: Path) -> None:
    client = _Client(runs=2, fail_type="ANDROID_APP")
    work = _work(tmp_path, "a.yaml", "b.yaml")

    errors = _together(
        _provider(client, _Transfer()),
        tmp_path,
        [(work, "a.yaml", _SHARE), (work, "b.yaml", _SHARE)],
    )

    assert all(isinstance(e, DeviceFarmError) for e in errors)
    assert client.scheduled == []


def test_a_holder_that_waited_on_a_failed_upload_fails_and_a_later_one_uploads_afresh() -> None:
    uploads = SharedUploads()
    made: list[str] = []

    def fail() -> tuple[str, frozenset[str]]:
        made.append("fail")
        raise OSError("reset")

    def succeed() -> tuple[str, frozenset[str]]:
        made.append("ok")
        return "arn:1", frozenset()

    with uploads.hold("k") as first, uploads.hold("k") as waiting:
        with pytest.raises(OSError, match="reset"):
            first(fail)
        with pytest.raises(DeviceFarmError, match="shared upload for k failed"):
            waiting(succeed)
        with uploads.hold("k") as later:
            assert later(succeed) == ("arn:1", False)

    assert made == ["fail", "ok"]


@pytest.mark.parametrize("code", ["NotFoundException", "ArgumentException"])
def test_a_rejected_reused_upload_falls_back_to_one_upload_per_job(
    tmp_path: Path, code: str
) -> None:
    client = _Client(runs=2)
    client.reject_reused = code
    work = _work(tmp_path, "a.yaml", "b.yaml", "c.yaml")
    provider = _provider(client, _Transfer())

    errors = _together(provider, tmp_path, [(work, s, _SHARE) for s in ("a.yaml", "b.yaml")])
    later = _together(provider, tmp_path, [(work, "c.yaml", _SHARE)])

    assert errors == [None, None] and later == [None]
    assert len(client.scheduled) == 3
    # The rejected reuse uploaded again, and the job after it shared nothing to begin with.
    assert client.count("ANDROID_APP") == 3
    assert client.count("APPIUM_PYTHON_TEST_PACKAGE") == 3


def test_another_schedule_failure_is_not_retried(tmp_path: Path) -> None:
    client = _Client(runs=2)
    client.reject_reused = "LimitExceededException"
    work = _work(tmp_path, "a.yaml", "b.yaml")

    errors = _together(
        _provider(client, _Transfer()),
        tmp_path,
        [(work, "a.yaml", _SHARE), (work, "b.yaml", _SHARE)],
    )

    assert sum(isinstance(e, _RejectedUpload) for e in errors) == 1
    assert client.count("ANDROID_APP") == 1


def test_a_schedule_refused_for_another_reason_keeps_sharing_on(tmp_path: Path) -> None:
    client = _Client()
    client.reject_all = "ArgumentException"
    work = _work(tmp_path, "a.yaml", "b.yaml")
    provider = _provider(client, _Transfer())

    refused = _together(provider, tmp_path, [(work, "a.yaml", _SHARE), (work, "b.yaml", _SHARE)])
    refused_attempts, apps_before = client.attempts, client.count("ANDROID_APP")
    client.reject_all = None
    client._runs = client.attempts + 2
    later = _together(provider, tmp_path, [(work, "a.yaml", _SHARE), (work, "b.yaml", _SHARE)])

    assert all(isinstance(e, _RejectedUpload) for e in refused)
    # The second job reused the first one's uploads, retried on its own, and was refused again.
    assert (refused_attempts, apps_before) == (3, 2)
    assert later == [None, None]
    assert client.count("ANDROID_APP") - apps_before == 1


def test_a_failed_rebuild_still_serves_the_holders_its_kept_upload_covers() -> None:
    uploads = SharedUploads()

    def never() -> tuple[str, frozenset[str]]:
        raise AssertionError("the kept upload should have served this holder")

    def fail() -> tuple[str, frozenset[str]]:
        raise OSError("reset")

    with uploads.hold("k") as h1, uploads.hold("k") as h2:
        assert h1(lambda: ("arn:1", frozenset({"a"})), needs="a") == ("arn:1", False)
        with pytest.raises(OSError, match="reset"):
            h2(fail, needs="b")
        assert h1(never, needs="a") == ("arn:1", True)
        with pytest.raises(DeviceFarmError, match="shared upload for k failed"):
            h2(never, needs="b")
