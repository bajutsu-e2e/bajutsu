"""Tests for the local evidence poster (scripts/post_evidence.py, BE-0503).

The script stays outside the `bajutsu` package so a team can copy it alone, so it is loaded here
from its path. Every GitHub call goes to an in-process fake REST server, so the gate runs offline:
the tests pin the sticky comment (create, then edit in place), the CRLF marker match, the `--key`
rejection before any network call, the no-manifest case, the release sink's guards, and a run
directory the script leaves byte-for-byte unchanged.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import threading
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "post_evidence.py"
_spec = importlib.util.spec_from_file_location("post_evidence", _MODULE_PATH)
assert _spec and _spec.loader
pe = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pe
_spec.loader.exec_module(pe)

OWNER, REPO, NUMBER = "acme", "app", 7
ISSUE = f"/repos/{OWNER}/{REPO}/issues/{NUMBER}"


# --- The fake GitHub ---------------------------------------------------------------------------


@dataclass
class FakeGitHub:
    """The slice of GitHub's REST API the script uses, held in memory."""

    base: str = ""
    login: str | None = "dev"
    comments: list[dict[str, Any]] = field(default_factory=list)
    releases: list[dict[str, Any]] = field(default_factory=list)
    workflows: dict[str, str] = field(default_factory=dict)
    requests: list[tuple[str, str]] = field(default_factory=list)
    blobs: dict[str, bytes] = field(default_factory=dict)
    comments_reply: object = None
    redirect_to: str = ""
    fail_writes: bool = False

    def writes(self) -> list[tuple[str, str]]:
        return [r for r in self.requests if r[0] != "GET"]

    def handle(self, method: str, url: str, body: bytes) -> tuple[int, object]:
        parts = urlsplit(url)
        self.requests.append((method, parts.path))
        if method != "GET" and self.fail_writes:
            return 500, {"message": "boom"}
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        return self._route(method, parts.path, query, body)

    def _route(
        self, method: str, path: str, query: dict[str, str], body: bytes
    ) -> tuple[int, object]:
        if path == "/user" and self.redirect_to:
            return 302, self.redirect_to
        if path == "/user":
            return (200, {"login": self.login}) if self.login else (403, {"message": "no user"})
        if path == f"/repos/{OWNER}/{REPO}" and method == "GET":
            return 200, {"visibility": "private"}
        if path == f"{ISSUE}/comments":
            return self._comments(method, query, body)
        if path.startswith(f"/repos/{OWNER}/{REPO}/issues/comments/"):
            comment = self._by_id(self.comments, path)
            comment["body"] = json.loads(body)["body"]
            return 200, comment
        if path == f"/repos/{OWNER}/{REPO}/releases":
            return self._releases(method, query, body)
        if path.startswith(f"/repos/{OWNER}/{REPO}/releases/"):
            release = self._by_id(self.releases, path)
            release.update(json.loads(body))
            return 200, release
        if path.startswith("/upload/"):
            return self._upload(path, query, body)
        if path == f"/repos/{OWNER}/{REPO}/contents/.github/workflows":
            listing = [{"name": n, "url": f"{self.base}/raw/{n}"} for n in self.workflows]
            return (200, listing) if self.workflows else (404, {"message": "Not Found"})
        if path.startswith("/raw/"):
            return 200, self.workflows[path.rsplit("/", 1)[1]]
        return 404, {"message": f"no route {method} {path}"}

    @staticmethod
    def _by_id(items: list[dict[str, Any]], path: str) -> dict[str, Any]:
        wanted = int(path.rsplit("/", 1)[1])
        return next(i for i in items if i["id"] == wanted)

    def _comments(self, method: str, query: dict[str, str], body: bytes) -> tuple[int, object]:
        if method == "POST":
            n = len(self.comments) + 1
            comment = {
                "id": 1000 + n,
                "body": json.loads(body)["body"],
                "user": {"login": self.login or "app[bot]"},
                "html_url": f"https://github.test/{OWNER}/{REPO}/pull/{NUMBER}#c{n}",
            }
            self.comments.append(comment)
            return 201, comment
        if self.comments_reply is not None:
            return 200, self.comments_reply
        return 200, _page(self.comments, query)

    def _releases(self, method: str, query: dict[str, str], body: bytes) -> tuple[int, object]:
        if method == "GET":
            return 200, _page(self.releases, query)
        rid = len(self.releases) + 1
        release = {
            **json.loads(body),
            "id": rid,
            "assets": [],
            "html_url": f"https://github.test/{OWNER}/{REPO}/releases/{rid}",
            "upload_url": f"{self.base}/upload/{rid}/assets{{?name,label}}",
        }
        self.releases.append(release)
        return 201, release

    def _upload(self, path: str, query: dict[str, str], body: bytes) -> tuple[int, object]:
        release = next(r for r in self.releases if r["id"] == int(path.split("/")[2]))
        asset = {
            "name": query["name"],
            "size": len(body),
            "state": "uploaded",
            "browser_download_url": f"https://github.test/dl/{query['name']}",
        }
        self.blobs[query["name"]] = body
        release["assets"].append(asset)
        return 201, asset


