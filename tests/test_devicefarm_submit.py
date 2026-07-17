"""Tests for the AWS Device Farm batch submitter (BE-0235, `scripts/devicefarm_submit.py`).

The submitter is CI-side glue, decoupled from the deterministic core: it packages Bajutsu +
scenarios, renders a Device Farm custom-environment test spec that runs `bajutsu run --backend adb`,
uploads/polls/downloads via the AWS SDK, and derives pass/fail from **Bajutsu's own manifest** — not
from Device Farm's run classification. These tests cover every piece that does not touch AWS: spec
rendering, package assembly, manifest → verdict, and the upload/poll/collect state machine driven by
a hand-written fake client (no real network, no mock library — the AWS SDK is the only external
seam, and it is replaced by an in-memory fake).
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.devicefarm_submit import (
    DeviceFarmError,
    Verdict,
    _safe_extract,
    build_package,
    main,
    render_test_spec,
    submit_and_collect,
    verdict_from_manifest,
)

# ---------------------------------------------------------------------------
# render_test_spec
# ---------------------------------------------------------------------------


def test_test_spec_is_valid_yaml_with_the_four_phases() -> None:
    spec = render_test_spec(
        ["demos/showcase/scenarios/smoke.yaml"],
        target="showcase-compose",
        config="demos/showcase/showcase.config.yaml",
    )
    doc = yaml.safe_load(spec)
    assert doc["version"] == 0.1
    assert set(doc["phases"]) == {"install", "pre_test", "test", "post_test"}


def test_test_spec_runs_bajutsu_over_adb_for_each_scenario() -> None:
    spec = render_test_spec(
        ["a.yaml", "b.yaml"],
        target="showcase-compose",
        config="showcase.config.yaml",
    )
    test_cmds = yaml.safe_load(spec)["phases"]["test"]["commands"]
    runs = [c for c in test_cmds if "bajutsu run" in c]
    assert len(runs) == 2
    assert all("--backend adb" in c for c in runs)
    assert all("--target showcase-compose" in c for c in runs)
    assert any("a.yaml" in c for c in runs)
    assert any("b.yaml" in c for c in runs)


def test_test_spec_copies_runs_into_the_device_farm_log_dir() -> None:
    # Artifact retrieval hinges on runs/ landing under $DEVICEFARM_LOG_DIR in post_test.
    spec = render_test_spec(["s.yaml"], target="t", config="c.yaml")
    doc = yaml.safe_load(spec)
    post = " ".join(doc["phases"]["post_test"]["commands"])
    assert "runs" in post
    assert "DEVICEFARM_LOG_DIR" in post
    assert "DEVICEFARM_LOG_DIR" in " ".join(doc["artifacts"])


def test_test_spec_selects_the_requested_python_version() -> None:
    spec = render_test_spec(["s.yaml"], target="t", config="c.yaml", python_version="3.13")
    install = " ".join(yaml.safe_load(spec)["phases"]["install"]["commands"])
    assert "3.13" in install


def test_test_spec_rejects_an_empty_scenario_list() -> None:
    # A spec with no scenario would run nothing and silently "pass" — fail loud instead.
    with pytest.raises(ValueError, match="scenario"):
        render_test_spec([], target="t", config="c.yaml")


# ---------------------------------------------------------------------------
# build_package
# ---------------------------------------------------------------------------


def test_package_bundles_files_and_directories_under_their_arcnames(tmp_path: Path) -> None:
    (tmp_path / "showcase.config.yaml").write_text("targets: {}")
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "smoke.yaml").write_text("scenarios: []")
    (scenarios / "nested").mkdir()
    (scenarios / "nested" / "deep.yaml").write_text("scenarios: []")
    out = tmp_path / "package.zip"

    build_package(
        [
            (tmp_path / "showcase.config.yaml", "showcase.config.yaml"),
            (scenarios, "scenarios"),
        ],
        out,
    )

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "showcase.config.yaml" in names
    assert "scenarios/smoke.yaml" in names
    assert "scenarios/nested/deep.yaml" in names


def test_package_raises_when_a_source_is_missing(tmp_path: Path) -> None:
    with pytest.raises(DeviceFarmError, match="not found"):
        build_package([(tmp_path / "ghost.whl", "ghost.whl")], tmp_path / "p.zip")


def test_package_skips_symlinks_under_a_packaged_directory(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.yaml").write_text("scenarios: []")
    # A symlink pointing at the real file must not be zipped (a link could point outside the tree).
    (src / "link.yaml").symlink_to(src / "real.yaml")
    out = tmp_path / "package.zip"

    build_package([(src, "src")], out)

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "src/real.yaml" in names
    assert "src/link.yaml" not in names


def test_package_excludes_vcs_and_build_noise_dirs(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "keep.yaml").write_text("scenarios: []")
    # Noise dirs (VCS metadata, build caches, scratch output) must never reach the upload.
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("[core]")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    (src / "runs").mkdir()
    (src / "runs" / "leftover.log").write_text("noise")
    out = tmp_path / "package.zip"

    build_package([(src, "src")], out)

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "src/keep.yaml" in names
    assert "src/.git/config" not in names
    assert "src/__pycache__/x.pyc" not in names
    assert "src/runs/leftover.log" not in names


def test_package_root_arcname_places_contents_at_the_zip_root(tmp_path: Path) -> None:
    # `--package .=.` puts the repo (its real tests/ + pyproject.toml) at the zip root, which
    # Device Farm's APPIUM_PYTHON_TEST_PACKAGE validation requires: the tests/ directory must sit
    # at the root, and `pip install <root>` needs pyproject.toml there too.
    src = tmp_path / "repo"
    src.mkdir()
    (src / "pyproject.toml").write_text("[project]\nname = 'x'")
    (src / "tests").mkdir()
    (src / "tests" / "test_x.py").write_text("def test_x() -> None: ...")
    out = tmp_path / "package.zip"

    build_package([(src, ".")], out)

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "pyproject.toml" in names
    assert "tests/test_x.py" in names
    # Nothing is nested under a "./" or "repo/" prefix — contents land directly at the root.
    assert not any(n.startswith(("./", "repo/")) for n in names)


def test_package_never_zips_the_output_archive_into_itself(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "keep.yaml").write_text("scenarios: []")
    # The output zip commonly lands inside the tree being packaged: `--out` defaults into the
    # repo root that `--package .=.` walks. Zipping the archive into itself reads back its
    # own growing bytes and blows the file up without bound, so it must be skipped.
    out = src / "package.zip"

    build_package([(src, "src")], out)

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "src/keep.yaml" in names
    assert "src/package.zip" not in names


# ---------------------------------------------------------------------------
# verdict_from_manifest
# ---------------------------------------------------------------------------


def _write_manifest(run_dir: Path, scenarios: list[tuple[str, bool]]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "ok": all(ok for _, ok in scenarios),
                "scenarios": [{"scenario": name, "ok": ok} for name, ok in scenarios],
            }
        )
    )


def test_verdict_is_pass_when_every_scenario_passed(tmp_path: Path) -> None:
    _write_manifest(tmp_path / "runs" / "20260714-100000", [("smoke", True), ("search", True)])
    v = verdict_from_manifest(tmp_path / "runs")
    assert v == Verdict(ok=True, passed=2, total=2, failures=[])


def test_verdict_is_fail_and_names_the_failing_scenario(tmp_path: Path) -> None:
    _write_manifest(tmp_path / "runs" / "20260714-100000", [("smoke", True), ("search", False)])
    v = verdict_from_manifest(tmp_path / "runs")
    assert not v.ok
    assert v.passed == 1
    assert v.total == 2
    assert v.failures == ["search"]


def test_verdict_aggregates_across_multiple_run_manifests(tmp_path: Path) -> None:
    _write_manifest(tmp_path / "runs" / "20260714-100000", [("smoke", True)])
    _write_manifest(tmp_path / "runs" / "20260714-100100", [("search", False)])
    v = verdict_from_manifest(tmp_path / "runs")
    assert not v.ok
    assert v.total == 2
    assert v.failures == ["search"]


def test_verdict_fails_loud_when_no_manifest_was_produced(tmp_path: Path) -> None:
    # No manifest means the run never produced a verdict — that is a failure, not a silent pass.
    (tmp_path / "runs").mkdir()
    v = verdict_from_manifest(tmp_path / "runs")
    assert not v.ok
    assert v.total == 0


def test_verdict_fails_loud_when_a_manifest_is_unreadable(tmp_path: Path) -> None:
    # A corrupted/truncated manifest must count as a failure, not vanish from the tally — otherwise
    # a broken run would silently drop from total/passed and flip the verdict to a false pass.
    run_dir = tmp_path / "runs" / "20260714-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text('{"scenarios": [')  # truncated, invalid JSON
    v = verdict_from_manifest(tmp_path / "runs")
    assert not v.ok
    assert v.total >= 1
    assert any("manifest.json" in f for f in v.failures)


# ---------------------------------------------------------------------------
# _safe_extract (zip-slip guard on downloaded artifacts)
# ---------------------------------------------------------------------------


def test_safe_extract_extracts_a_benign_artifact(tmp_path: Path) -> None:
    # A well-formed artifact zip extracts normally under `dest`.
    zip_path = tmp_path / "artifact.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("runs/20260714/manifest.json", '{"scenarios": []}')
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        _safe_extract(zf, dest)
    assert (dest / "runs" / "20260714" / "manifest.json").read_text() == '{"scenarios": []}'


def test_safe_extract_rejects_a_traversal_member(tmp_path: Path) -> None:
    # A crafted `../escape` member must be rejected loud (DeviceFarmError) and never written outside
    # `dest` — a zip-slip attempt from a compromised artifact source must not escape the run dir.
    zip_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../escape.txt", "pwned")
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(zip_path) as zf, pytest.raises(DeviceFarmError, match="unsafe path"):
        _safe_extract(zf, dest)
    assert not (tmp_path / "escape.txt").exists()


# ---------------------------------------------------------------------------
# submit_and_collect (fake AWS SDK seam)
# ---------------------------------------------------------------------------


class _FakeClient:
    """An in-memory stand-in for the boto3 `devicefarm` client.

    Records the calls the submitter makes and returns canned responses. Uploads report SUCCEEDED and
    the run reports COMPLETED so the happy path drives straight through; individual tests override
    `run_status`/`upload_status` to exercise the failure branches.
    """

    def __init__(
        self,
        *,
        upload_status: str = "SUCCEEDED",
        run_status: str = "COMPLETED",
        upload_message: str | None = None,
    ) -> None:
        self.upload_status = upload_status
        self.run_status = run_status
        self.upload_message = upload_message
        self.scheduled: dict[str, Any] | None = None
        self._n = 0

    def create_upload(self, *, projectArn: str, name: str, type: str) -> dict[str, Any]:  # noqa: N803 - boto3 kwargs
        self._n += 1
        return {"upload": {"arn": f"arn:upload/{self._n}/{name}", "url": f"https://s3/{name}"}}

    def get_upload(self, *, arn: str) -> dict[str, Any]:
        upload: dict[str, Any] = {"arn": arn, "status": self.upload_status}
        if self.upload_message is not None:
            upload["message"] = self.upload_message
        return {"upload": upload}

    def schedule_run(self, **kwargs: Any) -> dict[str, Any]:
        self.scheduled = kwargs
        return {"run": {"arn": "arn:run/1"}}

    def get_run(self, *, arn: str) -> dict[str, Any]:
        return {"run": {"arn": arn, "status": self.run_status, "result": "PASSED"}}

    def list_artifacts(self, *, arn: str, type: str) -> dict[str, Any]:
        return {
            "artifacts": [
                {"name": "runs", "type": "CUSTOMER_ARTIFACT", "url": "https://s3/runs.zip"}
            ]
        }


class _FakeTransfer:
    """Records uploads; on download, materializes a runs/ tree with the given verdict at `dest`."""

    def __init__(self, *, downloaded_ok: bool = True) -> None:
        self.uploaded: list[str] = []
        self.downloaded_ok = downloaded_ok

    def upload(self, url: str, path: Path) -> None:
        self.uploaded.append(url)

    def download(self, url: str, dest: Path) -> None:
        run = dest / "runs" / "20260714-120000"
        _write_manifest(run, [("smoke", self.downloaded_ok)])


def _submit(client: _FakeClient, transfer: _FakeTransfer, tmp_path: Path) -> Verdict:
    package = tmp_path / "package.zip"
    package.write_bytes(b"zip")
    spec = tmp_path / "testspec.yml"
    spec.write_text("version: 0.1")
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    return submit_and_collect(
        client,
        transfer,
        project_arn="arn:project/1",
        device_pool_arn="arn:pool/1",
        app_apk=apk,
        package_zip=package,
        spec_yaml=spec,
        dest=tmp_path / "out",
        sleep=lambda _: None,
    )


def test_submit_and_collect_uploads_schedules_and_returns_the_manifest_verdict(
    tmp_path: Path,
) -> None:
    client = _FakeClient()
    transfer = _FakeTransfer(downloaded_ok=True)

    verdict = _submit(client, transfer, tmp_path)

    # App APK, test package, and test spec are each uploaded (three presigned PUTs).
    assert len(transfer.uploaded) == 3
    assert client.scheduled is not None
    assert verdict.ok
    assert verdict.passed == 1


def test_submit_and_collect_reports_bajutsus_verdict_not_device_farms(tmp_path: Path) -> None:
    # Device Farm's own run.result is PASSED, but Bajutsu's manifest says the scenario failed.
    # The submitter must surface Bajutsu's verdict.
    client = _FakeClient(run_status="COMPLETED")
    transfer = _FakeTransfer(downloaded_ok=False)

    verdict = _submit(client, transfer, tmp_path)

    assert not verdict.ok
    assert verdict.failures == ["smoke"]


def test_submit_and_collect_fails_loud_on_a_failed_upload(tmp_path: Path) -> None:
    client = _FakeClient(upload_status="FAILED")
    transfer = _FakeTransfer()
    with pytest.raises(DeviceFarmError, match="upload"):
        _submit(client, transfer, tmp_path)


def test_failed_upload_surfaces_device_farms_reason(tmp_path: Path) -> None:
    # Device Farm records *why* an upload failed in the upload's message/metadata; the error must
    # carry it so the operator isn't left guessing at an opaque, remote validation failure.
    client = _FakeClient(upload_status="FAILED", upload_message="invalid test package structure")
    transfer = _FakeTransfer()
    with pytest.raises(DeviceFarmError, match="invalid test package structure"):
        _submit(client, transfer, tmp_path)


# ---------------------------------------------------------------------------
# main (argv parsing — --package-only build path)
# ---------------------------------------------------------------------------


def test_main_package_only_builds_from_source_arcname_entries(tmp_path: Path) -> None:
    # `--package-only` walks the whole argv path: parse `--package SRC=ARCNAME`, split on `=`, and
    # build the zip — no AWS credentials needed. Regression guard for the `--package` type: an entry
    # must arrive as a str so `raw.partition("=")` works (parsing it as a Path would crash here).
    config = tmp_path / "showcase.config.yaml"
    config.write_text("targets: {}")
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "smoke.yaml").write_text("scenarios: []")
    out = tmp_path / "package.zip"

    exit_code = main(
        [
            "--scenario",
            "scenarios/smoke.yaml",
            "--target",
            "showcase-compose",
            "--config",
            "showcase.config.yaml",
            "--app-apk",
            str(tmp_path / "app.apk"),
            "--package",
            f"{config}=showcase.config.yaml",
            "--package",
            f"{scenarios}=scenarios",
            "--out",
            str(out),
            "--package-only",
        ]
    )

    assert exit_code == 0
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "showcase.config.yaml" in names
    assert "scenarios/smoke.yaml" in names
