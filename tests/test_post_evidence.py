"""Tests for the post-evidence composite action's renderer (BE-0504).

Fixtures come from Bajutsu's own manifest writer, so a manifest schema change breaks these tests in
the PR that makes it instead of silently emptying the comment.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from bajutsu.common.evidence import Artifact
from bajutsu.common.orchestrator import RunResult, StepOutcome
from bajutsu.common.report import manifest_dict, scenario_result_dict
from bajutsu.common.report.manifest import SCHEMA_VERSION

_PATH = Path(__file__).resolve().parent.parent / ".github/actions/post-evidence/post_evidence.py"
_spec = importlib.util.spec_from_file_location("post_evidence", _PATH)
assert _spec and _spec.loader
pe = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pe
_spec.loader.exec_module(pe)

RUN_ID = "20261125-120000"


def _failing(name: str = "login", sid: str = "00-login", reason: str = "no such id") -> RunResult:
    return RunResult(
        scenario=name,
        ok=False,
        failure=reason,
        sid=sid,
        steps=[
            StepOutcome(index=0, action="tap", ok=True),
            StepOutcome(
                index=1,
                action="tap",
                ok=False,
                reason=reason,
                artifacts=[
                    Artifact(f"{sid}/step1/before.png", "screenshot", "driver"),
                    Artifact(f"{sid}/step1/after.png", "screenshot", "driver"),
                ],
            ),
        ],
        artifacts=[
            Artifact(f"{sid}/scenario.webm", "video", "playwright"),
            Artifact(f"{sid}/device.log", "deviceLog", "driver"),
        ],
    )


def _passing() -> RunResult:
    return RunResult(
        scenario="ok", ok=True, sid="01-ok", steps=[StepOutcome(index=0, action="tap", ok=True)]
    )


def _make_run(root: Path, results: list[RunResult], *, manifest: bool = True) -> Path:
    run = root / RUN_ID
    for r in results:
        d = run / r.sid
        d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps(scenario_result_dict(r)))
        for a in [*r.artifacts, *[a for s in r.steps for a in s.artifacts]]:
            f = run / a.name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x" * 10)
    if manifest:
        (run / "manifest.json").write_text(json.dumps(manifest_dict(RUN_ID, results)))
    return run


def _cfg(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "comment_key": "wf-job",
        "max_failures": 10,
        "upload": True,
        "artifact_url": "https://example.test/zip",
        "run_url": "https://example.test/run",
        "sha": "abcdef1234567",
        "base_url": "",
        "log_tail_kb": 1024,
        "size_warn_mb": 500,
        "zip_status": "ok",
        "state": {},
        "slot_urls": [],
    }
    base.update(kw)
    return pe.RenderConfig(**base)


def test_renders_failed_scenario_step_and_reason(tmp_path: Path) -> None:
    run = _make_run(tmp_path, [_failing(), _passing()])
    body = pe.render(pe.load_runs(run), _cfg())
    assert body.startswith("<!-- bajutsu-evidence:wf-job -->\n")
    assert "1 passed, 1 failed" in body
    assert "abcdef1" in body
    assert "step 1" in body and "no such id" in body
    assert "00-login/step1/after.png" in body  # prefers after.png
    assert "[Whole run (zip)](https://example.test/zip)" in body


def test_slot_urls_link_non_zipped_artifacts(tmp_path: Path) -> None:
    run = _make_run(tmp_path, [_failing()])
    cfg = _cfg(slot_urls=[{"screenshot": "https://a/shot", "video": "https://a/vid"}])
    body = pe.render(pe.load_runs(run), cfg)
    assert "[open](https://a/shot)" in body and "[open](https://a/vid)" in body


def test_no_manifest_reads_result_json_and_says_incomplete(tmp_path: Path) -> None:
    run = _make_run(tmp_path, [_failing()], manifest=False)
    runs = pe.load_runs(run)
    assert runs[0].manifest is None
    body = pe.render(runs, _cfg())
    assert "did not complete" in body and "incomplete" in body and "no such id" in body


def test_root_with_several_runs_names_dir_without_manifest(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    _make_run(tmp_path / "a", [_failing()]).rename(root / "run-a")
    _make_run(tmp_path / "b", [_failing(sid="00-other")], manifest=False).rename(root / "run-b")
    runs = pe.load_runs(root)
    assert [r.rel for r in runs] == ["run-a", "run-b"]
    body = pe.render(runs, _cfg())
    assert body.count("did not complete") == 1 and "run-b" in body


def test_scenario_level_failure_without_failed_step() -> None:
    view = pe.ScenarioView("s", False, "setup broke", "00-s", [{"index": 0, "ok": True}], [])
    run = pe.RunView("r", Path(), "", {"schemaVersion": SCHEMA_VERSION}, [view])
    body = pe.render([run], _cfg())
    assert "setup broke" in body and "step " not in body


def test_old_schema_version_gets_a_notice(tmp_path: Path) -> None:
    run = _make_run(tmp_path, [_failing()])
    m = json.loads((run / "manifest.json").read_text())
    m["schemaVersion"] = 1
    (run / "manifest.json").write_text(json.dumps(m))
    body = pe.render(pe.load_runs(run), _cfg())
    assert "does not understand" in body and "no such id" not in body


def test_min_schema_never_exceeds_current() -> None:
    assert pe.MIN_SCHEMA_VERSION <= SCHEMA_VERSION


def test_untrusted_text_cannot_mention_inject_or_forge_marker() -> None:
    evil = "@everyone <img src=x> <!-- <!-- bajutsu-evidence:k --> --> ```x```"
    out = pe.code(evil)
    assert "<!--" not in out and "-->" not in out
    assert out.startswith("`")
    multi = pe.code("a\n```\nb")
    assert multi.strip().startswith("````")


def test_failure_list_is_capped_and_counted(tmp_path: Path) -> None:
    results = [_failing(f"s{i}", f"0{i}-s{i}") for i in range(4)]
    body = pe.render(pe.load_runs(_make_run(tmp_path, results)), _cfg(max_failures=2))
    assert "…and 2 more failed scenario(s)" in body


def test_body_is_cut_below_the_comment_cap() -> None:
    long_reason = "x" * 5000
    views = [pe.ScenarioView(f"s{i}", False, long_reason, "", [], []) for i in range(300)]
    run = pe.RunView("r", Path(), "", {"schemaVersion": SCHEMA_VERSION}, views)
    body = pe.render([run], _cfg(max_failures=300))
    assert len(body) <= pe.MAX_COMMENT_CHARS + 50


def test_base_url_embeds_percent_encoded_screenshot_and_report(tmp_path: Path) -> None:
    run = _make_run(tmp_path, [_failing(sid="00-a b")])
    body = pe.render(pe.load_runs(run), _cfg(base_url="https://cdn.test/r/"))
    assert "![](https://cdn.test/r/00-a%20b/step1/after.png)" in body
    assert "[Full report](https://cdn.test/r/report.html)" in body


def test_upload_false_names_path_and_makes_no_truncation_claim(tmp_path: Path) -> None:
    run = _make_run(tmp_path, [_failing()])
    cfg = _cfg(upload=False, zip_status="external", state={"truncated": ["x"]})
    body = pe.render(pe.load_runs(run), cfg)
    assert "truncated" not in body and "cut to" not in body
    assert f"{RUN_ID}/00-login/step1/after.png" in body


def test_upload_failure_and_size_skip_notices(tmp_path: Path) -> None:
    runs = pe.load_runs(_make_run(tmp_path, [_failing()]))
    failed = pe.render(runs, _cfg(zip_status="failed"))
    assert "upload failed" in failed and "Whole run (zip)" not in failed
    skipped = pe.render(runs, _cfg(zip_status="skipped-size", state={"bytes": 3 * 1024 * 1024}))
    assert "skipped" in skipped and "3 MB" in skipped


def test_stage_truncates_oversize_device_log_only(tmp_path: Path) -> None:
    run = _make_run(tmp_path, [_failing()])
    log = run / "00-login/device.log"
    log.write_bytes(b"A" * 3000 + b"TAIL")
    state = pe.stage_runs(pe.load_runs(run), tmp_path / "stage", 1, 0)
    staged = (tmp_path / "stage" / RUN_ID / "00-login/device.log").read_bytes()
    assert staged.startswith(b"[post-evidence: truncated, original 3004 bytes")
    assert staged.endswith(b"TAIL") and len(staged) < 1300
    assert state["truncated"] == [f"{RUN_ID}/00-login/device.log"]
    assert log.stat().st_size == 3004  # the source is never edited
    assert (tmp_path / "stage" / RUN_ID / "manifest.json").read_text() == (
        run / "manifest.json"
    ).read_text()


def test_stage_truncates_without_manifest_and_zero_keeps_whole(tmp_path: Path) -> None:
    run = _make_run(tmp_path, [_failing()], manifest=False)
    (run / "00-login/device.log").write_bytes(b"B" * 5000)
    kept = pe.stage_runs(pe.load_runs(run), tmp_path / "s0", 0, 0)
    assert kept["truncated"] == []
    cut = pe.stage_runs(pe.load_runs(run), tmp_path / "s1", 1, 0)
    assert len(cut["truncated"]) == 1


def test_size_guard_skips_zip_over_threshold(tmp_path: Path) -> None:
    run = _make_run(tmp_path, [_failing()])
    (run / "00-login/big.bin").write_bytes(b"0" * (2 * 1024 * 1024))
    assert pe.stage_runs(pe.load_runs(run), tmp_path / "a", 1024, 1)["skipZip"] is True
    assert pe.stage_runs(pe.load_runs(run), tmp_path / "b", 1024, 0)["skipZip"] is False


def test_select_slots_cap_and_missing_files(tmp_path: Path) -> None:
    results = [_failing(f"s{i}", f"0{i}-s{i}") for i in range(5)]
    run = _make_run(tmp_path, results)
    (run / "01-s1/scenario.webm").unlink()
    slots = pe.select_slots(pe.load_runs(run))
    assert len(slots) == 3
    assert slots[0]["screenshot"] == f"{RUN_ID}/00-s0/step1/after.png"
    assert slots[1]["video"] == ""


def test_sanitize_artifact_name() -> None:
    assert pe.sanitize_artifact_name('a:b/c"d<e>f|g*h?i\\j\nk') == "a-b-c-d-e-f-g-h-i-j-k"


def test_marker_match_tolerates_crlf_and_trailing_space() -> None:
    assert pe.is_marker_line("<!-- bajutsu-evidence:k -->\r", "k")
    assert pe.is_marker_line("<!-- bajutsu-evidence:k -->  ", "k")
    assert not pe.is_marker_line("<!-- bajutsu-evidence:other -->", "k")


def test_find_comment_pages_and_matches_author(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = "<!-- bajutsu-evidence:k -->"
    pages = {
        1: [{"id": i, "body": "x", "user": {"login": "u"}} for i in range(100)],
        2: [
            {"id": 900, "body": marker, "user": {"login": "mallory"}},
            {"id": 901, "body": marker + "\r\nrest", "user": {"login": "github-actions[bot]"}},
        ],
    }
    seen: list[str] = []

    def fake(method: str, url: str, token: str, body: Any = None) -> Any:
        seen.append(url)
        return pages[int(url.rsplit("page=", 1)[1])]

    monkeypatch.setattr(pe, "_api", fake)
    assert pe.find_comment("https://api/x", "t", "k", "github-actions[bot]") == 901
    assert len(seen) == 2


def test_upsert_creates_or_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake(method: str, url: str, token: str, body: Any = None) -> Any:
        calls.append((method, url))
        return []

    monkeypatch.setattr(pe, "_api", fake)
    assert pe.upsert_comment("https://api", "o/r", "7", "t", "k", "a", "b") == "created"
    assert calls[-1] == ("POST", "https://api/repos/o/r/issues/7/comments")
    monkeypatch.setattr(pe, "find_comment", lambda *a: 5)
    assert pe.upsert_comment("https://api", "o/r", "7", "t", "k", "a", "b") == "updated"
    assert calls[-1] == ("PATCH", "https://api/repos/o/r/issues/comments/5")


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run: Path, **extra: str) -> Path:
    out = tmp_path / "out.txt"
    out.write_text("")
    env = {
        "PE_RUN_DIR": str(run),
        "PE_UPLOAD": "true",
        "PE_ARTIFACT_NAME": "wf:job",
        "RUNNER_TEMP": str(tmp_path / "tmp"),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_RUN_ID": "9",
        **extra,
    }
    (tmp_path / "tmp").mkdir(exist_ok=True)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return out


def test_cmd_stage_then_publish_without_issue_writes_summary_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _make_run(tmp_path, [_failing()])
    out = _env(monkeypatch, tmp_path, run, PE_ZIP_OUTCOME="success", PE_ZIP_URL="https://z")
    assert pe.main(["x", "stage"]) == 0
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert outputs["upload-zip"] == "true"
    assert Path(outputs["screenshot-1"]).name == "wf-job-screenshot-1.png"
    assert Path(outputs["video-1"]).name == "wf-job-video-1.webm"
    assert outputs["artifact-name"] == "wf-job"
    assert outputs["screenshot-2"] == ""
    assert pe.main(["x", "publish"]) == 0
    assert "Bajutsu run failed" in (tmp_path / "summary.md").read_text()


def test_publish_warns_and_exits_zero_when_api_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _make_run(tmp_path, [_failing()])
    _env(monkeypatch, tmp_path, run, PE_ISSUE_NUMBER="3", PE_TOKEN="t", PE_UPLOAD="false")

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("403")

    monkeypatch.setattr(pe, "_api", boom)
    assert pe.main(["x", "publish"]) == 0
    assert "::warning::" in capsys.readouterr().out


def test_stage_with_upload_false_stages_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _make_run(tmp_path, [_failing()])
    out = _env(monkeypatch, tmp_path, run, PE_UPLOAD="false")
    assert pe.main(["x", "stage"]) == 0
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert outputs["upload-zip"] == "" and outputs["screenshot-1"] == ""
    assert not (tmp_path / "tmp" / "post-evidence-stage").exists()


def test_usage_error() -> None:
    assert pe.main(["x"]) == 2


def test_empty_run_dir_is_reported_not_passed(tmp_path: Path) -> None:
    body = pe.render(pe.load_runs(tmp_path), _cfg(zip_status="none"))
    assert "incomplete" in body and "No results found" in body and "run passed" not in body