def _page(items: list[dict[str, Any]], query: dict[str, str]) -> list[dict[str, Any]]:
    size, page = int(query.get("per_page", "30")), int(query.get("page", "1"))
    return items[(page - 1) * size : page * size]


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    fake = FakeGitHub()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            status, payload = fake.handle(self.command, self.path, self.rfile.read(length))
            data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_response(status)
            if status == 302:
                self.send_header("Location", str(payload))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PATCH = _serve  # noqa: N815 — the stdlib handler's naming

        def log_message(self, *_: object) -> None:  # keep pytest output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield fake
    server.shutdown()
    server.server_close()


# --- Runs --------------------------------------------------------------------------------------


def _scenario(name: str, ok: bool, sid: str) -> dict[str, Any]:
    steps: list[dict[str, Any]] = [{"index": 1, "action": "launch", "ok": True, "reason": ""}]
    if not ok:
        steps.append(
            {
                "index": 2,
                "action": "tap",
                "ok": False,
                "reason": "no match: id='login'",
                "artifacts": [
                    {"name": f"{sid}/tap/after.png", "kind": "screenshot", "provider": "driver"},
                    {"name": f"{sid}/tap/device.log", "kind": "deviceLog", "provider": "driver"},
                ],
            }
        )
    return {
        "scenario": name,
        "ok": ok,
        "sid": sid,
        "steps": steps,
        "failure": None if ok else "step 2 (tap): no match",
        "artifacts": [] if ok else [{"name": f"{sid}/video.mp4", "kind": "video", "provider": "x"}],
    }


def _write_evidence(run_dir: Path, sid: str, log_bytes: int = 64) -> None:
    (run_dir / sid / "tap").mkdir(parents=True)
    (run_dir / sid / "tap" / "after.png").write_bytes(b"\x89PNG" + b"0" * 200)
    (run_dir / sid / "tap" / "device.log").write_bytes(b"L" * log_bytes)
    (run_dir / sid / "video.mp4").write_bytes(b"V" * 300)


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "runs" / "20261125-101530"
    run.mkdir(parents=True)
    scenarios = [_scenario("home loads", True, "01-home"), _scenario("log in", False, "02-login")]
    manifest = {"runId": run.name, "ok": False, "scenarios": scenarios}
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _write_evidence(run, "02-login", log_bytes=4096)
    return run


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _run(
    github: FakeGitHub, args: list[str], *, token: str | None = "t0ken", tty: bool = False
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    stdin = io.StringIO("y\n")
    stdin.isatty = lambda: tty  # type: ignore[method-assign]
    env = {"GITHUB_API_URL": github.base}
    if token:
        env["GITHUB_TOKEN"] = token
    code = pe.main(args, pe.Io(env=env, stdin=stdin, out=out, err=err, run_gh=lambda: None))
    return code, out.getvalue(), err.getvalue()


TO = f"{OWNER}/{REPO}#{NUMBER}"


# --- Arguments ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "host"),
    [
        ("acme/app#7", None),
        ("https://github.com/acme/app/pull/7", "github.com"),
        ("https://github.com/acme/app/issues/7#issuecomment-1", "github.com"),
    ],
)
def test_parse_target_accepts_slug_and_urls(text: str, host: str | None) -> None:
    assert pe.parse_target(text) == pe.Target("acme", "app", 7, host)


