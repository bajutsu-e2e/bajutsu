"""Tests for ``scripts/post_run_url.py`` (BE-0508), run against a fake GitHub HTTP server.

Fixtures come from Bajutsu's own manifest writer, so a manifest schema change breaks these tests in
the PR that makes it instead of silently emptying the comment. No test reaches the network.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from bajutsu.common.orchestrator import RunResult, StepOutcome
from bajutsu.common.report import manifest_dict, scenario_result_dict
from scripts import post_run_url as pru

RUN_ID = "20261125-120000"
URL = f"https://serve.example.test/runs/{RUN_ID}/report.html"


def _failing(name: str = "login", sid: str = "00-login", reason: str = "no such id") -> RunResult:
    return RunResult(
        scenario=name,
        ok=False,
        failure=reason,
        sid=sid,
        steps=[
            StepOutcome(index=0, action="tap", ok=True),
            StepOutcome(index=1, action="tap", ok=False, reason=reason),
        ],
    )


def _passing(sid: str = "01-ok") -> RunResult:
    return RunResult(
        scenario="ok", ok=True, sid=sid, steps=[StepOutcome(index=0, action="tap", ok=True)]
    )


def _make_run(root: Path, results: list[RunResult], *, manifest: bool = True) -> Path:
    run = root / RUN_ID
    run.mkdir(parents=True)
    for r in results:
        (run / r.sid).mkdir()
        (run / r.sid / "result.json").write_text(json.dumps(scenario_result_dict(r)))
    if manifest:
        (run / "manifest.json").write_text(json.dumps(manifest_dict(RUN_ID, results)))
    return run


# ---------------------------------------------------------------------------------------------
# The fake GitHub server


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class FakeGitHub:
    """In-memory comments and releases behind the REST paths the script uses."""

    login: str = "me"
    comments: list[dict[str, Any]] = field(default_factory=list)
    releases: list[dict[str, Any]] = field(default_factory=list)
    requests: list[Recorded] = field(default_factory=list)
    redirect_user_to: str | None = None
    fail_post: bool = False

    def handle(self, method: str, path: str, body: Any) -> tuple[int, Any, dict[str, str]]:
        route, _, query = path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        page, per_page = int(params.get("page", "1")), int(params.get("per_page", "30"))
        window = slice((page - 1) * per_page, page * per_page)
        if route == "/user":
            if self.redirect_user_to:
                return 302, None, {"Location": self.redirect_user_to}
            return 200, {"login": self.login}, {}
        if route.endswith("/releases") and method == "GET":
            return 200, self.releases[window], {}
        if "/releases/" in route and method == "PATCH":
            return self._edit(self.releases, int(route.rsplit("/", 1)[1]), body, "r")
        if route.endswith("/comments") and method == "GET":
            if "/issues/404/" in route:
                return 404, {"message": "Not Found"}, {}
            return 200, self.comments[window], {}
        if "/issues/comments/" in route and method == "PATCH":
            return self._edit(self.comments, int(route.rsplit("/", 1)[1]), body, "c")
        if route.endswith("/comments") and method == "POST":
            return self._create(body)
        return 404, {"message": f"no route {method} {route}"}, {}

    @staticmethod
    def _edit(
        items: list[dict[str, Any]], item_id: int, body: Any, kind: str
    ) -> tuple[int, Any, dict[str, str]]:
        for item in items:
            if item["id"] == item_id:
                item["body"] = body["body"]
                return 200, {**item, "html_url": f"https://github.com/{kind}/{item_id}"}, {}
        return 404, {"message": "Not Found"}, {}

    def _create(self, body: Any) -> tuple[int, Any, dict[str, str]]:
        if self.fail_post:
            return 403, {"message": "Resource not accessible"}, {}
        new = {"id": 1000 + len(self.comments), "body": body["body"], "user": {"login": self.login}}
        self.comments.append(new)
        return 201, {**new, "html_url": f"https://github.com/c/{new['id']}"}, {}


def _serve(handler_state: Any) -> tuple[ThreadingHTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def _do(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else None
            handler_state.requests.append(
                Recorded(self.command, self.path, dict(self.headers.items()), body)
            )
            status, payload, extra = handler_state.handle(self.command, self.path, body)
            data = json.dumps(payload).encode()
            self.send_response(status)
            for k, v in extra.items():
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self._do()

        def do_POST(self) -> None:
            self._do()

        def do_PATCH(self) -> None:
            self._do()

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGitHub]:
    fake = FakeGitHub()
    server, base = _serve(fake)
    monkeypatch.setattr(pru, "API_BASE", base)
    monkeypatch.setenv("GH_TOKEN", "tok-gh")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    yield fake
    server.shutdown()


def _main(run: Path, *extra: str, to: str = "acme/app#7", url: str = URL) -> int:
    return pru.main([str(run), "--url", url, "--to", to, "--yes", *extra])


# ---------------------------------------------------------------------------------------------
# The command


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("acme/app#7", pru.Target("acme", "app", number=7)),
        ("https://github.com/acme/app/pull/7", pru.Target("acme", "app", number=7)),
        (
            "https://github.com/acme/app/issues/7#issuecomment-1",
            pru.Target("acme", "app", number=7),
        ),
        ("https://github.com/acme/app/pull/7/files", pru.Target("acme", "app", number=7)),
        ("release:acme/app@v1.2.0", pru.Target("acme", "app", tag="v1.2.0")),
    ],
)
def test_parse_target_forms(value: str, expected: pru.Target) -> None:
    assert pru.parse_target(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "#7",
        "7",
        "https://evil.example/acme/app/pull/7",
        "http://github.com/acme/app/pull/7",
        "https://github.com.evil.example/acme/app/pull/7",
        "acme/app#0",
        "acme#7",
        "release:acme/app@",
        "acme/..#7",
        "https://github.com/acme/./pull/7",
        "release:acme/..@v1",
    ],
)
def test_parse_target_refusals(value: str) -> None:
    with pytest.raises(pru.UsageError):
        pru.parse_target(value)


def test_bare_number_names_the_fork_reason() -> None:
    with pytest.raises(pru.UsageError, match="owner/repo#NUMBER"):
        pru.parse_target("#12")


@pytest.mark.parametrize(
    "url",
    [
        "https://serve.example.test/runs/20990101-000000/report.html",  # another run
        f"ftp://serve.example.test/runs/{RUN_ID}/report.html",
        f"https://serve.example.test/runs/{RUN_ID}/report.html)x",
        f"https://serve.example.test/runs/{RUN_ID}/a b.html",
        f"https://serve.example.test/runs/{RUN_ID}/<x>",
        f"https://user:pw@serve.example.test/runs/{RUN_ID}/report.html",
        f"https://serve.example.test/runs/{RUN_ID}/report.html?token=abc",
        f"https://serve.example.test/runs/{RUN_ID}/report.html?",
        f"https://serve.example.test/runs/{RUN_ID}",  # no trailing slash after the id
    ],
)
def test_run_url_refusals(url: str) -> None:
    with pytest.raises(pru.UsageError):
        pru.check_run_url(url, RUN_ID)


def test_run_url_accepts_this_run() -> None:
    assert pru.check_run_url(URL, RUN_ID) == URL


def test_a_refused_argument_exits_2_and_never_reads_the_token(
    tmp_path: Path, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _make_run(tmp_path, [_passing()])

    def boom() -> str:
        raise AssertionError("token read")

    monkeypatch.setattr(pru, "read_token", boom)
    assert _main(run, to="https://evil.example/acme/app/pull/7") == 2
    assert _main(run, url="https://serve.example.test/runs/other/") == 2
    assert _main(run, "--marker", "bajutsu_abc") == 2
    assert _main(run, "--max-failures", "-1") == 2
    assert _main(run, "--archive-url", "https://s3.example/x.zip?X-Amz-Signature=1") == 2
    assert github.requests == []


def test_run_id_is_looked_up_under_runs(tmp_path: Path) -> None:
    _make_run(tmp_path, [_passing()])
    assert pru.resolve_run_dir(RUN_ID, tmp_path) == tmp_path / RUN_ID
    for bad in ("../no-such-run-dir", "missing"):
        with pytest.raises(pru.UsageError):
            pru.resolve_run_dir(bad, tmp_path)


def test_reads_result_json_when_no_manifest(tmp_path: Path) -> None:
    run = pru.read_run(_make_run(tmp_path, [_failing(), _passing()], manifest=False))
    assert run.verdict == "incomplete"
    assert [s.name for s in run.failed] == ["login"]
    assert "did not complete" in pru.render(run, URL)


def test_a_directory_with_no_results_is_refused(tmp_path: Path) -> None:
    (tmp_path / RUN_ID).mkdir()
    with pytest.raises(pru.UsageError, match=r"no manifest\.json"):
        pru.read_run(tmp_path / RUN_ID)


# ---------------------------------------------------------------------------------------------
# The renderer


def test_render_verdict_failures_and_link(tmp_path: Path) -> None:
    run = pru.read_run(_make_run(tmp_path, [_failing(), _passing()]))
    body = pru.render_comment(run, URL, "local", archive_url="https://serve.example.test/z.zip")
    assert body.startswith("<!-- bajutsu-run:local -->\n")
    assert "### Bajutsu run failed: 1 passed, 1 failed" in body
    assert "#### `login`" in body
    assert "Failed at step `1` `tap`: `no such id`" in body
    assert f"Run: [`{RUN_ID}`]({URL})" in body
    assert "[the run as a zip](https://serve.example.test/z.zip)" in body


def test_render_passed(tmp_path: Path) -> None:
    run = pru.read_run(_make_run(tmp_path, [_passing()]))
    assert "### Bajutsu run passed: 1 passed, 0 failed" in pru.render(run, URL)


def test_render_states_omitted_failures(tmp_path: Path) -> None:
    results = [_failing(name=f"s{i}", sid=f"{i:02d}-s") for i in range(5)]
    run = pru.read_run(_make_run(tmp_path, results))
    body = pru.render(run, URL, max_failures=2)
    assert body.count("#### ") == 2
    assert "…and 3 more failed scenario(s) not listed." in body


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("plain", "`plain`"),
        ("a `tick` b", "``a `tick` b``"),
        ("`edge", "`` `edge ``"),
        ("edge``", "``` edge`` ```"),
        ("", "`(empty)`"),
    ],
)
def test_code_span_delimiters_and_padding(text: str, expected: str) -> None:
    assert pru.code(text) == expected


def test_multiline_text_goes_in_a_longer_fence() -> None:
    out = pru.code("line1\n````\nline3")
    assert out == "\n`````\nline1\n````\nline3\n`````\n"


def test_comment_delimiters_are_stripped_until_none_remain() -> None:
    # Removing one delimiter joins its neighbours into another; a single pass would leave it.
    for text in ("<!<!---- bajutsu-run:x -->->", "<<!--!-- x --->>", "--<!---->>"):
        out = pru.code(text)
        assert "<!--" not in out and "-->" not in out


def test_app_text_cannot_mention_or_forge_a_marker(tmp_path: Path) -> None:
    reason = "@everyone <img src=x> <!-- bajutsu-run:local -->"
    run = pru.read_run(_make_run(tmp_path, [_failing(reason=reason)]))
    body = pru.render_comment(run, URL, "local")
    assert body.count("bajutsu-run:local -->") == 1
    assert "`@everyone <img src=x>  bajutsu-run:local `" in body


def test_long_text_is_cut_with_an_ellipsis() -> None:
    out = pru.code("x" * 600)
    assert out == "`" + "x" * 500 + "…`"


def test_body_is_cut_by_whole_blocks_below_the_limit(tmp_path: Path) -> None:
    results = [
        _failing(name=f"s{i}" + "n" * 400, sid=f"{i:02d}-s", reason="r" * 450) for i in range(40)
    ]
    run = pru.read_run(_make_run(tmp_path, results))
    body = pru.render(run, URL, max_failures=40, limit=5_000)
    assert len(body) < 5_000
    assert "not listed. The text was cut to fit GitHub's size limit." in body
    # Every listed block is whole: its reason span closes.
    assert body.count("#### ") == body.count("r" * 450 + "`")


def test_default_limits_match_github() -> None:
    assert pru.MAX_COMMENT_CHARS < 65_536
    assert pru.MAX_RELEASE_CHARS < 125_000


@pytest.mark.parametrize("name", ["local", "ci.job-1_a"])
def test_marker_accepts(name: str) -> None:
    assert pru.check_marker(name) == name


@pytest.mark.parametrize(
    "name", ["", "a b", "x-->", "a\nb", "bajutsu_k3y", "BAJUTSU_x", "é", "x" * 65]
)
def test_marker_refuses(name: str) -> None:
    with pytest.raises(pru.UsageError):
        pru.check_marker(name)


def test_marker_line_ignores_trailing_space_and_cr() -> None:
    assert pru.is_marker_line("<!-- bajutsu-run:local -->  \r", "local")
    assert not pru.is_marker_line("<!-- bajutsu-run:local2 -->", "local")


# ---------------------------------------------------------------------------------------------
# The GitHub client: the sticky comment


def test_creates_then_edits_the_same_comment(tmp_path: Path, github: FakeGitHub) -> None:
    run = _make_run(tmp_path, [_failing()])
    assert _main(run) == 0
    assert len(github.comments) == 1
    first_id = github.comments[0]["id"]
    assert _main(run) == 0
    assert len(github.comments) == 1
    assert github.comments[0]["id"] == first_id
    assert [(r.method, r.path.split("?")[0]) for r in github.requests][-1] == (
        "PATCH",
        f"/repos/acme/app/issues/comments/{first_id}",
    )


def test_matches_a_marker_across_a_carriage_return(tmp_path: Path, github: FakeGitHub) -> None:
    github.comments.append(
        {"id": 5, "body": "<!-- bajutsu-run:local -->\r\nold", "user": {"login": "me"}}
    )
    assert _main(_make_run(tmp_path, [_passing()])) == 0
    assert len(github.comments) == 1
    assert github.comments[0]["body"].startswith(
        "<!-- bajutsu-run:local -->\n### Bajutsu run passed"
    )


def test_ignores_another_users_comment_and_another_marker(
    tmp_path: Path, github: FakeGitHub
) -> None:
    github.comments += [
        {"id": 5, "body": "<!-- bajutsu-run:local -->\nx", "user": {"login": "someone"}},
        {"id": 6, "body": "<!-- bajutsu-run:other -->\nx", "user": {"login": "me"}},
    ]
    assert _main(_make_run(tmp_path, [_passing()])) == 0
    assert len(github.comments) == 3


def test_pages_through_comments(tmp_path: Path, github: FakeGitHub) -> None:
    github.comments += [{"id": i, "body": "noise", "user": {"login": "me"}} for i in range(150)]
    github.comments.append(
        {"id": 999, "body": "<!-- bajutsu-run:local -->\nold", "user": {"login": "me"}}
    )
    assert _main(_make_run(tmp_path, [_passing()])) == 0
    assert len(github.comments) == 151
    assert github.comments[-1]["body"] != "<!-- bajutsu-run:local -->\nold"


def test_author_flag_skips_the_user_lookup(tmp_path: Path, github: FakeGitHub) -> None:
    github.login = "app[bot]"
    assert _main(_make_run(tmp_path, [_passing()]), "--author", "app[bot]") == 0
    assert all(r.path != "/user" for r in github.requests)


def test_a_missing_thread_exits_1(tmp_path: Path, github: FakeGitHub) -> None:
    assert _main(_make_run(tmp_path, [_passing()]), to="acme/app#404") == 1


def test_a_write_refused_by_github_exits_1(tmp_path: Path, github: FakeGitHub) -> None:
    github.fail_post = True
    assert _main(_make_run(tmp_path, [_passing()])) == 1


def test_exit_status_never_reflects_the_verdict(tmp_path: Path, github: FakeGitHub) -> None:
    assert _main(_make_run(tmp_path, [_failing()])) == 0


# ---------------------------------------------------------------------------------------------
# The GitHub client: the release block


def _release(
    body: str | None, tag: str = "v1", rid: int = 1, draft: bool = False
) -> dict[str, Any]:
    return {"id": rid, "tag_name": tag, "body": body, "draft": draft}


def test_appends_then_replaces_the_release_block(tmp_path: Path, github: FakeGitHub) -> None:
    github.releases.append(_release("Release notes.", draft=True))
    run = _make_run(tmp_path, [_failing()])
    assert _main(run, to="release:acme/app@v1") == 0
    notes = github.releases[0]["body"]
    assert notes.startswith("Release notes.\n\n<!-- bajutsu-run:local -->\n### Bajutsu run failed")
    assert notes.rstrip().endswith("<!-- /bajutsu-run:local -->")
    assert _main(run, to="release:acme/app@v1") == 0
    assert github.releases[0]["body"] == notes


def test_replaces_only_the_block_and_keeps_the_rest() -> None:
    existing = "Top\n<!-- bajutsu-run:local -->\nold\n<!-- /bajutsu-run:local -->\r\nBottom\n"
    out = pru.release_body(existing, "new\n", "local")
    assert out == "Top\n<!-- bajutsu-run:local -->\nnew\n<!-- /bajutsu-run:local -->\r\nBottom\n"


def test_empty_notes_get_the_block_alone() -> None:
    assert pru.release_body("", "t", "local") == (
        "<!-- bajutsu-run:local -->\nt\n<!-- /bajutsu-run:local -->\n"
    )


def test_an_unterminated_block_is_an_error() -> None:
    with pytest.raises(pru.PostError, match="without"):
        pru.release_body("<!-- bajutsu-run:local -->\nold\n", "t", "local")


def test_release_matches_the_tag_exactly(tmp_path: Path, github: FakeGitHub) -> None:
    github.releases += [_release("a", tag="v1.0", rid=1), _release("b", tag="v1", rid=2)]
    assert _main(_make_run(tmp_path, [_passing()]), to="release:acme/app@v1") == 0
    assert github.releases[0]["body"] == "a"
    assert "bajutsu-run:local" in github.releases[1]["body"]


def test_a_missing_release_is_an_error(tmp_path: Path, github: FakeGitHub) -> None:
    assert _main(_make_run(tmp_path, [_passing()]), to="release:acme/app@nope") == 1
    assert all(r.method == "GET" for r in github.requests)


def test_release_room_counts_the_other_notes() -> None:
    assert pru.release_text_limit("x" * 100, "local") == pru.MAX_RELEASE_CHARS - 100
    with pytest.raises(pru.PostError, match="too long"):
        pru.release_text_limit("x" * pru.MAX_RELEASE_CHARS, "local")


# ---------------------------------------------------------------------------------------------
# Credentials


def test_token_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "a")
    monkeypatch.setenv("GITHUB_TOKEN", "b")
    assert pru.read_token() == "a"
    monkeypatch.delenv("GH_TOKEN")
    assert pru.read_token() == "b"
    monkeypatch.delenv("GITHUB_TOKEN")
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="c\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert pru.read_token() == "c"
    assert calls == [["gh", "auth", "token", "--hostname", "github.com"]]


@pytest.mark.parametrize("failure", ["missing", "empty"])
def test_no_token_anywhere_is_an_error(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if failure == "missing":
            raise FileNotFoundError("gh")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="not logged in")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(pru.PostError):
        pru.read_token()


def test_the_token_is_sent_but_never_follows_a_redirect(tmp_path: Path, github: FakeGitHub) -> None:
    other = FakeGitHub(login="elsewhere")
    server, base = _serve(other)
    try:
        github.redirect_user_to = f"{base}/user"
        assert _main(_make_run(tmp_path, [_passing()])) == 0
    finally:
        server.shutdown()
    assert github.requests[0].headers.get("Authorization") == "Bearer tok-gh"
    assert other.requests and "Authorization" not in other.requests[0].headers


# ---------------------------------------------------------------------------------------------
# The plan and the guards


def test_dry_run_prints_the_plan_and_writes_nothing(
    tmp_path: Path, github: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _make_run(tmp_path, [_failing()])
    assert _main(run, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "Target: acme/app#7" in out and "Action: create a comment" in out
    assert "### Bajutsu run failed" in out and "nothing was written" in out
    assert github.comments == []
    assert all(r.method == "GET" for r in github.requests)


def test_dry_run_without_a_token_stays_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_token() -> str:
        raise pru.PostError("none")

    monkeypatch.setattr(pru, "read_token", no_token)
    run = _make_run(tmp_path, [_passing()])
    assert pru.main([str(run), "--url", URL, "--to", "release:acme/app@v1", "--dry-run"]) == 0
    assert "not looked up: no token" in capsys.readouterr().out
    assert pru.main([str(run), "--url", URL, "--to", "acme/app#7", "--dry-run"]) == 0
    assert pru.main([str(run), "--url", URL, "--to", "acme/app#7", "--yes"]) == 1


def test_without_yes_it_asks_and_a_no_writes_nothing(
    tmp_path: Path, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _make_run(tmp_path, [_passing()])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert pru.main([str(run), "--url", URL, "--to", "acme/app#7"]) == 1
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert pru.main([str(run), "--url", URL, "--to", "acme/app#7"]) == 0
    assert len(github.comments) == 1


def test_without_yes_a_non_terminal_is_refused(
    tmp_path: Path, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert (
        pru.main([str(_make_run(tmp_path, [_passing()])), "--url", URL, "--to", "acme/app#7"]) == 1
    )
    assert github.comments == []


# ---------------------------------------------------------------------------------------------
# Failures that must stay loud


@pytest.mark.parametrize(
    "content",
    [
        "{broken",
        "[]",
        '{"ok": "yes", "scenarios": []}',
        '{"ok": true}',
        '{"ok": true, "scenarios": [1]}',
    ],
)
def test_a_broken_manifest_is_refused_not_reported_incomplete(tmp_path: Path, content: str) -> None:
    run = tmp_path / RUN_ID
    run.mkdir()
    (run / "manifest.json").write_text(content)
    with pytest.raises(pru.UsageError, match=r"manifest\.json"):
        pru.read_run(run)


def test_a_broken_result_json_is_counted_and_stated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _make_run(tmp_path, [_failing()], manifest=False)
    (run_dir / "02-bad").mkdir()
    (run_dir / "02-bad" / "result.json").write_text("{half")
    run = pru.read_run(run_dir)
    assert run.unreadable == 1 and len(run.scenarios) == 1
    assert "1 `result.json` file(s) could not be read" in pru.render(run, URL)
    assert "warning:" in capsys.readouterr().err


def test_github_message_is_reported(
    tmp_path: Path, github: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    github.fail_post = True
    assert _main(_make_run(tmp_path, [_passing()])) == 1
    assert "Resource not accessible" in capsys.readouterr().err


def test_gh_stderr_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **_: subprocess.CompletedProcess(argv, 1, stdout="", stderr="not logged in"),
    )
    with pytest.raises(pru.PostError, match="not logged in"):
        pru.read_token()


def test_a_comment_without_a_numeric_id_is_an_error(tmp_path: Path, github: FakeGitHub) -> None:
    github.comments.append(
        {"id": "x", "body": "<!-- bajutsu-run:local -->\n", "user": {"login": "me"}}
    )
    github.comments.append({"id": 3, "body": "<!-- bajutsu-run:local -->\n", "user": "me"})
    assert _main(_make_run(tmp_path, [_passing()])) == 1


def test_run_id_via_runs_dir(tmp_path: Path, github: FakeGitHub) -> None:
    _make_run(tmp_path, [_passing()])
    assert (
        pru.main([RUN_ID, "--runs-dir", str(tmp_path), "--url", URL, "--to", "acme/app#7", "--yes"])
        == 0
    )
    assert len(github.comments) == 1


def test_a_token_with_no_user_points_at_author(
    tmp_path: Path,
    github: FakeGitHub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(github, "redirect_user_to", None)
    original = github.handle

    def handle(method: str, path: str, body: Any) -> tuple[int, Any, dict[str, str]]:
        if path == "/user":
            return 403, {"message": "Resource not accessible by integration"}, {}
        return original(method, path, body)

    monkeypatch.setattr(github, "handle", handle)
    assert _main(_make_run(tmp_path, [_passing()])) == 1
    assert "--author" in capsys.readouterr().err


def test_an_interrupted_prompt_posts_nothing(
    tmp_path: Path, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

    def eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert (
        pru.main([str(_make_run(tmp_path, [_passing()])), "--url", URL, "--to", "acme/app#7"]) == 1
    )
    assert github.comments == []


def test_a_write_with_an_unreadable_reply_still_counts_as_posted(
    tmp_path: Path,
    github: FakeGitHub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    real = urllib.request.urlopen

    def garbled(req: urllib.request.Request, timeout: float) -> Any:
        reply = real(req, timeout=timeout)
        return io.BytesIO(b"<html>") if req.get_method() == "POST" else reply

    monkeypatch.setattr(urllib.request, "urlopen", garbled)
    assert _main(_make_run(tmp_path, [_passing()])) == 0
    assert "Posted." in capsys.readouterr().out
    assert len(github.comments) == 1


def test_a_dropped_connection_names_the_request(
    tmp_path: Path,
    github: FakeGitHub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def reset(req: urllib.request.Request, timeout: float) -> Any:
        raise ConnectionResetError("reset by peer")

    monkeypatch.setattr(urllib.request, "urlopen", reset)
    assert _main(_make_run(tmp_path, [_passing()])) == 1
    err = capsys.readouterr().err
    assert "GET /user: reset by peer" in err and "--author" not in err


def test_the_author_match_ignores_case(tmp_path: Path, github: FakeGitHub) -> None:
    run = _make_run(tmp_path, [_passing()])
    assert _main(run, "--author", "ME") == 0
    assert _main(run, "--author", "Me") == 0
    assert len(github.comments) == 1


def test_not_applicable_runs_are_stated(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    run_dir.mkdir()
    manifest = manifest_dict(RUN_ID, [], not_applicable=[{"scenario": "s", "reason": "no device"}])
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    body = pru.render(pru.read_run(run_dir), URL)
    assert "run failed: 0 passed, 0 failed" in body
    assert "1 run(s) were not applicable" in body
    assert "No scenario failed" in body
