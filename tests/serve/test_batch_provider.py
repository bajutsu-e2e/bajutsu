"""Tests for the batch-cloud provider seam serve dispatches a cloud-batch job through (BE-0336).

The registry is provider-generic (fail-closed on an unknown kind); the Device Farm concrete is driven
against an in-memory fake client/transfer — no real AWS — so its packaging + single-device selection +
collect logic is exercised without the ``aws`` extra.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import io
import json
import textwrap
import zipfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

from bajutsu.serve import batch_provider as bp


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    # The registry is process-global; snapshot and restore it so a registration in one test never
    # leaks into another (mirrors the serve env-snapshot fixtures).
    saved = dict(bp._PROVIDERS)
    try:
        yield
    finally:
        bp._PROVIDERS.clear()
        bp._PROVIDERS.update(saved)


def test_resolve_returns_the_registered_provider() -> None:
    sentinel: Any = object()
    bp.register("df", sentinel)
    assert bp.resolve("df") is sentinel


def test_resolve_fails_closed_on_an_unknown_provider() -> None:
    # An unknown provider is a clean config error raised here, not a silent no-op that would let a
    # cloud-batch job quietly vanish.
    with pytest.raises(ValueError, match="unknown batch provider 'nope'"):
        bp.resolve("nope")


def _render_test_spec_calls(source: str) -> list[ast.Call]:
    """Every `render_test_spec(...)` call node in `source` (a class method's own source has leading
    indentation `ast.parse` rejects, hence the dedent)."""
    tree = ast.parse(textwrap.dedent(source))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "render_test_spec"
    ]


def test_provider_never_wires_a_request_into_the_pre_test_hook() -> None:
    # render_test_spec's pre_test_commands hook (BE-0432) is Python-API-only: no request-sourced value
    # may reach it, or a client body would hand a shell on the host holding the run's AWS role. Guard
    # the one in-tree call site structurally (an AST walk, not a substring match on the source text) so
    # a comment merely mentioning the parameter can't trip a false positive, and so a future
    # `BatchRequest` field named anything (`setup_commands`, `device_setup`) wired into the hook still
    # trips this, forcing a reviewer to confirm the change consciously.
    calls = _render_test_spec_calls(inspect.getsource(bp.DeviceFarmBatchProvider.submit))
    assert calls, "expected DeviceFarmBatchProvider.submit to call render_test_spec"
    for call in calls:
        keyword_names = {keyword.arg for keyword in call.keywords}
        assert "pre_test_commands" not in keyword_names
        # `render_test_spec`'s parameter is keyword-only (after the `*` in its signature), so mypy
        # --strict already rejects passing it positionally — nothing to check there. A `**mapping`
        # splat is the one way a keyword-only argument still slips in unnamed; reject one on this call
        # (an `ast.keyword` with `arg is None` is a `**` unpack).
        assert None not in keyword_names
    # And, as documentation of the seam, the request today carries no field the call site could pass.
    field_names = {field.name for field in dataclasses.fields(bp.BatchRequest)}
    assert "pre_test_commands" not in field_names


def _zip_bytes(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buffer.getvalue()


class _FakeClient:
    """A minimal in-memory Device Farm client that drives the happy path and records the schedule."""

    def __init__(self) -> None:
        self.scheduled: dict[str, Any] | None = None
        self.schedule_calls = 0

    def create_upload(self, *, projectArn: str, name: str, type: str) -> dict[str, Any]:  # noqa: N803 - boto3 kwargs
        return {"upload": {"arn": f"arn:upload/{name}", "url": f"https://s3/{name}"}}

    def get_upload(self, *, arn: str) -> dict[str, Any]:
        return {"upload": {"arn": arn, "status": "SUCCEEDED"}}

    def schedule_run(self, **kwargs: Any) -> dict[str, Any]:
        self.scheduled = kwargs
        self.schedule_calls += 1
        return {"run": {"arn": "arn:run/1"}}

    def get_run(self, *, arn: str) -> dict[str, Any]:
        return {"run": {"arn": arn, "status": "COMPLETED"}}

    def list_artifacts(self, *, arn: str, type: str) -> dict[str, Any]:
        return {"artifacts": [{"name": "runs", "extension": "zip", "url": "https://s3/runs.zip"}]}


class _FakeTransfer:
    def __init__(self, *, manifest_ok: bool) -> None:
        self.uploaded: list[str] = []
        self._ok = manifest_ok

    def upload(self, url: str, path: Path) -> None:
        self.uploaded.append(url)

    def download(self, url: str) -> bytes:
        manifest = json.dumps(
            {"ok": self._ok, "scenarios": [{"scenario": "alpha", "ok": self._ok}]}
        )
        return _zip_bytes({"runs/20260101-1/manifest.json": manifest})


def test_devicefarm_provider_submits_one_scenario_reserving_a_single_device(tmp_path: Path) -> None:
    # The Device Farm concrete renders a one-scenario spec, packages the project, and schedules the run
    # with deviceSelectionConfiguration + maxDevices:1 (never a static pool), then reports the verdict
    # from the downloaded manifest tree.
    client = _FakeClient()
    transfer = _FakeTransfer(manifest_ok=True)
    provider = bp.DeviceFarmBatchProvider(
        client=client, transfer=transfer, project_arn="arn:project/1", sleep=lambda _: None
    )
    work = tmp_path / "project"
    work.mkdir()
    (work / "smoke.yaml").write_text("- name: alpha\n  steps: []\n", encoding="utf-8")
    (tmp_path / "app.apk").write_bytes(b"apk")
    request = bp.BatchRequest(
        provider="devicefarm",
        scenario="smoke.yaml",
        target="demo",
        config="bajutsu.config.yaml",
        platform="android",
        app_path=str(tmp_path / "app.apk"),
    )
    dest = tmp_path / "download"

    verdict = provider.submit(request, work_dir=work, dest=dest)

    assert verdict.ok and verdict.passed == 1
    assert len(transfer.uploaded) == 3  # app, test package, and test spec
    assert client.scheduled is not None
    assert client.scheduled["deviceSelectionConfiguration"]["maxDevices"] == 1
    assert "devicePoolArn" not in client.scheduled
    assert (dest / "runs" / "20260101-1" / "manifest.json").exists()


class _Checkpoint:
    """A tiny in-memory stand-in for the durable checkpoint the DB backend provides (BE-0336 Unit 5)."""

    def __init__(self) -> None:
        self.run_arn: str | None = None

    def load(self) -> str | None:
        return self.run_arn

    def save(self, run_arn: str) -> None:
        self.run_arn = run_arn


def _android_request(tmp_path: Path) -> tuple[Path, bp.BatchRequest]:
    work = tmp_path / "project"
    work.mkdir()
    (work / "smoke.yaml").write_text("- name: alpha\n  steps: []\n", encoding="utf-8")
    (tmp_path / "app.apk").write_bytes(b"apk")
    request = bp.BatchRequest(
        provider="devicefarm",
        scenario="smoke.yaml",
        target="demo",
        config="bajutsu.config.yaml",
        platform="android",
        app_path=str(tmp_path / "app.apk"),
    )
    return work, request


def test_devicefarm_provider_checkpoints_the_scheduled_run(tmp_path: Path) -> None:
    # Once the run is scheduled, the provider persists its ARN through the checkpoint so a worker that
    # re-leases the job after a restart can resume polling it instead of resubmitting (BE-0336 Unit 5).
    client = _FakeClient()
    provider = bp.DeviceFarmBatchProvider(
        client=client,
        transfer=_FakeTransfer(manifest_ok=True),
        project_arn="arn:project/1",
        sleep=lambda _: None,
    )
    work, request = _android_request(tmp_path)
    checkpoint = _Checkpoint()

    provider.submit(request, work_dir=work, dest=tmp_path / "d1", checkpoint=checkpoint)

    assert checkpoint.run_arn == "arn:run/1"
    assert client.schedule_calls == 1


def test_devicefarm_provider_resumes_a_scheduled_run_without_resubmitting(tmp_path: Path) -> None:
    # A restart mid-poll re-leases the job; the provider loads the persisted run ARN and resumes
    # polling that run — no re-upload, no reschedule — so the in-flight run (and its reserved device)
    # is not orphaned. The verdict still comes from the downloaded manifest (BE-0336 Unit 5).
    client = _FakeClient()
    transfer = _FakeTransfer(manifest_ok=True)
    provider = bp.DeviceFarmBatchProvider(
        client=client, transfer=transfer, project_arn="arn:project/1", sleep=lambda _: None
    )
    work, request = _android_request(tmp_path)
    checkpoint = _Checkpoint()

    provider.submit(request, work_dir=work, dest=tmp_path / "d1", checkpoint=checkpoint)
    uploads_after_first = len(transfer.uploaded)

    verdict = provider.submit(request, work_dir=work, dest=tmp_path / "d2", checkpoint=checkpoint)

    assert client.schedule_calls == 1  # resumed the scheduled run, never rescheduled
    assert len(transfer.uploaded) == uploads_after_first  # no re-upload on resume
    assert verdict.ok and verdict.passed == 1
    assert (tmp_path / "d2" / "runs" / "20260101-1" / "manifest.json").exists()


# ---------------------------------------------------------------------------
# BatchLifecycleHook integration tests (BE-0435)
# ---------------------------------------------------------------------------


class _CapturingTransfer:
    """Transfer stub that captures uploaded file bytes before temp dirs are cleaned up."""

    def __init__(self, *, manifest_ok: bool) -> None:
        self._ok = manifest_ok
        self.uploaded: list[str] = []
        self.packages: dict[str, bytes] = {}

    def upload(self, url: str, path: Path) -> None:
        self.uploaded.append(url)
        self.packages[url] = path.read_bytes()

    def download(self, url: str) -> bytes:
        manifest = json.dumps(
            {"ok": self._ok, "scenarios": [{"scenario": "alpha", "ok": self._ok}]}
        )
        return _zip_bytes({"runs/20260101-1/manifest.json": manifest})


def _android_request_with_config(
    tmp_path: Path,
    *,
    existing_launch_env: dict[str, str] | None = None,
    scenario_preconditions_launch_env: dict[str, str] | None = None,
) -> tuple[Path, bp.BatchRequest]:
    """Like _android_request but creates bajutsu.config.yaml and a scenario with preconditions."""
    work = tmp_path / "project"
    work.mkdir()

    config: dict[str, Any] = {
        "targets": {
            "demo": {
                "platform": "android",
                "package": "com.example.app",
            }
        }
    }
    if existing_launch_env:
        config["targets"]["demo"]["launchEnv"] = existing_launch_env
    (work / "bajutsu.config.yaml").write_text(
        yaml.dump(config, allow_unicode=True), encoding="utf-8"
    )

    preconditions_block = ""
    if scenario_preconditions_launch_env:
        env_block = "\n".join(
            f"      {k}: {v}" for k, v in scenario_preconditions_launch_env.items()
        )
        preconditions_block = f"\n  preconditions:\n    launchEnv:\n{env_block}"
    (work / "smoke.yaml").write_text(
        f"- name: alpha{preconditions_block}\n  steps: []\n", encoding="utf-8"
    )

    (tmp_path / "app.apk").write_bytes(b"apk")
    request = bp.BatchRequest(
        provider="devicefarm",
        scenario="smoke.yaml",
        target="demo",
        config="bajutsu.config.yaml",
        platform="android",
        app_path=str(tmp_path / "app.apk"),
    )
    return work, request


def _read_config_from_package(packages: dict[str, bytes]) -> dict[str, Any]:
    """Find the test package zip and extract bajutsu.config.yaml from it."""
    for url, data in packages.items():
        if not url.endswith(".zip") or "testspec" in url:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if "bajutsu.config.yaml" in zf.namelist():
                    return yaml.safe_load(zf.read("bajutsu.config.yaml").decode()) or {}
        except zipfile.BadZipFile:
            continue
    raise AssertionError("bajutsu.config.yaml not found in any uploaded package zip")


def _make_provider(
    *,
    client: _FakeClient | None = None,
    transfer: _CapturingTransfer | None = None,
    hooks: Sequence[bp.BatchLifecycleHook] = (),
) -> bp.DeviceFarmBatchProvider:
    return bp.DeviceFarmBatchProvider(
        client=client or _FakeClient(),
        transfer=transfer or _CapturingTransfer(manifest_ok=True),
        project_arn="arn:project/1",
        sleep=lambda _: None,
        hooks=hooks,
    )


def test_no_launch_env_injection_preserves_existing_config(tmp_path: Path) -> None:
    # Without a hook injecting launch_env, the packaged config retains its original values.
    transfer = _CapturingTransfer(manifest_ok=True)
    provider = _make_provider(transfer=transfer)
    work, request = _android_request_with_config(
        tmp_path, existing_launch_env={"EXISTING": "value"}
    )
    original = (work / "bajutsu.config.yaml").read_text(encoding="utf-8")

    provider.submit(request, work_dir=work, dest=tmp_path / "d")

    config = _read_config_from_package(transfer.packages)
    assert config["targets"]["demo"].get("launchEnv", {}).get("EXISTING") == "value"
    # Content round-trips through yaml.safe_load; verify key is present and unchanged
    assert yaml.safe_load(original)["targets"]["demo"]["launchEnv"]["EXISTING"] == "value"


def test_before_submit_launch_env_merged_into_packaged_config(tmp_path: Path) -> None:
    # A hook that sets ctx.launch_env causes the packaged config to contain those keys.
    class _ProxyHook(bp.BatchLifecycleHook):
        def before_submit(self, ctx: bp.BatchContext) -> None:
            ctx.launch_env["PROXY_HOST"] = "proxy.example.com"
            ctx.launch_env["PROXY_PORT"] = "3128"

    transfer = _CapturingTransfer(manifest_ok=True)
    provider = _make_provider(transfer=transfer, hooks=[_ProxyHook()])
    work, request = _android_request_with_config(tmp_path)

    provider.submit(request, work_dir=work, dest=tmp_path / "d")

    config = _read_config_from_package(transfer.packages)
    launch_env = config["targets"]["demo"]["launchEnv"]
    assert launch_env["PROXY_HOST"] == "proxy.example.com"
    assert launch_env["PROXY_PORT"] == "3128"


def test_injected_keys_win_over_existing_config_launch_env(tmp_path: Path) -> None:
    # ctx.launch_env wins over values already in the config's launchEnv on key collision.
    class _OverrideHook(bp.BatchLifecycleHook):
        def before_submit(self, ctx: bp.BatchContext) -> None:
            ctx.launch_env["SHARED_KEY"] = "from_hook"

    transfer = _CapturingTransfer(manifest_ok=True)
    provider = _make_provider(transfer=transfer, hooks=[_OverrideHook()])
    work, request = _android_request_with_config(
        tmp_path, existing_launch_env={"SHARED_KEY": "from_config", "OTHER": "kept"}
    )

    provider.submit(request, work_dir=work, dest=tmp_path / "d")

    config = _read_config_from_package(transfer.packages)
    launch_env = config["targets"]["demo"]["launchEnv"]
    assert launch_env["SHARED_KEY"] == "from_hook"
    assert launch_env["OTHER"] == "kept"


def test_config_not_duplicated_in_zip_when_overlay_applied(tmp_path: Path) -> None:
    # The config arcname must appear exactly once in the package zip when overlaid.
    class _Hook(bp.BatchLifecycleHook):
        def before_submit(self, ctx: bp.BatchContext) -> None:
            ctx.launch_env["K"] = "v"

    transfer = _CapturingTransfer(manifest_ok=True)
    provider = _make_provider(transfer=transfer, hooks=[_Hook()])
    work, request = _android_request_with_config(tmp_path)

    provider.submit(request, work_dir=work, dest=tmp_path / "d")

    pkg_bytes = next(
        data
        for url, data in transfer.packages.items()
        if url.endswith(".zip") and "testspec" not in url
    )
    with zipfile.ZipFile(io.BytesIO(pkg_bytes)) as zf:
        config_count = zf.namelist().count("bajutsu.config.yaml")
    assert config_count == 1


def test_after_run_called_with_verdict_on_success(tmp_path: Path) -> None:
    after_calls: list[Any] = []

    class _RecordHook(bp.BatchLifecycleHook):
        def after_run(self, ctx: bp.BatchContext, verdict: Any) -> None:
            after_calls.append(verdict)

    provider = _make_provider(hooks=[_RecordHook()])
    work, request = _android_request_with_config(tmp_path)

    verdict = provider.submit(request, work_dir=work, dest=tmp_path / "d")

    assert len(after_calls) == 1
    assert after_calls[0] is verdict
    assert after_calls[0].ok


def test_after_run_called_with_none_on_pre_verdict_failure(tmp_path: Path) -> None:
    # When before_submit raises, after_run is called with verdict=None for ALL hooks (including
    # the failing hook itself) and the original exception propagates.
    after_calls: list[tuple[str, Any]] = []

    class _RecordHook(bp.BatchLifecycleHook):
        def after_run(self, ctx: bp.BatchContext, verdict: Any) -> None:
            after_calls.append(("record", verdict))

    class _FailHook(bp.BatchLifecycleHook):
        def before_submit(self, ctx: bp.BatchContext) -> None:
            raise RuntimeError("setup intentionally failed")

        def after_run(self, ctx: bp.BatchContext, verdict: Any) -> None:
            after_calls.append(("fail", verdict))

    provider = _make_provider(hooks=[_RecordHook(), _FailHook()])
    work, request = _android_request_with_config(tmp_path)

    with pytest.raises(RuntimeError, match="setup intentionally failed"):
        provider.submit(request, work_dir=work, dest=tmp_path / "d")

    # Teardown is reverse order (FailHook then RecordHook), both with verdict=None.
    assert after_calls == [("fail", None), ("record", None)]


def test_after_run_called_on_checkpoint_resume(tmp_path: Path) -> None:
    # The resume path (run already scheduled) skips before_submit but still calls after_run.
    before_calls: list[str] = []
    after_calls: list[Any] = []

    class _RecordHook(bp.BatchLifecycleHook):
        def before_submit(self, ctx: bp.BatchContext) -> None:
            before_calls.append("called")

        def after_run(self, ctx: bp.BatchContext, verdict: Any) -> None:
            after_calls.append(verdict)

    provider = _make_provider(hooks=[_RecordHook()])
    work, request = _android_request_with_config(tmp_path)
    checkpoint = _Checkpoint()
    checkpoint.run_arn = "arn:run/existing"  # simulate already-scheduled run

    verdict = provider.submit(request, work_dir=work, dest=tmp_path / "d", checkpoint=checkpoint)

    assert before_calls == []  # before_submit skipped on resume
    assert len(after_calls) == 1
    assert after_calls[0] is verdict


def test_teardown_order_is_reverse_of_setup(tmp_path: Path) -> None:
    order: list[str] = []

    class _HookA(bp.BatchLifecycleHook):
        def before_submit(self, ctx: bp.BatchContext) -> None:
            order.append("A:before")

        def after_run(self, ctx: bp.BatchContext, verdict: Any) -> None:
            order.append("A:after")

    class _HookB(bp.BatchLifecycleHook):
        def before_submit(self, ctx: bp.BatchContext) -> None:
            order.append("B:before")

        def after_run(self, ctx: bp.BatchContext, verdict: Any) -> None:
            order.append("B:after")

    provider = _make_provider(hooks=[_HookA(), _HookB()])
    work, request = _android_request_with_config(tmp_path)

    provider.submit(request, work_dir=work, dest=tmp_path / "d")

    assert order == ["A:before", "B:before", "B:after", "A:after"]


def test_launch_env_collision_with_scenario_preconditions_raises(tmp_path: Path) -> None:
    # If a scenario's preconditions.launchEnv has a key that a hook injects, raise loudly at submit.
    class _ProxyHook(bp.BatchLifecycleHook):
        def before_submit(self, ctx: bp.BatchContext) -> None:
            ctx.launch_env["PROXY_HOST"] = "proxy.example.com"

    provider = _make_provider(hooks=[_ProxyHook()])
    work, request = _android_request_with_config(
        tmp_path, scenario_preconditions_launch_env={"PROXY_HOST": "other"}
    )

    with pytest.raises(ValueError, match="PROXY_HOST"):
        provider.submit(request, work_dir=work, dest=tmp_path / "d")


def test_hooks_never_sourced_from_a_batch_request_field() -> None:
    # BatchRequest carries no hook-related field — hook identity is deploy-time only (BE-0435).
    # An AST walk of submit() confirms that 'hooks' is never passed per-call (it is constructor-only).
    request_fields = {f.name for f in dataclasses.fields(bp.BatchRequest)}
    assert "hooks" not in request_fields, "BatchRequest must not carry a 'hooks' field"

    submit_source = inspect.getsource(bp.DeviceFarmBatchProvider.submit)
    tree = ast.parse(textwrap.dedent(submit_source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                assert kw.arg != "hooks", (
                    "DeviceFarmBatchProvider.submit passes 'hooks' in a call — "
                    "hooks must be wired at construction only, never per-submit"
                )


def test_job_id_reaches_batch_context(tmp_path: Path) -> None:
    received_job_ids: list[str] = []

    class _RecordJobId(bp.BatchLifecycleHook):
        def before_submit(self, ctx: bp.BatchContext) -> None:
            received_job_ids.append(ctx.job_id)

    provider = _make_provider(hooks=[_RecordJobId()])
    work, request = _android_request_with_config(tmp_path)

    provider.submit(request, work_dir=work, dest=tmp_path / "d", job_id="job-abc-123")

    assert received_job_ids == ["job-abc-123"]


# ---------------------------------------------------------------------------
# _check_launch_env_collisions: edge-case branches (BE-0435)
# ---------------------------------------------------------------------------


def test_after_run_hook_failure_does_not_skip_remaining_hooks(tmp_path: Path) -> None:
    # Even when an earlier hook's after_run raises, all later hooks still run.
    ran: list[str] = []

    class _FailHook(bp.BatchLifecycleHook):
        def after_run(self, ctx: bp.BatchContext, verdict: Any) -> None:
            ran.append("fail")
            raise RuntimeError("hook teardown error")

    class _RecordHook(bp.BatchLifecycleHook):
        def after_run(self, ctx: bp.BatchContext, verdict: Any) -> None:
            ran.append("record")

    provider = _make_provider(hooks=[_RecordHook(), _FailHook()])
    work, request = _android_request_with_config(tmp_path)

    with pytest.raises(RuntimeError, match="hook teardown error"):
        provider.submit(request, work_dir=work, dest=tmp_path / "d")

    # Hooks run in reverse; FailHook is last → runs first in teardown → RecordHook still runs
    assert ran == ["fail", "record"]


def test_collision_guard_skips_non_dict_preconditions(tmp_path: Path) -> None:
    # preconditions that is not a mapping (e.g. a scalar) must not raise AttributeError.
    from bajutsu.serve.batch_provider.device_farm_batch_provider import _check_launch_env_collisions

    scenario = tmp_path / "scenario.yaml"
    scenario.write_text("- name: s1\n  preconditions: true\n")
    _check_launch_env_collisions(scenario, {"PROXY_HOST": "proxy.example.com"})


def test_collision_guard_accepts_non_list_scenario_yaml(tmp_path: Path) -> None:
    # If the scenario YAML root is not a list, the guard returns early without raising.
    from bajutsu.serve.batch_provider.device_farm_batch_provider import _check_launch_env_collisions

    scenario = tmp_path / "scenario.yaml"
    scenario.write_text("not_a_list: true\n")
    _check_launch_env_collisions(scenario, {"PROXY_HOST": "proxy.example.com"})


def test_collision_guard_skips_non_dict_scenario_item(tmp_path: Path) -> None:
    # A non-dict item in the scenario list (e.g. a bare string) is silently skipped.
    from bajutsu.serve.batch_provider.device_farm_batch_provider import _check_launch_env_collisions

    scenario = tmp_path / "scenario.yaml"
    scenario.write_text("- just_a_string\n")
    _check_launch_env_collisions(scenario, {"PROXY_HOST": "proxy.example.com"})


def test_collision_guard_accepts_non_overlapping_keys(tmp_path: Path) -> None:
    # A scenario with launchEnv keys that don't overlap with the injected dict must not raise.
    from bajutsu.serve.batch_provider.device_farm_batch_provider import _check_launch_env_collisions

    scenario = tmp_path / "scenario.yaml"
    scenario.write_text("- name: s1\n  preconditions:\n    launchEnv:\n      OTHER_KEY: value\n")
    _check_launch_env_collisions(scenario, {"PROXY_HOST": "proxy.example.com"})