@pytest.mark.parametrize("text", ["#7", "7", "acme/app", "https://github.com/acme/app"])
def test_parse_target_refuses_a_bare_number(text: str) -> None:
    with pytest.raises(pe.PostError):
        pe.parse_target(text)


@pytest.mark.parametrize("key", ["local-->", "a\nb", "local -->", "", "a b", "x<!--"])
def test_bad_key_exits_before_any_network_call(github: FakeGitHub, run_dir: Path, key: str) -> None:
    code, _, err = _run(github, [str(run_dir), "--to", TO, "--key", key, "--yes"])
    assert code == pe.EXIT_USAGE
    assert "--key" in err
    assert github.requests == []


def test_key_allows_the_documented_alphabet() -> None:
    assert pe.check_key("ci.web_E2E-1") == "ci.web_E2E-1"


def test_token_resolution_order() -> None:
    assert pe.resolve_token({"GITHUB_TOKEN": "a", "GH_TOKEN": "b"}, lambda: "c") == "a"
    assert pe.resolve_token({"GH_TOKEN": "b"}, lambda: "c") == "b"
    assert pe.resolve_token({}, lambda: "c") == "c"
    assert pe.resolve_token({}, lambda: None) is None


def test_run_id_resolves_under_runs(run_dir: Path) -> None:
    assert pe.resolve_run_dir(run_dir.name, run_dir.parent) == run_dir
    with pytest.raises(pe.PostError):
        pe.resolve_run_dir("nope", run_dir.parent)


# --- Rendering ---------------------------------------------------------------------------------


def test_literal_outruns_backticks_and_fences_multiline_text() -> None:
    assert pe.literal("a `b` c") == "``a `b` c``"
    assert pe.literal("``x") == "``` ``x ```"
    fenced = pe.literal("line one\n````\nline two")
    assert "`````text\n" in fenced
    assert fenced.rstrip().endswith("`````")


def test_literal_strips_comment_markers_until_none_remain() -> None:
    hostile = "<!<!---->-- bajutsu-evidence:local --->>"
    out = pe.literal(hostile)
    assert "<!--" not in out
    assert "-->" not in out


def test_evidence_url_percent_encodes_each_segment() -> None:
    url = pe.evidence_url("https://cdn.test/runs/", "02-a b/#1?/after.png")
    assert url == "https://cdn.test/runs/02-a%20b/%231%3F/after.png"


def test_render_lists_failures_and_caps_them(run_dir: Path) -> None:
    run = pe.load_run(run_dir)
    body = pe.render(run, key="local", links={}, max_failures=0)
    assert body.splitlines()[0] == "<!-- bajutsu-evidence:local -->"
    assert "1 passed, 1 failed, 2 scenarios." in body
    assert "1 more failed scenarios are not shown." in body
    shown = pe.render(run, key="local", links={}, max_failures=10)
    assert "`log in`" in shown
    assert "`step 2 (tap)`" in shown
    assert "`no match: id='login'`" in shown
    assert "`02-login/tap/after.png`" in shown


def test_no_manifest_reads_each_result_json(tmp_path: Path) -> None:
    run = tmp_path / "20261125-000000"
    for sid, ok, prefix in (("01-a", True, ""), ("02-b", False, ""), ("03-c", False, "webkit/")):
        d = run / prefix / sid
        d.mkdir(parents=True)
        result = {"schemaVersion": 18, "scenario": _scenario(sid, ok, sid)}
        (d / "result.json").write_text(json.dumps(result), encoding="utf-8")
    view = pe.load_run(run)
    assert (view.ok, view.passed, view.failed, view.from_manifest) == (False, 1, 2, False)
    webkit = next(f for f in view.failures if f.name == "03-c")
    assert webkit.screenshots == ("webkit/03-c/tap/after.png",)
    assert "no `manifest.json`" in pe.render(view, key="k", links={}, max_failures=5)


def test_empty_run_dir_is_a_post_failure(github: FakeGitHub, tmp_path: Path) -> None:
    code, _, err = _run(github, [str(tmp_path), "--to", TO, "--yes"])
    assert code == pe.EXIT_POST_FAILED
    assert "no manifest.json" in err


# --- The sticky comment ------------------------------------------------------------------------


def test_rerun_edits_the_same_comment(github: FakeGitHub, run_dir: Path) -> None:
    before = _tree_digest(run_dir)
    first = _run(github, [str(run_dir), "--to", TO, "--yes"])
    second = _run(github, [str(run_dir), "--to", TO, "--yes"])
    assert first[0] == second[0] == pe.EXIT_OK
    assert len(github.comments) == 1
    assert [m for m, _ in github.writes()] == ["POST", "PATCH"]
    assert "Posted: https://github.test/" in second[1]
    assert _tree_digest(run_dir) == before


def test_a_failed_run_still_exits_zero(github: FakeGitHub, run_dir: Path) -> None:
    code, _, _ = _run(github, [str(run_dir), "--to", TO, "--yes"])
    assert code == pe.EXIT_OK
    assert "### Bajutsu run `20261125-101530`: failed" in github.comments[0]["body"]


def test_marker_matches_after_crlf_and_trailing_space(github: FakeGitHub, run_dir: Path) -> None:
    github.comments.append(
        {"id": 1, "body": "<!-- bajutsu-evidence:local --> \r\nold", "user": {"login": "dev"}}
    )
    _run(github, [str(run_dir), "--to", TO, "--yes"])
    assert len(github.comments) == 1
    assert github.comments[0]["body"].startswith("<!-- bajutsu-evidence:local -->\n")


def test_keys_and_authors_stay_separate(github: FakeGitHub, run_dir: Path) -> None:
    github.comments.append(
        {"id": 1, "body": "<!-- bajutsu-evidence:local -->\nnot mine", "user": {"login": "other"}}
    )
    github.comments.append(
        {"id": 2, "body": "<!-- bajutsu-evidence:ci -->\nci", "user": {"login": "dev"}}
    )
    _run(github, [str(run_dir), "--to", TO, "--yes"])
    assert [c["body"].split("\n")[1] for c in github.comments[:2]] == ["not mine", "ci"]
    assert len(github.comments) == 3


def test_finds_its_comment_past_the_first_page(github: FakeGitHub, run_dir: Path) -> None:
    for i in range(150):
        github.comments.append({"id": i + 1, "body": f"chatter {i}", "user": {"login": "x"}})
    github.comments.append(
        {"id": 999, "body": "<!-- bajutsu-evidence:local -->\nold", "user": {"login": "dev"}}
    )
    _run(github, [str(run_dir), "--to", TO, "--yes"])
    assert len(github.comments) == 151
    assert github.writes()[0] == ("PATCH", f"/repos/{OWNER}/{REPO}/issues/comments/999")


def test_author_flag_covers_a_token_with_no_user(github: FakeGitHub, run_dir: Path) -> None:
    github.login = None
    code, _, err = _run(github, [str(run_dir), "--to", TO, "--yes"])
    assert code == pe.EXIT_POST_FAILED
    assert "--author" in err
    code, _, _ = _run(github, [str(run_dir), "--to", TO, "--yes", "--author", "app[bot]"])
    assert code == pe.EXIT_OK


def test_a_failed_write_exits_non_zero(github: FakeGitHub, run_dir: Path) -> None:
    github.fail_writes = True
    code, _, err = _run(github, [str(run_dir), "--to", TO, "--yes"])
    assert code == pe.EXIT_POST_FAILED
    assert "HTTP 500" in err


def test_no_token_is_a_post_failure(github: FakeGitHub, run_dir: Path) -> None:
    code, _, err = _run(github, [str(run_dir), "--to", TO, "--yes"], token=None)
    assert code == pe.EXIT_POST_FAILED
    assert "GITHUB_TOKEN" in err


# --- Confirmation and dry run ------------------------------------------------------------------


def test_dry_run_writes_nothing(github: FakeGitHub, run_dir: Path) -> None:
    code, out, _ = _run(github, [str(run_dir), "--to", TO, "--sink", "release", "--dry-run"])
    assert code == pe.EXIT_OK
    assert github.writes() == []
    assert "repository visibility: private" in out
    assert "<!-- bajutsu-evidence:local -->" in out


def test_dry_run_works_offline_without_a_token(github: FakeGitHub, run_dir: Path) -> None:
    args = [str(run_dir), "--to", TO, "--sink", "release", "--dry-run"]
    code, out, _ = _run(github, args, token=None)
    assert code == pe.EXIT_OK
    assert github.requests == []
    assert "Remote state not read" in out
    assert "none found" not in out


def test_non_interactive_stdin_without_yes_refuses(github: FakeGitHub, run_dir: Path) -> None:
    code, out, _ = _run(github, [str(run_dir), "--to", TO])
    assert code == pe.EXIT_POST_FAILED
    assert "--yes" in out
    assert github.writes() == []


def test_interactive_yes_posts(github: FakeGitHub, run_dir: Path) -> None:
    code, _, _ = _run(github, [str(run_dir), "--to", TO], tty=True)
    assert code == pe.EXIT_OK
    assert len(github.comments) == 1


# --- Sinks -------------------------------------------------------------------------------------


def test_none_sink_links_through_the_base_url(github: FakeGitHub, run_dir: Path) -> None:
    args = [str(run_dir), "--to", TO, "--yes", "--evidence-base-url", "https://cdn.test/r/"]
    _run(github, args)
    body = github.comments[0]["body"]
    assert "![failing step](https://cdn.test/r/02-login/tap/after.png)" in body
    assert "[Video](https://cdn.test/r/02-login/video.mp4)" in body
    assert all("/releases" not in p for _, p in github.writes())


def test_release_sink_creates_a_draft_prerelease(github: FakeGitHub, run_dir: Path) -> None:
    before = _tree_digest(run_dir)
    code, _, _ = _run(github, [str(run_dir), "--to", TO, "--sink", "release", "--yes"])
    assert code == pe.EXIT_OK
    (release,) = github.releases
    assert release["tag_name"] == "bajutsu-evidence-7"
    assert (release["draft"], release["prerelease"], release["make_latest"]) == (
        True,
        True,
        "false",
    )
    names = sorted(a["name"] for a in release["assets"])
    assert names == [
        "20261125-101530-02-login-tap-after.png",
        "20261125-101530-02-login-video.mp4",
        "20261125-101530-20261125-101530.zip",
    ]
    body = github.comments[0]["body"]
    assert "![failing step](https://github.test/dl/20261125-101530-02-login-tap-after.png)" in body
    assert "[Whole run](https://github.test/dl/20261125-101530-20261125-101530.zip)" in body
    assert _tree_digest(run_dir) == before


def test_release_sink_truncates_device_log_in_the_zip_only(
    github: FakeGitHub, run_dir: Path
) -> None:
    args = [str(run_dir), "--to", TO, "--sink", "release", "--yes", "--log-tail-kb", "1"]
    _run(github, args)
    zipped = next(data for name, data in github.blobs.items() if name.endswith(".zip"))
    with zipfile.ZipFile(io.BytesIO(zipped)) as z:
        assert len(z.read("02-login/tap/device.log")) == 1024
    assert (run_dir / "02-login" / "tap" / "device.log").stat().st_size == 4096
    assert "truncated to its last 1 KB" in github.comments[0]["body"]


def test_size_guard_skips_the_zip_then_oversize_files(github: FakeGitHub, run_dir: Path) -> None:
    (run_dir / "02-login" / "video.mp4").write_bytes(b"V" * (2 * 1024 * 1024))
    args = [str(run_dir), "--to", TO, "--sink", "release", "--yes", "--size-warn-mb", "1"]
    code, out, _ = _run(github, args)
    assert code == pe.EXIT_OK
    names = [a["name"] for a in github.releases[0]["assets"]]
    assert names == ["20261125-101530-02-login-tap-after.png"]
    assert "20261125-101530.zip` skipped" in out
    assert "02-login/video.mp4` skipped" in github.comments[0]["body"]


def test_rerun_reuses_release_and_existing_assets(github: FakeGitHub, run_dir: Path) -> None:
    args = [str(run_dir), "--to", TO, "--sink", "release", "--yes"]
    _run(github, args)
    uploads = len([w for w in github.writes() if w[1].startswith("/upload/")])
    code, out, _ = _run(github, args)
    assert code == pe.EXIT_OK
    assert len(github.releases) == 1
    assert len([w for w in github.writes() if w[1].startswith("/upload/")]) == uploads
    assert "Release: reuse" in out
    assert "(draft)" in out


def test_publish_lists_existing_assets_and_tag_workflows(github: FakeGitHub, run_dir: Path) -> None:
    github.workflows = {
        "release.yml": "on:\n  release:\n    types: [published]\n",
        "tags.yml": "on:\n  push:\n    tags: ['v*']\n",
        "ci.yml": "on: [pull_request]\n",
        "push.yml": "on: [push]\n",
    }
    _run(github, [str(run_dir), "--to", TO, "--sink", "release", "--yes"])
    args = [str(run_dir), "--to", TO, "--sink", "release", "--publish", "--yes"]
    code, out, _ = _run(github, args)
    assert code == pe.EXIT_OK
    assert github.releases[0]["draft"] is False
    assert "existing assets public too: 20261125-101530-02-login-tap-after.png" in out
    assert "release.yml" in out
    assert "tags.yml" in out
    assert "ci.yml" not in out
    assert "push.yml" in out


# --- Review follow-ups -------------------------------------------------------------------------


def test_a_passing_run_renders_passed_and_no_failure_block(
    github: FakeGitHub, tmp_path: Path
) -> None:
    run = tmp_path / "20261125-000001"
    run.mkdir()
    manifest = {"runId": run.name, "ok": True, "scenarios": [_scenario("home", True, "01-home")]}
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert _run(github, [str(run), "--to", TO, "--yes"])[0] == pe.EXIT_OK
    body = github.comments[0]["body"]
    assert ": passed" in body
    assert "1 passed, 0 failed, 1 scenarios." in body
    assert "####" not in body


@pytest.mark.parametrize(
    "manifest",
    [{"runId": "x", "scenarios": []}, {"runId": "x", "ok": True}, {"ok": "yes", "scenarios": []}],
)
def test_a_manifest_without_a_verdict_is_refused(
    github: FakeGitHub, tmp_path: Path, manifest: dict[str, Any]
) -> None:
    run = tmp_path / "r"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    code, _, err = _run(github, [str(run), "--to", TO, "--yes"])
    assert code == pe.EXIT_POST_FAILED
    assert "`ok`" in err
    assert github.writes() == []


def test_artifact_paths_cannot_leave_the_run_directory(github: FakeGitHub, run_dir: Path) -> None:
    (run_dir.parent / "outside.png").write_bytes(b"secret")
    manifest = json.loads((run_dir / "manifest.json").read_text())
    shots = manifest["scenarios"][1]["steps"][1]["artifacts"]
    shots.append({"name": "../outside.png", "kind": "screenshot", "provider": "x"})
    shots.append(
        {"name": str(run_dir.parent / "outside.png"), "kind": "screenshot", "provider": "x"}
    )
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    code, out, _ = _run(github, [str(run_dir), "--to", TO, "--sink", "release", "--yes"])
    assert code == pe.EXIT_OK
    assert all("outside" not in a["name"] for a in github.releases[0]["assets"])
    assert all(b"secret" not in blob for blob in github.blobs.values())
    assert out.count("leaves the run directory") == 2


def test_a_missing_evidence_file_is_noted_and_unlinked(github: FakeGitHub, run_dir: Path) -> None:
    (run_dir / "02-login" / "video.mp4").unlink()
    code, _, _ = _run(github, [str(run_dir), "--to", TO, "--sink", "release", "--yes"])
    assert code == pe.EXIT_OK
    body = github.comments[0]["body"]
    assert "is named by the run but missing" in body
    assert "- Video `02-login/video.mp4`" in body
    assert all(not a["name"].endswith("video.mp4") for a in github.releases[0]["assets"])


def test_a_truncated_result_json_is_skipped_and_noted(github: FakeGitHub, tmp_path: Path) -> None:
    run = tmp_path / "20261125-000002"
    good = run / "01-a"
    good.mkdir(parents=True)
    result = {"schemaVersion": 18, "scenario": _scenario("a", False, "01-a")}
    (good / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (run / "02-b").mkdir()
    (run / "02-b" / "result.json").write_text('{"scenario": {"scen', encoding="utf-8")
    code, _, _ = _run(github, [str(run), "--to", TO, "--yes"])
    assert code == pe.EXIT_OK
    body = github.comments[0]["body"]
    assert "0 passed, 1 failed" in body
    assert "02-b/result.json" in body
    assert "skipped" in body
    assert str(tmp_path) not in body


@pytest.mark.parametrize("answer", ["\n", "n\n", ""])
def test_declining_the_prompt_posts_nothing(github: FakeGitHub, run_dir: Path, answer: str) -> None:
    out, err = io.StringIO(), io.StringIO()
    stdin = io.StringIO(answer)
    stdin.isatty = lambda: True  # type: ignore[method-assign]
    env = {"GITHUB_API_URL": github.base, "GITHUB_TOKEN": "t"}
    code = pe.main([str(run_dir), "--to", TO], pe.Io(env, stdin, out, err))
    assert code == pe.EXIT_POST_FAILED
    assert "not confirmed" in err.getvalue()
    assert github.writes() == []


def test_a_stuck_asset_is_not_reused(github: FakeGitHub, run_dir: Path) -> None:
    args = [str(run_dir), "--to", TO, "--sink", "release", "--yes"]
    _run(github, args)
    github.releases[0]["assets"][0]["state"] = "starter"
    code, _, err = _run(github, args)
    assert code == pe.EXIT_POST_FAILED
    assert "stuck" in err


def test_a_reply_that_is_not_a_list_stops_the_search(github: FakeGitHub, run_dir: Path) -> None:
    github.comments_reply = {"message": "odd"}
    code, _, err = _run(github, [str(run_dir), "--to", TO, "--yes"])
    assert code == pe.EXIT_POST_FAILED
    assert "not a list" in err
    assert github.writes() == []


def test_login_hint_only_for_a_token_without_a_user(github: FakeGitHub, run_dir: Path) -> None:
    github.login = None
    assert "--author" in _run(github, [str(run_dir), "--to", TO, "--yes"])[2]
    code, _, err = _run(github, [str(run_dir), "--to", TO, "--yes"], token="")
    assert code == pe.EXIT_POST_FAILED
    assert "GITHUB_TOKEN" in err


def test_publish_without_workflows_says_none_found(github: FakeGitHub, run_dir: Path) -> None:
    args = [str(run_dir), "--to", TO, "--sink", "release", "--publish", "--yes"]
    _, out, _ = _run(github, args)
    assert "none found" in out


def test_a_url_on_another_host_is_refused_before_any_request(
    github: FakeGitHub, run_dir: Path
) -> None:
    other = "https://ghe.corp.example/acme/app/pull/7"
    code, _, err = _run(github, [str(run_dir), "--to", other, "--yes"])
    assert code == pe.EXIT_POST_FAILED
    assert "ghe.corp.example" in err
    assert github.requests == []


def test_a_url_on_the_api_hosts_web_side_is_accepted() -> None:
    target = pe.parse_target("https://ghe.corp.example/acme/app/pull/7")
    pe.check_host(target, "https://ghe.corp.example/api/v3")
    pe.check_host(pe.parse_target("https://github.com/acme/app/issues/7"), "https://api.github.com")
    pe.check_host(pe.parse_target("https://acme.ghe.com/o/r/pull/1"), "https://api.acme.ghe.com")
    with pytest.raises(pe.PostError):
        pe.check_host(target, "https://api.github.com")


def test_the_token_is_not_forwarded_on_a_redirect(github: FakeGitHub, run_dir: Path) -> None:
    seen: list[str | None] = []

    class Sink(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"[]")

        def log_message(self, *_: object) -> None:
            pass

    other = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        gh = pe.GitHub("t0ken", github.base)
        github.redirect_to = f"http://127.0.0.1:{other.server_address[1]}/elsewhere"
        gh.request("GET", "/user")
    finally:
        other.shutdown()
        other.server_close()
    assert seen == [None]
